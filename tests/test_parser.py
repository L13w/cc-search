from __future__ import annotations

import io
from pathlib import Path

import pytest

from claude_search.parser import (
    KIND_ASSISTANT_PROSE,
    KIND_TOOL_RESULT,
    KIND_TOOL_USE,
    KIND_USER_TYPED,
    MAX_FIELD_CHARS,
    iter_complete_lines,
    parse_file,
    parse_line,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_iter_complete_lines_skips_partial_trailing_line():
    data = b'{"a":1}\n{"b":2}\n{"c":3'  # last line has no \n
    fp = io.BytesIO(data)
    out = list(iter_complete_lines(fp))
    assert [t for _, t in out] == ['{"a":1}\n', '{"b":2}\n']


def test_iter_complete_lines_resumes_from_offset():
    data = b'{"a":1}\n{"b":2}\n{"c":3}\n'
    fp = io.BytesIO(data)
    out = list(iter_complete_lines(fp, start_offset=8))  # skip first line
    assert [t for _, t in out] == ['{"b":2}\n', '{"c":3}\n']
    assert [o for o, _ in out] == [8, 16]


def test_parse_normal_file_yields_indexable_messages():
    path = FIXTURES / "normal.jsonl"
    parsed = [m for m, _ in parse_file(path) if m is not None]
    ids = [m.message_id for m in parsed]
    # queue-operation skipped; user, assistant text, assistant tool_use,
    # user tool_result are all indexed.
    assert ids == ["u-1", "a-1", "a-2", "u-2"]

    by_id = {m.message_id: m for m in parsed}
    assert by_id["u-1"].role == "user"
    assert by_id["u-1"].kind == KIND_USER_TYPED
    assert "ZK proof verifier" in by_id["u-1"].content
    assert by_id["u-1"].project_path == "/home/u/proj"
    assert by_id["u-1"].session_id == "sess-1"

    # Assistant message flattens thinking + text into prose.
    assert by_id["a-1"].kind == KIND_ASSISTANT_PROSE
    assert "thinking" not in by_id["a-1"].content  # the literal type label
    assert "zk proof verifier" in by_id["a-1"].content
    assert "sketching the verifier API" in by_id["a-1"].content

    # Tool-use-only assistant message.
    assert by_id["a-2"].kind == KIND_TOOL_USE
    assert "[tool_use:Bash]" in by_id["a-2"].content
    assert "cargo new zk-verifier" in by_id["a-2"].content

    # Tool result.
    assert by_id["u-2"].kind == KIND_TOOL_RESULT
    assert "[tool_result]" in by_id["u-2"].content
    assert "zk-verifier" in by_id["u-2"].content


def test_assistant_text_with_tool_use_classifies_as_prose_and_drops_tool_blocks():
    line = (
        '{"type":"assistant","sessionId":"s","cwd":"/p","uuid":"a-mix",'
        '"timestamp":"t","message":{"role":"assistant","content":['
        '{"type":"text","text":"Running the test suite now."},'
        '{"type":"tool_use","id":"x","name":"Bash","input":{"command":"pytest"}}'
        "]}}"
    )
    msg = parse_line(line, file_path=Path("x"), line_number=1, byte_offset=0)
    assert msg is not None
    assert msg.kind == KIND_ASSISTANT_PROSE
    assert "Running the test suite now." in msg.content
    # The tool_use block must be dropped from prose rows.
    assert "tool_use:" not in msg.content
    assert "pytest" not in msg.content


def test_user_text_block_list_classifies_as_typed():
    line = (
        '{"type":"user","sessionId":"s","cwd":"/p","uuid":"u-blk",'
        '"timestamp":"t","message":{"role":"user","content":['
        '{"type":"text","text":"hello there"}]}}'
    )
    msg = parse_line(line, file_path=Path("x"), line_number=1, byte_offset=0)
    assert msg is not None
    assert msg.kind == KIND_USER_TYPED
    assert msg.content == "hello there"


def test_parse_skips_queue_operation_and_other_event_types():
    path = FIXTURES / "normal.jsonl"
    skipped = [(m, off) for m, off in parse_file(path) if m is None]
    # The first line is the queue-operation. We should advance past it.
    assert skipped, "expected at least one skipped line"


def test_parse_empty_file_yields_nothing():
    path = FIXTURES / "empty.jsonl"
    out = list(parse_file(path))
    assert out == []


def test_parse_unicode_content_round_trips():
    path = FIXTURES / "unicode.jsonl"
    parsed = [m for m, _ in parse_file(path) if m is not None]
    contents = " ".join(m.content for m in parsed)
    assert "こんにちは" in contents
    assert "café" in contents
    assert "🚀" in contents
    assert "naïve résumé" in contents


def test_parse_malformed_line_is_skipped_and_does_not_break_subsequent():
    path = FIXTURES / "malformed.jsonl"
    parsed = [m for m, _ in parse_file(path) if m is not None]
    ids = [m.message_id for m in parsed]
    # 'u-good-1' before the bad line, 'u-good-2' after it.
    # missing uuid line and empty content line are both skipped.
    assert ids == ["u-good-1", "u-good-2"]


def test_parse_truncates_oversized_fields():
    big = "x" * (MAX_FIELD_CHARS + 5_000)
    line = (
        '{"type":"user","sessionId":"s","cwd":"/p","uuid":"u","timestamp":"t",'
        f'"message":{{"role":"user","content":{__import__("json").dumps(big)}}}}}'
    )
    msg = parse_line(line, file_path=Path("x"), line_number=1, byte_offset=0)
    assert msg is not None
    assert len(msg.content) == MAX_FIELD_CHARS


def test_parse_records_byte_offset():
    path = FIXTURES / "normal.jsonl"
    msgs = [m for m, _ in parse_file(path) if m is not None]
    assert msgs[0].byte_offset > 0  # first indexable line is not at offset 0
    # Offsets must be strictly increasing.
    offsets = [m.byte_offset for m in msgs]
    assert offsets == sorted(offsets)


def test_parse_resume_yields_only_new_lines():
    path = FIXTURES / "normal.jsonl"
    all_pairs = list(parse_file(path))
    # Pretend we stopped after the second line.
    midpoint_offset = all_pairs[1][1]
    resumed = list(parse_file(path, start_offset=midpoint_offset))
    resumed_ids = [m.message_id for m, _ in resumed if m is not None]
    full_ids = [m.message_id for m, _ in all_pairs if m is not None]
    # The resume run should only see the suffix.
    assert resumed_ids == full_ids[len(full_ids) - len(resumed_ids):]
