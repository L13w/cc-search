"""`claude-search` CLI entry point."""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import click

from . import __version__
from . import auth as auth_mod
from .index import Index, SearchHit
from .ingest import ingest_all
from .parser import DEFAULT_QUERY_KINDS
from .paths import default_db_path, default_projects_dir


def _setup_logging(verbose: bool, log_file: Path | None) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = []
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        # Also redirect raw stdout/stderr to the log file. Two reasons:
        #   1. pythonw.exe (Windows scheduled task) leaves sys.stderr
        #      as None — uvicorn's startup banner crashes the process
        #      when it tries to write to it.
        #   2. uvicorn configures its own loggers with a StreamHandler
        #      pointing at sys.stderr; redirecting once here means
        #      everything ends up in the log file without touching
        #      uvicorn's log_config.
        try:
            fh = open(log_file, "a", encoding="utf-8", buffering=1)
            sys.stdout = fh
            sys.stderr = fh
        except OSError:
            pass
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    else:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )


@click.group()
@click.version_option(__version__, prog_name="claude-search")
@click.option(
    "--db-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to the SQLite index. Defaults to the per-user data dir.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.option(
    "--log-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write logs to this file instead of stderr.",
)
@click.pass_context
def main(ctx: click.Context, db_path: Path | None, verbose: bool, log_file: Path | None) -> None:
    """Local full-text search over Claude Code session history."""
    _setup_logging(verbose, log_file)
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path or default_db_path()


@main.command("index")
@click.option(
    "--projects-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Override the Claude Code projects directory.",
)
@click.option(
    "--incremental/--rebuild",
    default=True,
    help="Incremental (default) only reads new bytes per file. Rebuild drops and rebuilds the index.",
)
@click.pass_context
def cmd_index(ctx: click.Context, projects_dir: Path | None, incremental: bool) -> None:
    """Build or update the search index from local Claude Code sessions."""
    projects = projects_dir or default_projects_dir()
    db_path: Path = ctx.obj["db_path"]
    if not projects.exists():
        click.echo(f"projects directory not found: {projects}", err=True)
        sys.exit(1)

    started = time.monotonic()
    with Index(db_path) as index:
        if not incremental:
            click.echo("rebuilding index from scratch")
            index.reset()

        def _progress(path: Path, stats) -> None:
            click.echo(
                f"  {path.name}: {stats.messages_inserted} msgs, "
                f"{stats.files_changed}/{stats.files_seen} files changed",
                err=True,
            )

        stats = ingest_all(
            index,
            projects,
            incremental=incremental,
            progress_cb=_progress if ctx.obj.get("verbose") else None,
        )

    elapsed = time.monotonic() - started
    click.echo(
        f"indexed {stats.messages_inserted} new messages from "
        f"{stats.files_changed}/{stats.files_seen} file(s) in {elapsed:.2f}s "
        f"(read {stats.bytes_read:,} bytes)"
    )
    click.echo(f"db: {db_path}")


@main.command("query")
@click.argument("query_text", nargs=-1, required=True)
@click.option("--limit", type=int, default=20, show_default=True, help="Max results.")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON instead of human-readable text.")
@click.option(
    "--all",
    "include_all",
    is_flag=True,
    help="Include tool calls, tool output, and image messages in results. "
    "By default the search is limited to typed prompts and assistant prose.",
)
@click.pass_context
def cmd_query(
    ctx: click.Context,
    query_text: tuple[str, ...],
    limit: int,
    json_output: bool,
    include_all: bool,
) -> None:
    """Search the index. Quote multi-word phrases or use FTS5 syntax."""
    db_path: Path = ctx.obj["db_path"]
    if not db_path.exists():
        click.echo(
            f"index not found at {db_path}; run `claude-search index` first.",
            err=True,
        )
        sys.exit(1)

    q = " ".join(query_text).strip()
    kinds = None if include_all else DEFAULT_QUERY_KINDS
    with Index(db_path) as index:
        try:
            hits = index.search(q, limit=limit, kinds=kinds)
        except ValueError as e:
            click.echo(str(e), err=True)
            sys.exit(2)

    if json_output:
        click.echo(json.dumps([_hit_to_dict(h) for h in hits], indent=2))
        return

    if not hits:
        click.echo("(no results)")
        return

    for h in hits:
        project = _short_project(h.project_path)
        ts = h.timestamp[:16].replace("T", " ") if h.timestamp else "(no ts)"
        click.echo(f"{ts}  [{project}]  {h.role}")
        click.echo(f"  > {h.snippet}")
        click.echo("")


