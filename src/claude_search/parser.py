"""Defensive JSONL parser for Claude Code session files.

The session JSONL schema is undocumented and varies across Claude Code
versions. We extract the fields we care about, skip what we don't
understand, and never crash on a single malformed line.

Each indexable event is classified into a `kind`:

- ``user_typed``: text the human typed into Claude Code
- ``assistant_prose``: Claude's text/thinking replies (no tool calls)
- ``tool_use``: Claude's tool invocations (Bash, Edit, Read, etc.)
- ``tool_result``: output returned to Claude from a tool
- ``image``: an image attachment with no accompanying text

Indexed `content` is the kind-appropriate text only — tool_use blocks
are dropped from `assistant_prose` rows, tool_result blocks are dropped
from `user_typed` rows, etc. This keeps prose searches signal-rich.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

# Per-field truncation. Long tool outputs and file dumps are noise and would
# bloat the FTS index without aiding recall.
MAX_FIELD_CHARS = 10_000

# Event-level types we care about. Everything else (queue-operation, progress,
# file-history-snapshot, pr-link, ai-title, attachment, system, last-prompt)
# carries no useful free text for a content-search index.
INDEXABLE_TYPES = frozenset({"user", "assistant"})

KIND_USER_TYPED = "user_typed"
KIND_ASSISTANT_PROSE = "assistant_prose"
KIND_TOOL_USE = "tool_use"
KIND_TOOL_RESULT = "tool_result"
KIND_IMAGE = "image"

ALL_KINDS = frozenset(
    {KIND_USER_TYPED, KIND_ASSISTANT_PROSE, KIND_TOOL_USE, KIND_TOOL_RESULT, KIND_IMAGE}
)

# Kinds the CLI shows by default. Others are still indexed and reachable via
# `--all` so users can opt back in without a reindex.
DEFAULT_QUERY_KINDS = frozenset({KIND_USER_TYPED, KIND_ASSISTANT_PROSE})


@dataclass(frozen=True)
class ParsedMessage:
    """One indexable record extracted from a JSONL line."""

    message_id: str
    session_id: str
    project_path: str
    timestamp: str
    role: str
    kind: str
    content: str
    byte_offset: int  # offset of the start of the source line in the file


def iter_complete_lines(fp, *, start_offset: int = 0) -> Iterator[tuple[int, str]]:
    """Yield (byte_offset, line) pairs for lines that end in '\\n'.

    A trailing partial line (no newline) is treated as mid-write and skipped.
    The caller can persist the offset of the last fully-read line and resume
    from there next time.

    The file must be opened in binary mode so byte offsets are meaningful.
    """
    fp.seek(start_offset)
    pos = start_offset
    while True:
        raw = fp.readline()
        if not raw:
            return
        if not raw.endswith(b"\n"):
            # Mid-write partial line; do not advance.
            return
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("non-utf8 bytes at offset %d, skipping", pos)
            pos += len(raw)
            continue
        yield pos, text
        pos += len(raw)


def _truncate(s: str) -> str:
    if len(s) > MAX_FIELD_CHARS:
        return s[:MAX_FIELD_CHARS]
    return s


def _render_text_block(b: dict) -> str:
    return _truncate(str(b.get("text", "")))


def _render_thinking_block(b: dict) -> str:
    return _truncate(str(b.get("thinking", "")))


def _render_tool_use_block(b: dict) -> str:
    name = b.get("name", "")
    inp = b.get("input")
    try:
        inp_s = json.dumps(inp, ensure_ascii=False, default=str) if inp is not None else ""
    except Exception:
        inp_s = str(inp)
    return _truncate(f"[tool_use:{name}] {inp_s}".strip())


def _render_tool_result_block(b: dict) -> str:
    c = b.get("content")
    if isinstance(c, str):
        return _truncate(f"[tool_result] {c}".strip())
    if isinstance(c, list):
        parts: list[str] = []
        for inner in c:
            if isinstance(inner, dict) and inner.get("type") == "text":
                parts.append(str(inner.get("text", "")))
            elif isinstance(inner, dict):
                try:
                    parts.append(json.dumps(inner, ensure_ascii=False, default=str))
                except Exception:
                    parts.append(str(inner))
        return _truncate(f"[tool_result] {' '.join(p for p in parts if p)}".strip())
    return ""


def _classify_and_render(role: str, content: object) -> tuple[str, str]:
    """Return (kind, rendered_content) for one event.

    Returns ('', '') if there's nothing useful to index.
    """
    if isinstance(content, str):
        text = _truncate(content)
        if not text:
            return "", ""
        if role == "assistant":
            return KIND_ASSISTANT_PROSE, text
        return KIND_USER_TYPED, text

    if not isinstance(content, list):
        return "", ""

    text_parts: list[str] = []
    tool_use_parts: list[str] = []
    tool_result_parts: list[str] = []
    saw_image = False

    for b in content:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "text":
            text_parts.append(_render_text_block(b))
        elif bt == "thinking":
            text_parts.append(_render_thinking_block(b))
        elif bt == "tool_use":
            tool_use_parts.append(_render_tool_use_block(b))
        elif bt == "tool_result":
            tool_result_parts.append(_render_tool_result_block(b))
        elif bt == "image":
            saw_image = True

    text_parts = [p for p in text_parts if p]
    tool_use_parts = [p for p in tool_use_parts if p]
    tool_result_parts = [p for p in tool_result_parts if p]

    # Prefer prose if there's any. A message with both text and tool calls
    # gets stored as prose with the tool blocks dropped, since prose is what
    # the user-facing search is about.
    if text_parts:
        joined = _truncate(" ".join(text_parts))
        kind = KIND_ASSISTANT_PROSE if role == "assistant" else KIND_USER_TYPED
        return kind, joined

    if tool_use_parts:
        return KIND_TOOL_USE, _truncate(" ".join(tool_use_parts))

    if tool_result_parts:
        return KIND_TOOL_RESULT, _truncate(" ".join(tool_result_parts))

    if saw_image:
        return KIND_IMAGE, "[image]"

    return "", ""


def parse_line(
    raw_line: str,
    *,
    file_path: Path,
    line_number: int,
    byte_offset: int,
    project_path_fallback: str = "",
) -> ParsedMessage | None:
    """Parse a single JSONL line into a ParsedMessage, or None if it should be skipped.

    Never raises. Logs a warning for malformed JSON and returns None.
    """
    try:
        obj = json.loads(raw_line)
    except json.JSONDecodeError as e:
        logger.warning("json decode error in %s line %d: %s", file_path, line_number, e)
        return None
    if not isinstance(obj, dict):
        return None

    event_type = obj.get("type")
    if event_type not in INDEXABLE_TYPES:
        return None

    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None

    role = msg.get("role") or event_type
    kind, content_text = _classify_and_render(role, msg.get("content"))
    if not content_text:
        return None

    message_id = obj.get("uuid") or msg.get("id") or ""
    session_id = obj.get("sessionId") or ""
    timestamp = obj.get("timestamp") or ""
    project_path = obj.get("cwd") or project_path_fallback

    if not message_id:
        # Without a stable id we can't dedupe across reindexes. Skip.
        logger.debug("missing uuid in %s line %d, skipping", file_path, line_number)
        return None

    return ParsedMessage(
        message_id=str(message_id),
        session_id=str(session_id),
        project_path=str(project_path),
        timestamp=str(timestamp),
        role=str(role),
        kind=kind,
        content=content_text,
        byte_offset=byte_offset,
    )


def parse_file(
    path: Path,
    *,
    start_offset: int = 0,
    project_path_fallback: str = "",
) -> Iterator[tuple[ParsedMessage | None, int]]:
    """Yield (ParsedMessage|None, end_offset) pairs for each complete line in the file.

    `end_offset` is the byte offset *after* the line just consumed. The caller
    persists the last yielded `end_offset` to resume next time without
    rereading already-indexed bytes.

    Lines that don't parse, lack a message id, or aren't indexable types yield
    a `None` ParsedMessage but still advance the offset.
    """
    with path.open("rb") as fp:
        for line_no, (offset, text) in enumerate(
            iter_complete_lines(fp, start_offset=start_offset),
            start=1,
        ):
            try:
                msg = parse_line(
                    text,
                    file_path=path,
                    line_number=line_no,
                    byte_offset=offset,
                    project_path_fallback=project_path_fallback,
                )
            except Exception as e:
                logger.warning("unexpected parse error in %s line %d: %s", path, line_no, e)
                msg = None
            yield msg, offset + len(text.encode("utf-8"))
