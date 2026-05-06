"""SQLite FTS5 index over parsed Claude Code messages."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .parser import DEFAULT_QUERY_KINDS, ParsedMessage

logger = logging.getLogger(__name__)

# Bump when the schema changes incompatibly. Mismatch triggers a rebuild.
SCHEMA_VERSION = 2

SCHEMA_SQL = [
    # FTS5 virtual table holds the indexable content. UNINDEXED columns are
    # stored alongside but not tokenised.
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
        message_id UNINDEXED,
        session_id UNINDEXED,
        project_path UNINDEXED,
        timestamp UNINDEXED,
        role UNINDEXED,
        kind UNINDEXED,
        content,
        tokenize = 'porter unicode61'
    )
    """,
    # Per-file ingest state for incremental reindexing. Phase 2's watcher
    # reads `last_byte_offset` to know where to resume.
    """
    CREATE TABLE IF NOT EXISTS ingest_state (
        file_path TEXT PRIMARY KEY,
        last_byte_offset INTEGER NOT NULL,
        last_indexed_at TEXT NOT NULL
    )
    """,
    # Dedup index. FTS5 has no UNIQUE constraint and scanning an UNINDEXED
    # column is O(N), so we keep message_ids in a regular table with a
    # PRIMARY KEY for O(log N) dedup checks during ingest.
    """
    CREATE TABLE IF NOT EXISTS seen_messages (
        message_id TEXT PRIMARY KEY
    )
    """,
    # Single-row metadata table. We store the schema version so we can detect
    # incompatible schema changes and rebuild.
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    # Auth tokens issued by this server. Stored hashed so a DB leak
    # can't replay them. `kind` is 'invite' (single-use, expires) or
    # 'bearer' (long-lived, revoked by deletion).
    """
    CREATE TABLE IF NOT EXISTS tokens (
        id TEXT PRIMARY KEY,
        token_hash TEXT UNIQUE NOT NULL,
        kind TEXT NOT NULL,
        label TEXT,
        created_at TEXT NOT NULL,
        last_used_at TEXT,
        expires_at TEXT,
        last_ip TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tokens_hash ON tokens(token_hash)",
]


@dataclass(frozen=True)
class SearchHit:
    message_id: str
    session_id: str
    project_path: str
    timestamp: str
    role: str
    kind: str
    snippet: str
    score: float


class Index:
    """Wraps the SQLite connection and exposes ingest + query operations.

    Use as a context manager so the connection is committed and closed.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # --- lifecycle -----------------------------------------------------

    def open(self) -> None:
        if self._conn is not None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # `check_same_thread=False` is fine for phase 1 (single-threaded)
        # and saves Phase 2 a refactor when we run from a watcher thread.
        # `isolation_level=None` puts sqlite3 in autocommit mode: no
        # implicit BEGIN before DML. The only transactions that exist
        # are the ones we open explicitly via `transaction()`, which
        # avoids "cannot start a transaction within a transaction"
        # when multiple call sites both try to manage their own.
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._ensure_schema()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Index":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Index is not open")
        return self._conn

    # --- schema --------------------------------------------------------

    def _ensure_schema(self) -> None:
        cur = self.conn.cursor()
        # Detect schema version mismatch on a pre-existing DB. If the meta
        # table doesn't exist yet we're either fresh or pre-versioning; treat
        # as fresh and write the version.
        existing = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        if existing is not None:
            row = cur.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            if row is not None and row["value"] != str(SCHEMA_VERSION):
                logger.warning(
                    "schema version mismatch (db=%s, code=%s); rebuilding",
                    row["value"],
                    SCHEMA_VERSION,
                )
                self._drop_all()
        for stmt in SCHEMA_SQL:
            cur.execute(stmt)
        cur.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def _drop_all(self) -> None:
        cur = self.conn.cursor()
        cur.execute("DROP TABLE IF EXISTS messages")
        cur.execute("DROP TABLE IF EXISTS ingest_state")
        cur.execute("DROP TABLE IF EXISTS seen_messages")
        cur.execute("DROP TABLE IF EXISTS meta")
        cur.execute("DROP TABLE IF EXISTS tokens")

    def reset(self) -> None:
        """Drop and recreate all tables. Used by `index --rebuild`."""
        self._drop_all()
        self._ensure_schema()

    # --- ingest --------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # Connection is in autocommit mode (see open()), so we drive
        # BEGIN/COMMIT manually.
        cur = self.conn.cursor()
        cur.execute("BEGIN")
        try:
            yield
        except Exception:
            cur.execute("ROLLBACK")
            raise
        else:
            cur.execute("COMMIT")

    def has_message(self, message_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM seen_messages WHERE message_id = ? LIMIT 1",
            (message_id,),
        ).fetchone()
        return row is not None

    def insert_messages(self, messages: Iterable[ParsedMessage]) -> int:
        """Insert messages, skipping ones whose message_id already exists.

        Returns the number of rows actually inserted. Dedup goes through
        `seen_messages` (PRIMARY KEY indexed) so this stays O(log N) per row.
        """
        cur = self.conn.cursor()
        inserted = 0
        for m in messages:
            cur.execute(
                "INSERT OR IGNORE INTO seen_messages(message_id) VALUES (?)",
                (m.message_id,),
            )
            if cur.rowcount == 0:
                continue
            cur.execute(
                """
                INSERT INTO messages
                    (message_id, session_id, project_path, timestamp, role, kind, content)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    m.message_id,
                    m.session_id,
                    m.project_path,
                    m.timestamp,
                    m.role,
                    m.kind,
                    m.content,
                ),
            )
            inserted += 1
        return inserted

    def get_ingest_offset(self, file_path: Path) -> int:
        row = self.conn.execute(
            "SELECT last_byte_offset FROM ingest_state WHERE file_path = ?",
            (str(file_path),),
        ).fetchone()
        return int(row["last_byte_offset"]) if row else 0

    def set_ingest_offset(self, file_path: Path, offset: int) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            """
            INSERT INTO ingest_state(file_path, last_byte_offset, last_indexed_at)
            VALUES (?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                last_byte_offset = excluded.last_byte_offset,
                last_indexed_at = excluded.last_indexed_at
            """,
            (str(file_path), offset, now),
        )

    # --- query ---------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        kinds: frozenset[str] | set[str] | None = None,
        project_paths: list[str] | tuple[str, ...] | None = None,
    ) -> list[SearchHit]:
        """Run an FTS5 MATCH query, returning hits ordered by bm25 (best first).

        `kinds` is an iterable of allowed `kind` values (see parser module).
        Defaults to `DEFAULT_QUERY_KINDS` (typed user prompts + assistant
        prose). Pass an empty set or `None` to mean "no filter, return all
        kinds".

        `project_paths`, if provided, restricts hits to messages whose
        `project_path` (the cwd Claude Code recorded for the event) is
        in the list. None or empty means no filter.
        """
        if not query or not query.strip():
            return []
        kind_clause = ""
        kind_params: tuple = ()
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            kind_clause = f" AND kind IN ({placeholders})"
            kind_params = tuple(kinds)
        proj_clause = ""
        proj_params: tuple = ()
        if project_paths:
            placeholders = ",".join("?" for _ in project_paths)
            proj_clause = f" AND project_path IN ({placeholders})"
            proj_params = tuple(project_paths)
        sql = f"""
            SELECT
                message_id,
                session_id,
                project_path,
                timestamp,
                role,
                kind,
                snippet(messages, 6, '**', '**', ' … ', 16) AS snippet,
                bm25(messages) AS score
            FROM messages
            WHERE messages MATCH ?{kind_clause}{proj_clause}
            ORDER BY bm25(messages)
            LIMIT ?
        """
        try:
            rows = self.conn.execute(sql, (query, *kind_params, *proj_params, limit)).fetchall()
        except sqlite3.OperationalError as e:
            # FTS5 raises on syntactically invalid queries (unbalanced quotes,
            # bare operators, etc). Surface a clean error.
            raise ValueError(f"invalid search query: {e}") from e
        return [
            SearchHit(
                message_id=r["message_id"],
                session_id=r["session_id"],
                project_path=r["project_path"],
                timestamp=r["timestamp"],
                role=r["role"],
                kind=r["kind"],
                snippet=r["snippet"],
                score=float(r["score"]),
            )
            for r in rows
        ]

    # --- status --------------------------------------------------------

    def message_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()
        return int(row["c"])

    def kind_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT kind, COUNT(*) AS c FROM messages GROUP BY kind"
        ).fetchall()
        return {r["kind"]: int(r["c"]) for r in rows}

    def session_messages(
        self,
        session_id: str,
        *,
        limit: int = 5000,
    ) -> list[SearchHit]:
        """Every message in a session, oldest-first, with full content.

        The drawer uses this to render the surrounding conversation when
        a hit is clicked. session_id is UNINDEXED so this is a scan, but
        at phase-2 sizes (~50K rows) it's still well under 50ms.
        """
        rows = self.conn.execute(
            """
            SELECT message_id, session_id, project_path, timestamp, role, kind, content
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        return [
            SearchHit(
                message_id=r["message_id"],
                session_id=r["session_id"],
                project_path=r["project_path"],
                timestamp=r["timestamp"],
                role=r["role"],
                kind=r["kind"],
                snippet=r["content"],  # full content
                score=0.0,
            )
            for r in rows
        ]

    def list_projects(self) -> list[tuple[str, int]]:
        """Return (project_path, message_count) for every distinct cwd, count desc."""
        rows = self.conn.execute(
            """
            SELECT project_path, COUNT(*) AS c
            FROM messages
            WHERE project_path != ''
            GROUP BY project_path
            ORDER BY c DESC, project_path ASC
            """
        ).fetchall()
        return [(r["project_path"], int(r["c"])) for r in rows]

    def get_message(self, message_id: str) -> SearchHit | None:
        """Fetch a single row by message_id with the full untruncated content.

        The drawer uses this to show the whole message after the user
        clicks a result row — search snippets are intentionally short.
        """
        row = self.conn.execute(
            """
            SELECT message_id, session_id, project_path, timestamp, role, kind, content
            FROM messages
            WHERE message_id = ?
            LIMIT 1
            """,
            (message_id,),
        ).fetchone()
        if row is None:
            return None
        return SearchHit(
            message_id=row["message_id"],
            session_id=row["session_id"],
            project_path=row["project_path"],
            timestamp=row["timestamp"],
            role=row["role"],
            kind=row["kind"],
            snippet=row["content"],  # full content rather than a snippet
            score=0.0,
        )

    def recent(
        self,
        *,
        limit: int = 50,
        kinds: frozenset[str] | set[str] | None = None,
        project_paths: list[str] | tuple[str, ...] | None = None,
    ) -> list[SearchHit]:
        """Return the N most recent messages by timestamp, no full-text query.

        ISO 8601 timestamps sort lexically, so DESC ordering on the
        UNINDEXED column gives newest-first.
        """
        clauses: list[str] = []
        params: list = []
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            clauses.append(f"kind IN ({placeholders})")
            params.extend(kinds)
        if project_paths:
            placeholders = ",".join("?" for _ in project_paths)
            clauses.append(f"project_path IN ({placeholders})")
            params.extend(project_paths)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT
                message_id, session_id, project_path, timestamp, role, kind,
                substr(content, 1, 240) AS snippet,
                0.0 AS score
            FROM messages
            {where}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        rows = self.conn.execute(sql, (*params, limit)).fetchall()
        return [
            SearchHit(
                message_id=r["message_id"],
                session_id=r["session_id"],
                project_path=r["project_path"],
                timestamp=r["timestamp"],
                role=r["role"],
                kind=r["kind"],
                snippet=r["snippet"],
                score=float(r["score"]),
            )
            for r in rows
        ]

    def file_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM ingest_state").fetchone()
        return int(row["c"])

    def last_indexed_at(self) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(last_indexed_at) AS t FROM ingest_state"
        ).fetchone()
        return row["t"] if row and row["t"] else None