@main.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", type=int, default=8765, show_default=True, help="Bind port.")
@click.option(
    "--projects-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Override the Claude Code projects directory.",
)
@click.option(
    "--no-watcher",
    is_flag=True,
    help="Skip the background file watcher (manual reindex only).",
)
@click.pass_context
def cmd_serve(
    ctx: click.Context,
    host: str,
    port: int,
    projects_dir: Path | None,
    no_watcher: bool,
) -> None:
    """Run the local HTTP API. Localhost-only by default."""
    import uvicorn

    from .server import make_app

    db_path: Path = ctx.obj["db_path"]
    projects = projects_dir or default_projects_dir()
    app = make_app(
        db_path=db_path,
        projects_dir=projects,
        enable_watcher=not no_watcher,
        host=host,
    )
    from .server import _is_loopback
    auth_mode = "off (loopback bind)" if _is_loopback(host) else "bearer required"
    click.echo(f"claude-search serve · http://{host}:{port}", err=True)
    click.echo(f"  db:       {db_path}", err=True)
    click.echo(f"  projects: {projects}", err=True)
    click.echo(f"  watcher:  {'off' if no_watcher else 'on'}", err=True)
    click.echo(f"  auth:     {auth_mode}", err=True)
    if not _is_loopback(host):
        click.echo(
            "  ⚠ plain HTTP across the network — run over Tailscale, VPN, or SSH tunnel.",
            err=True,
        )
    uvicorn.run(app, host=host, port=port, log_level="info")


@main.command("status")
@click.pass_context
def cmd_status(ctx: click.Context) -> None:
    """Show index size, message count, and last ingest time."""
    db_path: Path = ctx.obj["db_path"]
    if not db_path.exists():
        click.echo(f"index not found at {db_path}")
        sys.exit(1)
    size = db_path.stat().st_size
    with Index(db_path) as index:
        n_msgs = index.message_count()
        n_files = index.file_count()
        last = index.last_indexed_at()
        kinds = index.kind_counts()
    click.echo(f"db:           {db_path}")
    click.echo(f"size:         {size:,} bytes")
    click.echo(f"messages:     {n_msgs:,}")
    click.echo(f"files:        {n_files:,}")
    click.echo(f"last ingest:  {last or '(never)'}")
    if kinds:
        click.echo("by kind:")
        for kind in sorted(kinds, key=lambda k: -kinds[k]):
            click.echo(f"  {kind:<18s} {kinds[kind]:>10,}")


# ── auth subcommands ──────────────────────────────────────────────────


@main.group("auth")
def cmd_auth() -> None:
    """Manage tokens issued by this server."""


def _detect_lan_ip() -> str:
    """Best-effort LAN IP discovery for embedding in invite URLs.

    Falls back to the loopback address if nothing better is available.
    """
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Doesn't actually send anything; just picks the outbound iface.
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


@cmd_auth.command("invite")
@click.option("--label", default="client", show_default=True, help="What you'll call the connecting machine.")
@click.option("--host", default=None, help="Host to embed in the invite URL. Auto-detects the LAN IP if omitted.")
@click.option("--port", type=int, default=8765, show_default=True, help="Port the server listens on.")
@click.option("--ttl", type=int, default=auth_mod.INVITE_TTL_SECONDS, show_default=True, help="Invite lifetime in seconds.")
@click.option("--copy", is_flag=True, help="Also copy the invite URL to the system clipboard if possible.")
@click.pass_context
def cmd_auth_invite(
    ctx: click.Context,
    label: str,
    host: str | None,
    port: int,
    ttl: int,
    copy: bool,
) -> None:
    """Mint a single-use invite URL the other machine can paste."""
    db_path: Path = ctx.obj["db_path"]
    if not db_path.exists():
        click.echo(f"index not found at {db_path}; run `claude-search index` first.", err=True)
        sys.exit(1)
    embed_host = host or _detect_lan_ip()
    with Index(db_path) as index:
        issued = auth_mod.mint_invite(index, label=label, ttl_seconds=ttl)
    url = auth_mod.build_invite_url(host=embed_host, port=port, invite_token=issued.token)
    click.echo(url)
    click.echo(f"  id:         {issued.id}", err=True)
    click.echo(f"  label:      {issued.label}", err=True)
    click.echo(f"  expires:    {issued.expires_at}", err=True)
    if copy:
        if _copy_to_clipboard(url):
            click.echo("  copied to clipboard.", err=True)
        else:
            click.echo("  (clipboard unavailable; URL printed above)", err=True)


