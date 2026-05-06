"""Watchdog-based incremental ingest.

Watches the projects directory for changes to `.jsonl` session files
and drives `ingest_file` per affected path. Debounces because Claude
Code writes line-by-line and fires many events per second during an
active session.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .index import Index
from .ingest import ingest_file

logger = logging.getLogger(__name__)

# How long after the last write to a file before we ingest it. Long
# enough to coalesce the burst of per-line writes; short enough that
# results show up in search within a few seconds of being written.
DEBOUNCE_SECONDS = 1.0

# Hard ceiling on how long a busy file can defer its ingest. Without
# this an active session that writes continuously would never get
# indexed.
MAX_DEFER_SECONDS = 5.0


class _Handler(FileSystemEventHandler):
    """Routes raw watchdog events to the watcher's pending-set."""

    def __init__(self, watcher: "SessionWatcher") -> None:
        self.watcher = watcher

    def _maybe_track(self, path: str | bytes | None) -> None:
        if not path:
            return
        p = Path(path if isinstance(path, str) else path.decode("utf-8", "replace"))
        if p.suffix != ".jsonl":
            return
        self.watcher._mark_pending(p)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe_track(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe_track(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe_track(getattr(event, "dest_path", None))


class SessionWatcher:
    """Background observer that reindexes session files as they change.

    Uses one daemon worker thread that polls `_pending` and calls
    `ingest_file` after the debounce window. The watchdog Observer's
    own thread feeds `_pending` and never touches the index directly.
    """

    def __init__(
        self,
        *,
        index: Index,
        projects_dir: Path,
        write_lock: threading.Lock,
        debounce_seconds: float = DEBOUNCE_SECONDS,
        max_defer_seconds: float = MAX_DEFER_SECONDS,
    ) -> None:
        self.index = index
        self.projects_dir = projects_dir
        self.write_lock = write_lock
        self.debounce = debounce_seconds
        self.max_defer = max_defer_seconds

        self._observer: Observer | None = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._pending_lock = threading.Lock()
        # path -> (last_seen_at, first_seen_at)
        self._pending: dict[Path, tuple[float, float]] = {}

    # ── public lifecycle ──────────────────────────────────────────────

    def start(self) -> None:
        if self._observer is not None:
            return
        self._observer = Observer()
        self._observer.schedule(
            _Handler(self),
            str(self.projects_dir),
            recursive=True,
        )
        self._observer.start()
        self._worker = threading.Thread(
            target=self._run, name="claude-search-watcher", daemon=True
        )
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None

    # ── pending-set bookkeeping (called from observer thread) ─────────

    def _mark_pending(self, path: Path) -> None:
        now = time.monotonic()
        with self._pending_lock:
            existing = self._pending.get(path)
            first_seen = existing[1] if existing is not None else now
            self._pending[path] = (now, first_seen)

    # ── worker loop ───────────────────────────────────────────────────

    def _run(self) -> None:
        # Poll cadence: a fraction of the debounce window so we react
        # promptly without spinning.
        tick = max(0.1, self.debounce / 4)
        while not self._stop.is_set():
            self._stop.wait(tick)
            self._drain_ready()
        # On shutdown, flush whatever's pending.
        self._drain_ready(force=True)

    def _drain_ready(self, *, force: bool = False) -> None:
        now = time.monotonic()
        ready: list[Path] = []
        with self._pending_lock:
            for path, (last, first) in list(self._pending.items()):
                idle = now - last
                age = now - first
                if force or idle >= self.debounce or age >= self.max_defer:
                    ready.append(path)
                    self._pending.pop(path, None)
        for path in ready:
            self._ingest_one(path)

    def _ingest_one(self, path: Path) -> None:
        try:
            with self.write_lock:
                with self.index.transaction():
                    inserted, _read = ingest_file(self.index, path, incremental=True)
            if inserted:
                logger.info("watcher: %s +%d msgs", path.name, inserted)
        except FileNotFoundError:
            # File was deleted between event and ingest; safe to ignore.
            logger.debug("watcher: %s vanished before ingest", path)
        except Exception as e:
            # One bad file should not kill the watcher.
            logger.warning("watcher: failed to ingest %s: %s", path, e)
