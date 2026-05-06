"""HTTP API for claude-search.

Exposes the local FTS5 index over HTTP so the desktop UI can fan out
to this machine.

Auth model:

- When the server is bound to loopback (``127.0.0.1`` / ``::1``), no
  auth is required — only this machine's processes can reach it. The
  desktop UI uses this path.
- When bound to a non-loopback address, a Bearer token issued via the
  invite-redeem flow is required for ``/v1/search``, ``/v1/recent``,
  and ``/v1/status``.
- ``/v1/auth/redeem`` is always public (it's how a fresh client gets
  its first token).
- ``/v1/auth/issue``, ``/v1/auth/clients``, ``/v1/auth/revoke`` are
  always loopback-only — they're operator endpoints driven by the UI
  on the same machine.

TLS isn't here yet — when the bind is non-loopback, traffic is plain
HTTP. Run it over Tailscale, a VPN, or an SSH tunnel. TLS comes back
when the UI is packaged as a desktop app that can pin self-signed
fingerprints.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from . import auth as auth_mod
from .index import Index
from .ingest import ingest_all
from .parser import DEFAULT_QUERY_KINDS
from .paths import default_db_path, default_projects_dir
from .watcher import SessionWatcher

logger = logging.getLogger(__name__)


# ── Request / response models ──────────────────────────────────────────


class SearchRequest(BaseModel):
    q: str = Field(..., description="FTS5 query string")
    limit: int = Field(20, ge=1, le=200)
    kinds: list[str] | None = Field(
        default=None,
        description=(
            "Allowed message kinds. Defaults to user_typed + assistant_prose. "
            "Pass an empty list to mean 'no filter'."
        ),
    )
    project_paths: list[str] | None = Field(
        default=None,
        description=(
            "Restrict hits to messages whose cwd matches one of these paths. "
            "None or empty means no filter."
        ),
    )


class ProjectOut(BaseModel):
    project_path: str
    messages: int


class SearchHitOut(BaseModel):
    message_id: str
    session_id: str
    project_path: str
    timestamp: str
    role: str
    kind: str
    snippet: str
    score: float


class StatusOut(BaseModel):
    version: str
    messages: int
    files: int
    last_indexed_at: str | None
    by_kind: dict[str, int]
    db_path: str


class IssueInviteRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=64)
    ttl_seconds: int | None = Field(None, ge=60, le=24 * 60 * 60)


class IssueInviteResponse(BaseModel):
    id: str
    invite_url: str
    expires_at: str
    label: str


class RedeemRequest(BaseModel):
    invite: str = Field(..., min_length=10)
    client_label: str = Field("client", min_length=1, max_length=64)


class RedeemResponse(BaseModel):
    token: str
    server_label: str
    version: str


class TokenOut(BaseModel):
    id: str
    kind: str
    label: str | None
    created_at: str
    last_used_at: str | None
    expires_at: str | None
    last_ip: str | None


class WhoAmIOut(BaseModel):
    label: str | None
    kind: str


# ── Lifespan: open index, start watcher, close on shutdown ─────────────


def _is_loopback(host: str) -> bool:
    """Return True if `host` is a loopback address.

    Treats names ('localhost') as loopback. Falls back to False on any
    parse error so a misconfigured host string doesn't accidentally
    disable auth.
    """
    if not host:
        return False
    if host in ("localhost", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def make_app(
    *,
    db_path: Path | None = None,
    projects_dir: Path | None = None,
    enable_watcher: bool = True,
    cors_origins: list[str] | None = None,
    host: str = "127.0.0.1",
) -> FastAPI:
    """Build the FastAPI app. Factored so tests can substitute paths."""

    db = db_path or default_db_path()
    projects = projects_dir or default_projects_dir()
    bind_is_loopback = _is_loopback(host)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        index = Index(db)
        index.open()
        # SQLite + threads: serialise writes through this lock. Reads can
        # take it too — they're cheap and we don't have a hot-path latency
        # constraint at phase-2 scale.
        write_lock = threading.Lock()

        # Backfill on startup so the index is current when the API is up.
        if projects.exists():
            with write_lock:
                with index.transaction():
                    pass  # ensure connection is alive
                stats = ingest_all(index, projects, incremental=True)
            logger.info(
                "startup ingest: %d new msgs from %d/%d files",
                stats.messages_inserted,
                stats.files_changed,
                stats.files_seen,
            )
        else:
            logger.warning("projects dir %s does not exist", projects)

        watcher: SessionWatcher | None = None
        if enable_watcher and projects.exists():
            watcher = SessionWatcher(
                index=index,
                projects_dir=projects,
                write_lock=write_lock,
            )
            watcher.start()
            logger.info("watcher started on %s", projects)

        # Periodically purge expired invites so the tokens table doesn't
        # accumulate stale rows.
        with write_lock:
            auth_mod.purge_expired_invites(index)

        app.state.index = index
        app.state.write_lock = write_lock
        app.state.projects_dir = projects
        app.state.db_path = db
        app.state.watcher = watcher
        app.state.bind_is_loopback = bind_is_loopback
        app.state.bind_host = host

        try:
            yield
        finally:
            if watcher is not None:
                watcher.stop()
            index.close()

    app = FastAPI(
        title="claude-search",
        version=__version__,
        lifespan=lifespan,
    )

    # The desktop UI runs from a static-file dev server (default 5173),
    # the service runs on its own port — different origins. Open CORS for
    # localhost in any port so the UX of "open the prototype anywhere"
    # works. This is local-only; cross-machine traffic comes through a
    # different code path with auth and TLS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or [],
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # ── Auth dependencies ──────────────────────────────────────────────

    def get_index(request: Request) -> Index:
        return request.app.state.index

    def _client_is_loopback(request: Request) -> bool:
        client = request.client
        if client is None:
            return False
        return _is_loopback(client.host)

    def require_bearer_if_remote(request: Request) -> None:
        """Allow when bind is loopback OR the request comes from loopback.

        This way `serve --host 0.0.0.0` still lets the local UI call the
        operator endpoints without a token (since the request originates
        from this machine), but anything from the network needs auth.
        """
        if request.app.state.bind_is_loopback or _client_is_loopback(request):
            # Even loopback callers benefit from a bearer if they have one
            # (tests assume it's accepted). We just don't *require* it.
            header = request.headers.get("authorization", "")
            if header.lower().startswith("bearer "):
                _check_bearer(request, header)
            return
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="bearer token required")
        _check_bearer(request, header)

    def require_loopback(request: Request) -> None:
        """Operator endpoints. Refuse anything not from this machine.

        When the server is bound to loopback every connection is local by
        definition — accept anything. Otherwise, the client peer must
        itself be loopback.
        """
        if request.app.state.bind_is_loopback:
            return
        if not _client_is_loopback(request):
            raise HTTPException(
                status_code=403,
                detail="operator endpoint — only available to local processes",
            )

    def _check_bearer(request: Request, header: str) -> None:
        plain = header.split(" ", 1)[1].strip()
        index: Index = request.app.state.index
        write_lock: threading.Lock = request.app.state.write_lock
        ip = request.client.host if request.client else None
        with write_lock:
            row = auth_mod.authenticate_bearer(index, plain, ip=ip)
        if row is None:
            raise HTTPException(status_code=401, detail="invalid or revoked token")
        request.state.token = row

    # ── Routes ─────────────────────────────────────────────────────────

    @app.get("/v1/status", response_model=StatusOut, dependencies=[Depends(require_bearer_if_remote)])
    def status_route(request: Request, index: Index = Depends(get_index)) -> StatusOut:
        return StatusOut(
            version=__version__,
            messages=index.message_count(),
            files=index.file_count(),
            last_indexed_at=index.last_indexed_at(),
            by_kind=index.kind_counts(),
            db_path=str(request.app.state.db_path),
        )

    @app.get(
        "/v1/recent",
        response_model=list[SearchHitOut],
        dependencies=[Depends(require_bearer_if_remote)],
    )
    def recent_route(
        limit: int = 50,
        index: Index = Depends(get_index),
    ) -> list[SearchHitOut]:
        if limit < 1 or limit > 200:
            raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
        hits = index.recent(limit=limit, kinds=DEFAULT_QUERY_KINDS)
        return [
            SearchHitOut(
                message_id=h.message_id,
                session_id=h.session_id,
                project_path=h.project_path,
                timestamp=h.timestamp,
                role=h.role,
                kind=h.kind,
                snippet=h.snippet,
                score=h.score,
            )
            for h in hits
        ]

    @app.get(
        "/v1/projects",
        response_model=list[ProjectOut],
        dependencies=[Depends(require_bearer_if_remote)],
    )
    def projects_route(index: Index = Depends(get_index)) -> list[ProjectOut]:
        return [
            ProjectOut(project_path=p, messages=n)
            for p, n in index.list_projects()
        ]

    @app.get(
        "/v1/session/{session_id}",
        response_model=list[SearchHitOut],
        dependencies=[Depends(require_bearer_if_remote)],
    )
    def session_route(
        session_id: str,
        limit: int = 5000,
        index: Index = Depends(get_index),
    ) -> list[SearchHitOut]:
        if limit < 1 or limit > 10_000:
            raise HTTPException(status_code=400, detail="limit must be between 1 and 10000")
        hits = index.session_messages(session_id, limit=limit)
        return [
            SearchHitOut(
                message_id=h.message_id,
                session_id=h.session_id,
                project_path=h.project_path,
                timestamp=h.timestamp,
                role=h.role,
                kind=h.kind,
                snippet=h.snippet,
                score=h.score,
            )
            for h in hits
        ]

    @app.get(
        "/v1/message/{message_id}",
        response_model=SearchHitOut,
        dependencies=[Depends(require_bearer_if_remote)],
    )
    def message_route(
        message_id: str, index: Index = Depends(get_index)
    ) -> SearchHitOut:
        hit = index.get_message(message_id)
        if hit is None:
            raise HTTPException(status_code=404, detail="message not found")
        return SearchHitOut(
            message_id=hit.message_id,
            session_id=hit.session_id,
            project_path=hit.project_path,
            timestamp=hit.timestamp,
            role=hit.role,
            kind=hit.kind,
            snippet=hit.snippet,  # full content
            score=hit.score,
        )

    @app.post(
        "/v1/search",
        response_model=list[SearchHitOut],
        dependencies=[Depends(require_bearer_if_remote)],
    )
    def search_route(
        body: SearchRequest, index: Index = Depends(get_index)
    ) -> list[SearchHitOut]:
        if body.kinds is None:
            kinds: frozenset[str] | None = DEFAULT_QUERY_KINDS
        elif len(body.kinds) == 0:
            kinds = None  # explicit "all kinds"
        else:
            kinds = frozenset(body.kinds)
        try:
            hits = index.search(
                body.q,
                limit=body.limit,
                kinds=kinds,
                project_paths=body.project_paths or None,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return [
            SearchHitOut(
                message_id=h.message_id,
                session_id=h.session_id,
                project_path=h.project_path,
                timestamp=h.timestamp,
                role=h.role,
                kind=h.kind,
                snippet=h.snippet,
                score=h.score,
            )
            for h in hits
        ]

    # ── Auth endpoints ─────────────────────────────────────────────────

    @app.post("/v1/auth/redeem", response_model=RedeemResponse)
    def redeem_route(body: RedeemRequest, request: Request) -> RedeemResponse:
        """Public endpoint. Burns an invite, returns a long-lived bearer.

        Always available (even on remote binds) — that's how a fresh
        client gets its first credential.
        """
        index: Index = request.app.state.index
        write_lock: threading.Lock = request.app.state.write_lock
        try:
            with write_lock:
                issued = auth_mod.redeem_invite(
                    index, invite_token=body.invite, client_label=body.client_label
                )
        except auth_mod.InviteNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except auth_mod.InviteExpired as e:
            raise HTTPException(status_code=410, detail=str(e)) from e
        except auth_mod.InviteAlreadyUsed as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return RedeemResponse(
            token=issued.token,
            server_label=issued.label,
            version=__version__,
        )

    @app.get(
        "/v1/auth/whoami",
        response_model=WhoAmIOut,
        dependencies=[Depends(require_bearer_if_remote)],
    )
    def whoami_route(request: Request) -> WhoAmIOut:
        token = getattr(request.state, "token", None)
        if token is None:
            return WhoAmIOut(label=None, kind="local")
        return WhoAmIOut(label=token.label, kind=token.kind)

    @app.post(
        "/v1/auth/issue",
        response_model=IssueInviteResponse,
        dependencies=[Depends(require_loopback)],
    )
    def issue_invite_route(body: IssueInviteRequest, request: Request) -> IssueInviteResponse:
        """Operator endpoint: mint an invite. Loopback-only."""
        index: Index = request.app.state.index
        write_lock: threading.Lock = request.app.state.write_lock
        host = request.app.state.bind_host
        port = request.url.port or 8765
        ttl = body.ttl_seconds or auth_mod.INVITE_TTL_SECONDS
        with write_lock:
            issued = auth_mod.mint_invite(index, label=body.label, ttl_seconds=ttl)
        return IssueInviteResponse(
            id=issued.id,
            invite_url=auth_mod.build_invite_url(host=host, port=port, invite_token=issued.token),
            expires_at=issued.expires_at,
            label=issued.label,
        )

    @app.get(
        "/v1/auth/clients",
        response_model=list[TokenOut],
        dependencies=[Depends(require_loopback)],
    )
    def list_clients_route(request: Request) -> list[TokenOut]:
        index: Index = request.app.state.index
        rows = auth_mod.list_tokens(index)
        return [
            TokenOut(
                id=r.id,
                kind=r.kind,
                label=r.label,
                created_at=r.created_at,
                last_used_at=r.last_used_at,
                expires_at=r.expires_at,
                last_ip=r.last_ip,
            )
            for r in rows
        ]

    @app.post(
        "/v1/auth/revoke/{token_id}",
        dependencies=[Depends(require_loopback)],
    )
    def revoke_route(token_id: str, request: Request) -> dict:
        index: Index = request.app.state.index
        write_lock: threading.Lock = request.app.state.write_lock
        with write_lock:
            ok = auth_mod.revoke_token(index, token_id)
        if not ok:
            raise HTTPException(status_code=404, detail="token not found")
        return {"revoked": token_id}

    # ── Static UI ──────────────────────────────────────────────────────
    # Mount the bundled web assets at /. API routes are registered above
    # this so /v1/* always wins; everything else falls through to the
    # static directory. html=True makes / serve index.html and unknown
    # paths fall back to it (so that /settings.html works even though
    # there's no SPA router).
    try:
        from importlib.resources import files as _resource_files
        web_dir = _resource_files("claude_search").joinpath("web")
        web_path = Path(str(web_dir))
        if web_path.is_dir():
            app.mount("/", StaticFiles(directory=web_path, html=True), name="web")
        else:
            logger.warning("bundled web directory not found at %s", web_path)
    except Exception as e:
        logger.warning("failed to mount static UI: %s", e)

    return app