def _copy_to_clipboard(text: str) -> bool:
    """Best-effort clipboard copy. Returns True on success."""
    import shutil
    import subprocess

    candidates: list[list[str]] = []
    if shutil.which("wl-copy"):
        candidates.append(["wl-copy"])
    if shutil.which("xclip"):
        candidates.append(["xclip", "-selection", "clipboard"])
    if shutil.which("xsel"):
        candidates.append(["xsel", "--clipboard", "--input"])
    if shutil.which("pbcopy"):
        candidates.append(["pbcopy"])
    if shutil.which("clip"):  # Windows
        candidates.append(["clip"])
    for cmd in candidates:
        try:
            p = subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=2)
            if p.returncode == 0:
                return True
        except (subprocess.SubprocessError, OSError):
            continue
    return False


@cmd_auth.command("list")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON.")
@click.pass_context
def cmd_auth_list(ctx: click.Context, json_output: bool) -> None:
    """List active tokens (invites and bearers)."""
    db_path: Path = ctx.obj["db_path"]
    if not db_path.exists():
        click.echo(f"index not found at {db_path}", err=True)
        sys.exit(1)
    with Index(db_path) as index:
        auth_mod.purge_expired_invites(index)
        rows = auth_mod.list_tokens(index)
    if json_output:
        click.echo(json.dumps([
            {
                "id": r.id, "kind": r.kind, "label": r.label,
                "created_at": r.created_at, "last_used_at": r.last_used_at,
                "expires_at": r.expires_at, "last_ip": r.last_ip,
            } for r in rows
        ], indent=2))
        return
    if not rows:
        click.echo("(no tokens)")
        return
    click.echo(f"{'ID':<14}{'KIND':<10}{'LABEL':<20}{'CREATED':<22}{'LAST USED':<22}{'IP':<16}")
    for r in rows:
        click.echo(
            f"{r.id:<14}{r.kind:<10}{(r.label or ''):<20}"
            f"{r.created_at[:19]:<22}{(r.last_used_at or '(never)')[:19]:<22}"
            f"{(r.last_ip or '—'):<16}"
        )


@cmd_auth.command("revoke")
@click.argument("token_id")
@click.pass_context
def cmd_auth_revoke(ctx: click.Context, token_id: str) -> None:
    """Revoke a token by its short id (see `claude-search auth list`)."""
    db_path: Path = ctx.obj["db_path"]
    if not db_path.exists():
        click.echo(f"index not found at {db_path}", err=True)
        sys.exit(1)
    with Index(db_path) as index:
        ok = auth_mod.revoke_token(index, token_id)
    if not ok:
        click.echo(f"no token with id {token_id}", err=True)
        sys.exit(1)
    click.echo(f"revoked {token_id}")


def _hit_to_dict(h: SearchHit) -> dict:
    return {
        "message_id": h.message_id,
        "session_id": h.session_id,
        "project_path": h.project_path,
        "timestamp": h.timestamp,
        "role": h.role,
        "kind": h.kind,
        "snippet": h.snippet,
        "score": h.score,
    }


def _short_project(project_path: str) -> str:
    if not project_path:
        return "?"
    # Use the trailing path component, stripped of leading dashes from the
    # encoded form, so output stays readable.
    name = project_path.rstrip("/").split("/")[-1]
    return name or project_path


# ── service subcommands ──────────────────────────────────────────────


@main.group("service")
def cmd_service() -> None:
    """Install/manage claude-search as a background service."""


def _is_loopback_host(h: str) -> bool:
    return h in ("127.0.0.1", "localhost", "::1")


