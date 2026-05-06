"""Walk the projects directory and drive parser + index."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .index import Index
from .parser import ParsedMessage, parse_file
from .paths import decode_project_dirname

logger = logging.getLogger(__name__)


@dataclass
class IngestStats:
    files_seen: int = 0
    files_changed: int = 0
    messages_inserted: int = 0
    bytes_read: int = 0


def find_session_files(projects_dir: Path) -> Iterable[Path]:
    """Yield every `*.jsonl` session file under the projects directory.

    Sessions live one level deep: `<projects_dir>/<encoded_project>/<session>.jsonl`.
    Some entries are themselves directories (older session formats). We
    rglob to be tolerant of either layout.
    """
    if not projects_dir.exists():
        return []
    return sorted(projects_dir.rglob("*.jsonl"))


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
