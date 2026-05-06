"""Walk the projects directory and drive parser + index."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .index import Index
from .parser import ParsedMessage, parse_file
from .paths import decode_project_dirname

logger = logging.getLogger(__name__)


# Project directories whose encoded name contains any of these substrings
# are skipped. Default: claude-mem observer sessions, which contain
# structured XML records of file edits, not user/AI conversation.
# Override at runtime via CLAUDE_SEARCH_EXCLUDE_DIRS (comma-separated)
# or pass `exclude_dir_patterns=` directly. Pass `()` to disable.
DEFAULT_EXCLUDE_DIR_PATTERNS: tuple[str, ...] = ("claude-mem",)


def _resolve_exclude_patterns(
    exclude_dir_patterns: tuple[str, ...] | None,
) -> tuple[str, ...]:
    if exclude_dir_patterns is not None:
        return tuple(exclude_dir_patterns)
    env = os.environ.get("CLAUDE_SEARCH_EXCLUDE_DIRS")
    if env is not None:
        return tuple(p.strip() for p in env.split(",") if p.strip())
    return DEFAULT_EXCLUDE_DIR_PATTERNS


def is_path_excluded(
    path: Path,
    projects_dir: Path,
    *,
    exclude_dir_patterns: tuple[str, ...] | None = None,
) -> bool:
    """True if `path` lives under a project dir whose name matches a pattern.

    Path is checked against the IMMEDIATE child of `projects_dir`, since
    that's the encoded-cwd directory. Patterns are substring matches.
    """
    patterns = _resolve_exclude_patterns(exclude_dir_patterns)
    if not patterns:
        return False
    try:
        rel = path.resolve().relative_to(projects_dir.resolve())
    except (ValueError, OSError):
        return False
    if not rel.parts:
        return False
    project_name = rel.parts[0]
    return any(p in project_name for p in patterns)


@dataclass
class IngestStats:
    files_seen: int = 0
    files_changed: int = 0
    messages_inserted: int = 0
    bytes_read: int = 0


def find_session_files(
    projects_dir: Path,
    *,
    exclude_dir_patterns: tuple[str, ...] | None = None,
) -> Iterable[Path]:
    """Yield every `*.jsonl` session file under the projects directory.

    Skips project directories whose encoded name matches any of the
    exclude patterns (default: `("claude-mem",)`). Sessions live one
    level deep: `<projects_dir>/<encoded_project>/<session>.jsonl`,
    sometimes nested in `subagents/` for older formats.
    """
    if not projects_dir.exists():
        return []
    patterns = _resolve_exclude_patterns(exclude_dir_patterns)
    out: list[Path] = []
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        name = project_dir.name
        if patterns and any(p in name for p in patterns):
            logger.debug("skipping excluded project dir: %s", name)
            continue
        out.extend(sorted(project_dir.rglob("*.jsonl")))
    return out


def ingest_file(
    index: Index,
    path: Path,
    *,
    incremental: bool = True,
) -> tuple[int, int]:
    """Ingest a single file. Returns (messages_inserted, bytes_read).

    `bytes_read` is the number of new bytes consumed (after the resume offset),
    and `messages_inserted` counts only newly-inserted rows.
    """
    start_offset = index.get_ingest_offset(path) if incremental else 0
    try:
        size = path.stat().st_size
    except OSError as e:
        logger.warning("could not stat %s: %s", path, e)
        return 0, 0

    if incremental and start_offset >= size:
        # File hasn't grown since last ingest; nothing to do. (If it shrank,
        # something is unusual — possibly a rotated file. We leave it alone
        # and let `index --rebuild` recover.)
        return 0, 0

    project_path_fallback = decode_project_dirname(path.parent.name)
    inserted = 0
    last_offset = start_offset
    batch: list[ParsedMessage] = []

    try:
        for parsed, end_offset in parse_file(
            path,
            start_offset=start_offset,
            project_path_fallback=project_path_fallback,
        ):
            last_offset = end_offset
            if parsed is not None:
                batch.append(parsed)
            if len(batch) >= 500:
                inserted += index.insert_messages(batch)
                batch.clear()
    except OSError as e:
        logger.warning("read error on %s: %s", path, e)

    if batch:
        inserted += index.insert_messages(batch)
    index.set_ingest_offset(path, last_offset)
    return inserted, max(0, last_offset - start_offset)


def ingest_all(
    index: Index,
    projects_dir: Path,
    *,
    incremental: bool = True,
    progress_cb=None,
) -> IngestStats:
    """Walk the projects directory and ingest every .jsonl file.

    `progress_cb`, if provided, is called as `progress_cb(path, stats)` after
    each file. Useful for the CLI to render a progress bar without coupling
    the ingest loop to click.
    """
    stats = IngestStats()
    files = list(find_session_files(projects_dir))
    for path in files:
        stats.files_seen += 1
        try:
            with index.transaction():
                inserted, read = ingest_file(index, path, incremental=incremental)
        except Exception as e:
            # One bad file should not stop the whole walk.
            logger.warning("failed to ingest %s: %s", path, e)
            if progress_cb:
                progress_cb(path, stats)
            continue
        if inserted or read:
            stats.files_changed += 1
        stats.messages_inserted += inserted
        stats.bytes_read += read
        if progress_cb:
            progress_cb(path, stats)
    return stats
