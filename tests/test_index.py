from __future__ import annotations

from pathlib import Path

import pytest

from claude_search.index import Index
from claude_search.ingest import ingest_all, ingest_file
from claude_search.parser import (
    DEFAULT_QUERY_KINDS,
    KIND_ASSISTANT_PROSE,
    KIND_TOOL_RESULT,
    KIND_TOOL_USE,
    KIND_USER_TYPED,
    ParsedMessage,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _msg(**overrides) -> ParsedMessage:
    base = dict(
        message_id="m-1",
        session_id="s-1",
        project_path="/home/u/proj",
        timestamp="2026-03-27T22:27:34Z",
        role="user",
        kind=KIND_USER_TYPED,
        content="hello world ZK proof verifier in rust",
        byte_offset=0,
    )
    base.update(overrides)
    return ParsedMessage(**base)


def test_schema_creates_clean(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        assert idx.message_count() == 0
        assert idx.file_count() == 0


def test_insert_and_search(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        idx.insert_messages(
            [
                _msg(message_id="a", content="cargo new zk-verifier"),
                _msg(message_id="b", content="totally unrelated python pickle text"),
            ]
        )
        hits = idx.search("zk")
        assert len(hits) == 1
        assert hits[0].message_id == "a"
        assert "**" in hits[0].snippet  # FTS5 snippet wraps matches


def test_insert_dedupe_on_message_id(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        n1 = idx.insert_messages([_msg(message_id="a", content="alpha")])
        n2 = idx.insert_messages([_msg(message_id="a", content="alpha")])
        assert n1 == 1
        assert n2 == 0
        assert idx.message_count() == 1


def test_search_empty_query_returns_nothing(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        idx.insert_messages([_msg()])
        assert idx.search("") == []
        assert idx.search("   ") == []


def test_search_invalid_query_raises_value_error(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        idx.insert_messages([_msg()])
        with pytest.raises(ValueError):
            # An unbalanced quote is a syntax error in FTS5.
            idx.search('"unbalanced')


def test_ingest_file_records_offset_and_inserts(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        inserted, read = ingest_file(idx, FIXTURES / "normal.jsonl")
        assert inserted == 4
        assert read > 0
        # Offset persisted; second pass is a no-op.
        inserted2, read2 = ingest_file(idx, FIXTURES / "normal.jsonl")
        assert inserted2 == 0
        assert read2 == 0


def test_ingest_file_recovers_from_malformed_lines(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        inserted, _ = ingest_file(idx, FIXTURES / "malformed.jsonl")
        # Two good messages survive; bad-json + missing-uuid + empty-content drop.
        assert inserted == 2


def test_ingest_resume_from_partial_last_line(tmp_path: Path):
    """Simulate a mid-write file: last line lacks \\n. Then add the rest and retry."""
    src = tmp_path / "session.jsonl"
    line1 = b'{"type":"user","sessionId":"s","cwd":"/p","uuid":"r-1","timestamp":"t","message":{"role":"user","content":"first"}}\n'
    partial = b'{"type":"user","sessionId":"s","cwd":"/p","uuid":"r-2","timestamp":"t","message":{"role":"user","content":"sec'
    src.write_bytes(line1 + partial)

    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        inserted, _ = ingest_file(idx, src)
        assert inserted == 1
        offset_after_first = idx.get_ingest_offset(src)
        assert offset_after_first == len(line1)

        # Now the writer finishes the line and appends a third one.
        rest = b'ond"}}\n{"type":"user","sessionId":"s","cwd":"/p","uuid":"r-3","timestamp":"t","message":{"role":"user","content":"third"}}\n'
        src.write_bytes(line1 + partial + rest)

        inserted2, _ = ingest_file(idx, src)
        assert inserted2 == 2
        assert idx.message_count() == 3


def test_find_session_files_skips_claude_mem_dirs(tmp_path: Path):
    """Default exclude pattern hides claude-mem observer sessions."""
    from claude_search.ingest import find_session_files

    proj = tmp_path / "-home-u-projA"
    proj.mkdir()
    (proj / "sess1.jsonl").write_text("{}\n", encoding="utf-8")

    obs = tmp_path / "-home-u-.claude-mem-observer-sessions"
    obs.mkdir()
    (obs / "sess2.jsonl").write_text("{}\n", encoding="utf-8")

    files = list(find_session_files(tmp_path))
    names = sorted(p.name for p in files)
    assert names == ["sess1.jsonl"]
    # Opt back in via explicit empty patterns.
    files_all = list(find_session_files(tmp_path, exclude_dir_patterns=()))
    assert sorted(p.name for p in files_all) == ["sess1.jsonl", "sess2.jsonl"]


def test_find_session_files_respects_env_var(tmp_path: Path, monkeypatch):
    from claude_search.ingest import find_session_files

    a = tmp_path / "-home-u-keep"
    b = tmp_path / "-home-u-drop-me"
    a.mkdir(); b.mkdir()
    (a / "k.jsonl").write_text("{}\n", encoding="utf-8")
    (b / "d.jsonl").write_text("{}\n", encoding="utf-8")

    monkeypatch.setenv("CLAUDE_SEARCH_EXCLUDE_DIRS", "drop-me")
    files = list(find_session_files(tmp_path))
    assert [p.name for p in files] == ["k.jsonl"]


def test_is_path_excluded(tmp_path: Path):
    from claude_search.ingest import is_path_excluded

    keep = tmp_path / "-home-u-projA" / "sess.jsonl"
    drop = tmp_path / "-home-u-.claude-mem-observer" / "sess.jsonl"
    keep.parent.mkdir(parents=True)
    drop.parent.mkdir(parents=True)
    keep.touch(); drop.touch()

    assert is_path_excluded(drop, tmp_path) is True
    assert is_path_excluded(keep, tmp_path) is False


def test_ingest_all_walks_directory(tmp_path: Path):
    # Build a fake projects dir layout matching ~/.claude/projects.
    proj_a = tmp_path / "-home-u-projA"
    proj_b = tmp_path / "-home-u-projB"
    proj_a.mkdir()
    proj_b.mkdir()
    (proj_a / "sess1.jsonl").write_text(
        '{"type":"user","sessionId":"sA","cwd":"/home/u/projA","uuid":"a-1","timestamp":"t","message":{"role":"user","content":"alpha message"}}\n',
        encoding="utf-8",
    )
    (proj_b / "sess2.jsonl").write_text(
        '{"type":"user","sessionId":"sB","cwd":"/home/u/projB","uuid":"b-1","timestamp":"t","message":{"role":"user","content":"beta message"}}\n',
        encoding="utf-8",
    )

    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        stats = ingest_all(idx, tmp_path)
        assert stats.files_seen == 2
        assert stats.messages_inserted == 2
        # Project path comes from cwd in the events, not the encoded dirname.
        hit = idx.search("alpha")[0]
        assert hit.project_path == "/home/u/projA"


def test_rebuild_drops_existing_data(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        idx.insert_messages([_msg(message_id="m", content="something")])
        assert idx.message_count() == 1
        idx.reset()
        assert idx.message_count() == 0
        assert idx.file_count() == 0


def test_search_kinds_filter(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        idx.insert_messages(
            [
                _msg(message_id="t1", kind=KIND_USER_TYPED, content="install varnish"),
                _msg(message_id="p1", kind=KIND_ASSISTANT_PROSE, content="use varnish 7.4"),
                _msg(message_id="r1", kind=KIND_TOOL_RESULT, content="[tool_result] varnish: not installed"),
                _msg(message_id="u1", kind=KIND_TOOL_USE, content="[tool_use:Bash] {\"command\":\"apt-get install varnish\"}"),
            ]
        )
        # No filter (default at the Index layer): all kinds.
        all_ids = sorted(h.message_id for h in idx.search("varnish"))
        assert all_ids == ["p1", "r1", "t1", "u1"]

        # CLI's default filter: only typed + prose.
        ids = sorted(h.message_id for h in idx.search("varnish", kinds=DEFAULT_QUERY_KINDS))
        assert ids == ["p1", "t1"]

        # Explicit single-kind filter.
        only_results = [h.message_id for h in idx.search("varnish", kinds={KIND_TOOL_RESULT})]
        assert only_results == ["r1"]


def test_kind_counts_breakdown(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        idx.insert_messages(
            [
                _msg(message_id="a", kind=KIND_USER_TYPED, content="x"),
                _msg(message_id="b", kind=KIND_USER_TYPED, content="y"),
                _msg(message_id="c", kind=KIND_ASSISTANT_PROSE, content="z"),
            ]
        )
        counts = idx.kind_counts()
        assert counts == {KIND_USER_TYPED: 2, KIND_ASSISTANT_PROSE: 1}


def test_schema_version_mismatch_triggers_rebuild(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        idx.insert_messages([_msg(message_id="m", content="alpha")])
        idx.set_ingest_offset(tmp_path / "f.jsonl", 100)
        # Forge an older schema_version in the meta table.
        idx.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '0')"
        )
        idx.conn.commit()

    # Reopening should detect the mismatch and rebuild from scratch.
    with Index(db) as idx:
        assert idx.message_count() == 0
        assert idx.file_count() == 0


def _insert_test_token(idx: Index, *, label: str = "client") -> None:
    idx.conn.execute(
        """
        INSERT INTO tokens(id, token_hash, kind, label, created_at)
        VALUES (?, ?, 'bearer', ?, '2026-05-06T00:00:00Z')
        """,
        (f"id-{label}", f"hash-{label}", label),
    )
    idx.conn.commit()


def _count_tokens(idx: Index) -> int:
    return int(idx.conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0])


def test_reset_preserves_tokens(tmp_path: Path):
    """`index --rebuild` must not log paired clients out."""
    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        idx.insert_messages([_msg(message_id="m", content="alpha")])
        _insert_test_token(idx, label="laptop")
        assert _count_tokens(idx) == 1

        idx.reset()

        assert idx.message_count() == 0  # index data wiped
        assert _count_tokens(idx) == 1   # tokens survive


def test_schema_version_rebuild_preserves_tokens(tmp_path: Path):
    """A schema bump that rebuilds index tables must keep tokens intact."""
    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        idx.insert_messages([_msg(message_id="m", content="alpha")])
        _insert_test_token(idx, label="laptop")
        idx.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '0')"
        )
        idx.conn.commit()

    with Index(db) as idx:
        assert idx.message_count() == 0  # index rebuilt
        assert _count_tokens(idx) == 1   # token survived the rebuild
