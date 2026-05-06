# claude-search

Local full-text search over Claude Code session history. Indexes every message under your `~/.claude/projects/` JSONL files into a SQLite FTS5 database, exposes it as a small HTTP service, and ships with a browser UI that searches across every machine you've paired.

> [!NOTE]
> Your session data never leaves your machines. claude-search is local-only by default; cross-machine search runs over your private [Tailscale](https://tailscale.com) network. There's no cloud, no telemetry, and nothing to log into.

## Why

If you use Claude Code, you already have months of useful conversation buried in `~/.claude/projects/`. claude-search makes it searchable in milliseconds: every prompt you've ever typed, every reply, every tool call. Multi-machine setups (e.g. a desktop and a laptop) merge into one search bar.

## Features

- **Fast full-text search** over your entire Claude Code history (SQLite FTS5).
- **Browser UI** with a result drawer that opens the full session at the matched message.
- **Auto-indexing** — a background watcher picks up new sessions as Claude Code writes them.
- **Filters** by message kind (typed prose / tool calls / tool output) and by working directory.
- **Multi-machine fan-out** — search your laptop and your dev VM together; results merge by relevance.
- **Token-based pairing** — a one-line invite URL pairs machines without passwords.
- **No admin install** — service runs as your user (systemd `--user` on Linux, Scheduled Task on Windows).
- **Privacy by design** — data and tokens stay on your machines; cross-machine traffic rides Tailscale's WireGuard tunnels.

## Install

Requires Python 3.11+ and [pipx](https://pipx.pypa.io/).

If you don't have pipx:

```bash
# Linux (Ubuntu 22.04+ / Debian)
sudo apt install pipx && pipx ensurepath

# macOS
brew install pipx && pipx ensurepath

# Windows (PowerShell)
python -m pip install --user pipx
python -m pipx ensurepath
# close and reopen PowerShell so PATH refreshes
```

Then on each machine:

```bash
pipx install git+https://github.com/L13w/cc-search.git
claude-search service install
```

That installs the package into an isolated venv, registers a per-user background service, and starts it. Open `http://127.0.0.1:8765/` in your browser to verify — you should see a search UI showing your real message count.

To update later:

```bash
pipx install --force git+https://github.com/L13w/cc-search.git
claude-search service restart
```

## Use

The UI at `http://127.0.0.1:8765/` is the main interface:

- Type to search; results appear as you type with snippets highlighted in place.
- Click a result to open the conversation drawer; the matched message scrolls into view at the top third with the surrounding session above and below.
- Filter chips:
  - **Prose only / Include tools** — by default search hits only your typed prompts and Claude's prose replies. Toggle to include tool calls and tool output.
  - **Time range** — last 7d, last 30d, last 90d, all time.
  - **Projects** — pick one or more working directories from the dropdown. Same project on different machines shows up twice with the parent path so you can disambiguate.
- Keyboard shortcuts (the modifier symbol matches your OS — `⌘` on Mac, `⊞` on Windows, `⌃` on Linux):
  - `Mod`+`K` — focus the search bar from anywhere
  - `Mod`+`,` — open settings
  - `Esc` — close drawer / clear query
  - `?` — show the full shortcut list

The CLI mirrors the API for scripting:

```bash
claude-search query "kubernetes" --limit 50
claude-search query "rate limiting" --json | jq
claude-search status                    # message count, kinds, last ingest
```

## Add another machine (Tailscale)

Cross-machine search requires a working network path between machines. **Tailscale is the supported way for now** — it gives every machine a stable hostname (e.g. `laptop`, `dev-vm`) over an encrypted private network without you exposing any ports publicly.

### Setup (one-time per network)

1. Install Tailscale on each machine and sign them into the same tailnet — see [tailscale.com/download](https://tailscale.com/download).
2. Confirm MagicDNS resolves between them: `ping <other-machine-hostname>` should work from each.

### Pair the machines

On the **machine you want to search from** (e.g. your laptop), open `http://127.0.0.1:8765/`. You'll see your local index. To add the other machine:

1. **On the other machine** (e.g. your dev VM), reinstall the service to listen on Tailscale:

   ```bash
   claude-search service stop
   claude-search service install --host 0.0.0.0 --insecure-allow-remote
   ```

   The `--insecure-allow-remote` flag acknowledges that traffic is plain HTTP — Tailscale provides the encryption underneath. Don't use this on a service exposed to the open internet.

2. **On that same machine**, mint an invite, embedding the Tailscale hostname:

   ```bash
   claude-search auth invite --label laptop --host <tailscale-hostname>
   ```

   That prints a one-line `claude-search://join?...` URL. Copy it.

3. **On the original machine**, in the browser, click the gear icon → Remote servers → paste the invite URL into the "Paste invite" field. The card slides in and the status dot turns green within ~30s.

The search bar now fans out to local + every paired remote in parallel and merges results by relevance score. The status pill shows `<total> indexed · <reachable>/<total> servers`.

To search the other direction, mint an invite on the laptop and paste it into the VM's UI. Pairing is symmetric.

### Manage tokens

The `auth` group manages issued tokens:

```bash
claude-search auth list                 # show issued invites + bearers
claude-search auth invite --label X     # mint a 15-minute single-use invite
claude-search auth revoke <id>          # kill a token immediately
```

Operator endpoints (issuing invites, listing clients, revoking) are loopback-only — they cannot be invoked over the network.

## Service management

```bash
claude-search service status             # is it running?
claude-search service logs -f            # follow logs
claude-search service start | stop | restart
claude-search service uninstall          # leaves the index DB alone
claude-search service uninstall --purge  # also wipes the index + tokens
```

Under the hood:

- **Linux** — systemd `--user` unit at `~/.config/systemd/user/claude-search.service`. Logs go to journald. To survive logout/reboot, run `sudo loginctl enable-linger $USER` once (the install command flags this if needed).
- **Windows** — Scheduled Task `ClaudeSearch` triggered AtLogon, running via `pythonw.exe` (no console window). Logs go to a rotating file at `%LOCALAPPDATA%\claude-search\Logs\serve.log`.
- **macOS** — not yet packaged; you can run `claude-search serve` manually or wrap in a launchd plist.

## Where data lives

| Item | Linux / macOS | Windows |
| --- | --- | --- |
| Source sessions (read) | `~/.claude/projects/` | `%USERPROFILE%\.claude\projects\` |
| Index database | `~/.local/share/claude-search/index.sqlite` | `%LOCALAPPDATA%\claude-search\index.sqlite` |
| Logs | `journalctl --user -u claude-search` | `%LOCALAPPDATA%\claude-search\Logs\serve.log` |
| Tokens (server-side) | inside the index DB, hashed | inside the index DB, hashed |
| Tokens (browser-side) | `localStorage` of the page origin | same |

Source sessions are read-only; claude-search never modifies them.

### Retention: claude-search outlives Claude Code's cleanup

Claude Code's `cleanupPeriodDays` setting in `~/.claude/settings.json` defaults to **30 days** — older session JSONL files are deleted from `~/.claude/projects/` at next CLI startup. If you want a long history, raise it:

```json
{ "cleanupPeriodDays": 1000 }
```

Minimum is 1 day; there's no way to disable cleanup entirely.

claude-search's index is decoupled from source files once content is ingested — full message text lives in the SQLite FTS5 table, not just a reference. So anything claude-search has already indexed stays searchable and browseable in the drawer, even if the original JSONL gets cleaned up later. The watcher doesn't currently process delete events, which means `ingest_state` accumulates orphan rows over time — purely cosmetic, no functional impact.

> [!TIP]
> If you've already lost old sessions to the default 30-day cleanup, you can't recover them — but raise `cleanupPeriodDays` now and run claude-search before the next cleanup, and you'll have a durable searchable archive of everything from this point forward.

## Architecture in one paragraph

A FastAPI service on `127.0.0.1:8765` serves both the API (`/v1/*`) and the bundled web UI (`/`). A SQLite FTS5 index stores one row per message with `kind`, `role`, `project_path`, `session_id`, `timestamp`, and full content. A watchdog observer reindexes session files within ~1s of a write. Cross-machine pairing is a one-line invite URL → bearer token flow stored in the browser's `localStorage`. The search bar fan-out uses `Promise.allSettled` against local + every paired remote with a per-server timeout, then merges hits by FTS5's bm25 score.

## Search syntax

Queries are passed straight to FTS5, so its [query syntax](https://www.sqlite.org/fts5.html#full_text_query_syntax) works:

```
"exact phrase"           # quoted phrase
term*                    # prefix match
a AND b                  # both terms (default behavior with multiple terms)
a OR b                   # either term
a NOT b                  # exclude b
```

> [!WARNING]
> Hyphen is the NOT operator in FTS5, so `cargo zk-verifier` is interpreted as `cargo zk NOT verifier` and may error. Quote the phrase or replace the hyphen with a space.

## Limitations

- **No TLS yet.** Cross-machine traffic is plain HTTP; Tailscale provides the encryption. Don't use `--insecure-allow-remote` on a service exposed to the open internet. TLS pinning will return when the UI is packaged as a desktop app.
- **Tokens in browser localStorage.** Adequate for a single-user setup on a machine you control; not appropriate for a shared machine. A future desktop client will move them to the OS keychain.
- **macOS not packaged.** The Python stack works fine; only the `service install` shim is missing. Run `claude-search serve` manually or contribute a launchd backend.
- **No mobile UI.** Desktop browsers only.
- **Frontend uses CDN-loaded React.** First page load needs internet to fetch React, ReactDOM, and Babel-standalone from `unpkg.com`. After that the page is cached.

## Privacy & security model

- **Local first.** All indexing and search runs on your own machines. Nothing is sent to a server we control because there isn't one.
- **Tailscale + bearer tokens** for cross-machine. Tokens are 192-bit URL-safe random strings, stored hashed (SHA-256) on the server side. Plaintext is shown to the user exactly once at issue time.
- **No telemetry.** claude-search makes no outbound HTTP requests other than the ones you explicitly trigger (CDN fetches for React, API calls to your own paired machines).
- **Source data is read-only.** The watcher reads `~/.claude/projects/` but never writes there.

## Roadmap

- TLS pinning for cross-machine (with Tauri/Electron desktop client)
- mDNS auto-discovery on the local network
- Search-result attribution badges (which machine each hit came from)
- Per-server scoring weights and pinned-machines preferences
- macOS launchd packaging
- PyPI publishing once the surface is stable

## Development

```bash
git clone https://github.com/L13w/cc-search.git
cd claude-search
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest                                            # all tests, ~3s
claude-search index && claude-search serve        # foreground dev mode
```

The frontend at `src/claude_search/web/` is plain JSX + Babel-standalone — no build step. Edit, save, hard-refresh.

## Contributing

Issues and PRs welcome. The codebase is small (~3K LOC Python, ~2K LOC frontend) and well-tested. Useful starting points:

- **Add a backend endpoint:** see `src/claude_search/server.py` and add a matching test in `tests/test_server.py`.
- **Add a UI feature:** the panels live at `src/claude_search/web/{remote,service}-panel.jsx` and the search shell at `app.jsx` + `index.html`. Components rely on globals (`window.csAPI`, `window.SearchApp`, `window.AppParts`) — check them before adding new ones.
- **Port to macOS:** add `src/claude_search/service/_launchd.py` mirroring `_systemd.py` and dispatch from `service/__init__.py`.

## License

MIT.
