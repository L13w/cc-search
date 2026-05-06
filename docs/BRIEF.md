claude-search: project brief
Goal
A local full-text search index over Claude Code session history, runnable on Windows and Linux. Phase 1 is a CLI that builds the index and queries it. Phase 2 (later) adds a file watcher and HTTP API and runs as a background service.
Scope of phase 1 (this brief)

One-shot indexer that walks ~/.claude/projects/ (Linux) or %USERPROFILE%\.claude\projects\ (Windows), parses every .jsonl session file, and writes to a SQLite FTS5 index.
CLI query command that takes a search string and returns matching messages with timestamps.
Both human-readable and --json output.

Out of scope for phase 1: file watcher, HTTP API, Windows Service / systemd unit, web frontend, conversation context expansion, project/date filters. Those are phase 2+.
Stack

Python 3.11+
Standard library sqlite3 with FTS5 (no external DB)
click or typer for the CLI
platformdirs for cross-platform path resolution
No other runtime dependencies

Project layout
claude-search/
├── pyproject.toml
├── README.md
├── src/claude_search/
│   ├── __init__.py
│   ├── cli.py          # entry point: `claude-search`
│   ├── paths.py        # cross-platform path resolution
│   ├── parser.py       # JSONL -> normalized message records
│   ├── index.py        # SQLite FTS5 schema + insert/query
│   └── ingest.py       # walk projects dir, drive parser+index
└── tests/
    ├── test_parser.py
    └── test_index.py
Install via pip install -e . so claude-search ends up on PATH on both Windows and Linux.
Data source assumptions
Claude Code stores sessions as JSONL under ~/.claude/projects/<encoded-project-path>/<session-uuid>.jsonl. Each line is a JSON object representing one event (user message, assistant message, tool use, tool result, etc.).
Important first step: the agent should run head -n 5 on a real session file before writing the parser, since the schema is undocumented and may have changed. The parser should be defensive: extract the fields it knows, skip lines it can't parse, log warnings rather than crash.
Expected fields per line (verify against actual data):

type or role — message kind
timestamp or created_at — ISO 8601 string
content — string or list of content blocks
uuid or id — message identifier
sessionId — session identifier (often also encoded in the filename)

Index schema
sqlCREATE VIRTUAL TABLE messages USING fts5(
    message_id UNINDEXED,
    session_id UNINDEXED,
    project_path UNINDEXED,
    timestamp UNINDEXED,
    role UNINDEXED,
    content,
    tokenize = 'porter unicode61'
);

CREATE TABLE ingest_state (
    file_path TEXT PRIMARY KEY,
    last_byte_offset INTEGER NOT NULL,
    last_indexed_at TEXT NOT NULL
);
The ingest_state table tracks per-file byte offset so phase 2's watcher can resume from where phase 1 left off without reindexing.
Index location: use platformdirs.user_data_dir("claude-search"), which gives:

Windows: C:\Users\llew\AppData\Local\claude-search\
Linux: ~/.local/share/claude-search/

Override with --db-path flag.
Content extraction rules

For text messages, index the raw text.
For tool calls and tool results, index a flattened representation (tool name + stringified input/output), but truncate any single field to 10,000 chars to keep the index sane. Long file dumps and web fetches are noise.
Skip empty content.
One row per message, not per content block.

CLI surface
claude-search index                    # full reindex from scratch
claude-search index --incremental      # default: only new/changed files
claude-search query "ZK proof"         # default human-readable output
claude-search query "ZK proof" --json
claude-search query "ZK proof" --limit 20   # default 20
claude-search status                   # show index size, message count, last ingest time
Human-readable output format:
2026-03-14 09:22  [scheducal]  user
  > ...snippet with **highlighted** match...

2026-03-14 09:23  [scheducal]  assistant
  > ...snippet...
JSON output: array of {message_id, session_id, project_path, timestamp, role, snippet, score}.
Use FTS5's built-in snippet() and bm25() for highlighting and ranking.
Acceptance criteria

claude-search index completes without errors on a real ~/.claude/projects/ directory.
claude-search query "<term I know is in my history>" returns relevant results in under 100ms after index is built.
Running claude-search index a second time with no new data is a no-op (offset tracking works).
Works identically on Windows 11 and Ubuntu 22.04+.
--json output is valid JSON parseable by jq.
README explains install, first-run indexing, and basic queries.

Notes for the agent

Start by exploring ~/.claude/projects/ to understand the actual JSONL schema. Don't assume.
Handle partial last lines (file may be mid-write): only process lines ending in \n.
Use pathlib.Path everywhere, never string path concatenation, since this needs to work on Windows.
Don't pull in heavy dependencies. SQLite FTS5 is enough for phase 1.
Don't try to be clever with async. Phase 1 is single-threaded sync code.
Write tests against fixture JSONL files in tests/fixtures/, not against the real ~/.claude directory.

Phase 2 preview (don't build yet, but design to support)

watchdog-based file watcher subscribes to ~/.claude/projects/, picks up MODIFY events, reads from last_byte_offset to current EOF, indexes new lines.
FastAPI service exposes GET /search?q=...&limit=... returning the same JSON shape as --json.
Run as Windows Service (via pywin32 or nssm) and systemd user unit on Linux. Both should be documented in the README, not auto-installed.