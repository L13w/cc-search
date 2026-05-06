# claude-search

Local full-text search over Claude Code session history. Cross-platform (Windows + Linux). Python only.

## What this project is

A SQLite FTS5 index over `~/.claude/projects/` JSONL session files, queried via a CLI. Phase 1 is one-shot index + query. Phase 2 will add a file watcher and HTTP API running as a background service.

See `docs/BRIEF.md` for the full phase 1 spec and acceptance criteria.

## Working agreements

### Before writing parser code
Always run `head -n 5` on a real session file under `~/.claude/projects/<some-project>/<session>.jsonl` and show the output before assuming any schema. The JSONL format is undocumented and changes between Claude Code versions. Don't guess field names.

### Cross-platform discipline
This runs on Windows 11 and Ubuntu. Non-negotiable rules:
- `pathlib.Path` for all file paths. No string concatenation, no `os.path.join` for new code.
- Never hardcode `/` or `\` separators.
- Use `platformdirs` for user data, cache, and config locations. Never hardcode `~/.local/share` or `%APPDATA%`.
- Test on Linux at minimum before declaring something done. The Ubuntu VM is the dev target; the Windows laptop is the second target.
- Line endings: open text files with explicit `encoding="utf-8"`. Don't rely on platform defaults.

### Defensive parsing
The session JSONL is third-party data we don't control:
- Skip lines that don't end in `\n` (file may be mid-write). Buffer the partial line and let the next read retry.
- Wrap each line's `json.loads` in try/except. Log a warning with file path and line number, then continue. Never crash the whole ingest because of one bad line.
- Missing fields are normal. Use `.get()` with defaults. Don't assume any field exists.
- Truncate any single content field to 10,000 chars before indexing. Long tool outputs are noise.

### Dependencies
Keep them minimal. Phase 1 allowed list:
- `click` or `typer` (pick one, don't import both)
- `platformdirs`
- Standard library only otherwise (`sqlite3`, `pathlib`, `json`, `logging`)

Don't add a dependency without asking. No `pandas`, no `pydantic`, no async frameworks for phase 1.

### No async in phase 1
Single-threaded sync code. Phase 2 may introduce async for the FastAPI layer, but the indexer and parser stay sync. Async + SQLite + file watching is a footgun and we don't need the throughput.

### Testing
- Tests run against fixture JSONL files in `tests/fixtures/`, never against the real `~/.claude` directory.
- Fixture files should cover: normal messages, tool calls, tool results, malformed lines, partial last line, empty file, unicode content.
- Run `pytest` before declaring any change done. CI is just "does pytest pass on Ubuntu."

### Index lifecycle
- The DB lives at `platformdirs.user_data_dir("claude-search")`. Don't put it in the project directory.
- Schema migrations: if the FTS5 schema changes, bump a `schema_version` in a metadata table and rebuild from scratch on mismatch. Don't try to migrate FTS5 in place.
- `ingest_state` table tracks per-file byte offset. Phase 2's watcher reads this to know where to resume. Keep this contract stable.

### File handling rule (project-wide)
Never delete a file without renaming it to `DELETE-<original-name>` first and flagging in the response. This applies to source files, fixtures, and any generated output. Applies regardless of file type.

### Commit hygiene
- Small commits. One concept per commit.
- Commit messages: imperative mood, lowercase, no period. Example: `add fts5 schema and ingest_state table`.
- Don't commit the SQLite DB, `.venv/`, `__pycache__/`, or anything under `tests/fixtures/real/` (real session data should never enter the repo).

## Quick reference

### Run locally (after `pip install -e .`)
claude-search index                    # full reindex
claude-search index --incremental      # default
claude-search query "ZK proof"
claude-search query "ZK proof" --json
claude-search status

### Tests
pytest                                  # all tests
pytest tests/test_parser.py -v          # one file

### Where things live
- Index DB: `platformdirs.user_data_dir("claude-search")/index.sqlite`
- Logs: stderr by default, `--log-file` to redirect
- Source sessions: `~/.claude/projects/` (Linux) or `%USERPROFILE%\.claude\projects\` (Windows)

## Things to ask the human about, not decide unilaterally

- Adding a new runtime dependency
- Schema changes to the FTS5 table or `ingest_state`
- Anything that would break the phase 2 watcher contract
- Deleting files (use `DELETE-` prefix instead and flag it)
- Changes to the CLI surface defined in BRIEF.md

## Out of scope for phase 1

Don't build these even if they seem easy:
- File watcher (phase 2)
- HTTP API (phase 2)
- Windows Service / systemd unit (phase 2)
- Conversation context expansion in query results
- Project / date / role filters
- Web frontend
- Embedding-based semantic search