@cmd_service.command("install")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", type=int, default=8765, show_default=True, help="Bind port.")
@click.option("--no-bootstrap", is_flag=True, help="Skip the initial `claude-search index` run.")
@click.option(
    "--insecure-allow-remote",
    is_flag=True,
    help="Required when --host is non-loopback. Acknowledges that traffic is "
    "plain HTTP and should only be used over Tailscale, VPN, or SSH tunnel.",
)
@click.pass_context
def cmd_service_install(
    ctx: click.Context,
    host: str,
    port: int,
    no_bootstrap: bool,
    insecure_allow_remote: bool,
) -> None:
    """Install + start the claude-search background service.

    Idempotent — safe to re-run after `pipx install --force`.
    """
    from . import service as service_mod

    if not _is_loopback_host(host) and not insecure_allow_remote:
        click.echo(
            f"refusing to install on {host}: traffic is plain HTTP. "
            "Re-run with --insecure-allow-remote and run the service "
            "behind Tailscale, a VPN, or an SSH tunnel.",
            err=True,
        )
        sys.exit(2)

    # Port-collision probe — fail fast with a useful message. Use
    # SO_REUSEADDR to match uvicorn's behavior so we don't false-alarm
    # on a TIME-WAIT socket from a recent shutdown.
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host if host != "0.0.0.0" else "127.0.0.1", port))
    except OSError:
        click.echo(
            f"port {port} on {host} is already in use. "
            "Pick a different --port, or stop whatever is listening there.",
            err=True,
        )
        sys.exit(2)
    finally:
        s.close()

    # Bootstrap index synchronously so the UI is responsive on first hit.
    if not no_bootstrap:
        click.echo("indexing existing sessions before starting the service…", err=True)
        db_path: Path = ctx.obj["db_path"]
        projects = default_projects_dir()
        if projects.exists():
            with Index(db_path) as index:
                stats = ingest_all(index, projects, incremental=True)
            click.echo(
                f"  +{stats.messages_inserted} messages from "
                f"{stats.files_changed}/{stats.files_seen} files",
                err=True,
            )
        else:
            click.echo(f"  (no projects dir at {projects}; skipping)", err=True)

    try:
        hints = service_mod.install(
            service_mod.ServiceConfig(host=host, port=port)
        )
    except service_mod.UnsupportedPlatform as e:
        click.echo(str(e), err=True)
        sys.exit(2)
    except Exception as e:
        click.echo(f"install failed: {e}", err=True)
        sys.exit(1)
    for line in hints:
        click.echo(line)


@cmd_service.command("uninstall")
@click.option("--purge", is_flag=True, help="Also delete the index DB and tokens.")
@click.pass_context
def cmd_service_uninstall(ctx: click.Context, purge: bool) -> None:
    """Stop and remove the service. Leaves user data alone unless --purge."""
    from . import service as service_mod

    try:
        service_mod.uninstall()
    except service_mod.UnsupportedPlatform as e:
        click.echo(str(e), err=True)
        sys.exit(2)

    if purge:
        db_path: Path = ctx.obj["db_path"]
        for ext in ("", "-wal", "-shm", "-journal"):
            p = Path(str(db_path) + ext)
            if p.exists():
                p.unlink()
                click.echo(f"removed {p}", err=True)
    click.echo("service uninstalled.")


@cmd_service.command("status")
def cmd_service_status() -> None:
    """Show whether the service is installed and running."""
    from . import service as service_mod

    try:
        s = service_mod.status()
    except service_mod.UnsupportedPlatform as e:
        click.echo(str(e), err=True)
        sys.exit(2)
    state = (
        "running" if s.running
        else ("installed (not running)" if s.installed else "not installed")
    )
    click.echo(f"state: {state}")
    if s.detail:
        click.echo("")
        click.echo(s.detail)


@cmd_service.command("start")
def cmd_service_start() -> None:
    """Start the service."""
    from . import service as service_mod
    service_mod.start()
    click.echo("started.")


@cmd_service.command("stop")
def cmd_service_stop() -> None:
    """Stop the service."""
    from . import service as service_mod
    service_mod.stop()
    click.echo("stopped.")


@cmd_service.command("restart")
def cmd_service_restart() -> None:
    """Restart the service. Use this after `pipx install --force` to load new code."""
    from . import service as service_mod
    service_mod.restart()
    click.echo("restarted.")


@cmd_service.command("logs")
@click.option("-f", "--follow", is_flag=True, help="Follow new log lines (Ctrl+C to exit).")
@click.option("-n", "--lines", type=int, default=200, show_default=True, help="Tail this many lines.")
def cmd_service_logs(follow: bool, lines: int) -> None:
    """Show recent service logs."""
    from . import service as service_mod
    try:
        for line in service_mod.tail_logs(follow=follow, lines=lines):
            click.echo(line)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
