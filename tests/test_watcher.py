"""Watcher integration test — append to a session file, expect reindex."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from claude_search.index import Index
from claude_search.watcher import SessionWatcher


def _wait_until(predicate, timeout=5.0, tick=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(tick)
    return False


def test_watcher_picks_up_appended_lines(tmp_path: Path):
    projects = tmp_path / "projects" / "-home-u-proj"
    projects.mkdir(parents=True)
    src = projects / "session.jsonl"

    line1 = (
        b'{"type":"user","sessionId":"s","cwd":"/home/u/proj","uuid":"w-1",'
        b'"timestamp":"t","message":{"role":"user","content":"first watcher message"}}\n'
    )
    src.write_bytes(line1)

    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        # Initial seed so ingest_state has an offset (mimics startup ingest).
        from claude_search.ingest import ingest_file
        ingest_file(idx, src)
        assert idx.message_count() == 1

        watcher = SessionWatcher(
            index=idx,
            projects_dir=tmp_path / "projects",
            write_lock=threading.Lock(),
            debounce_seconds=0.2,
            max_defer_seconds=1.0,
        )
        watcher.start()
        try:
            line2 = (
                b'{"type":"user","sessionId":"s","cwd":"/home/u/proj","uuid":"w-2",'
                b'"timestamp":"t","message":{"role":"user","content":"second watcher message"}}\n'
            )
            with open(src, "ab") as fp:
                fp.write(line2)
            assert _wait_until(lambda: idx.message_count() == 2, timeout=4.0), \
                f"expected 2 msgs, got {idx.message_count()}"
        finally:
            watcher.stop()
