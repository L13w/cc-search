"""Token-based auth for the claude-search service.

Two token kinds:

- ``invite``: single-use, short-lived (15 min default). Created by the
  operator on the server side and shared with a client.
- ``bearer``: long-lived, returned to a client after redeeming an
  invite. Sent on every API call as ``Authorization: Bearer <token>``.

Tokens are 192-bit URL-safe random strings. We store SHA-256 hashes of
them, never the plaintext, so a DB leak can't replay an active token.
The plaintext is shown to the user exactly once at issue time.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .index import Index

INVITE_TTL_SECONDS = 15 * 60  # 15 minutes
TOKEN_BYTES = 24  # 192 bits → ~32 url-safe chars


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return secrets.token_urlsafe(8)  # 12-char short id, e.g. "k7p2-w3rt"


def hash_token(plain: str) -> str:
    """Hash a plaintext token for at-rest storage."""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def generate_token() -> str:
    """Generate a fresh plaintext token (URL-safe, ~32 chars)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


@dataclass(frozen=True)
class IssuedInvite:
    id: str
    token: str  # plaintext — shown once, never stored
    label: str
    expires_at: str


@dataclass(frozen=True)
class IssuedBearer:
    id: str
    token: str  # plaintext — shown once
    label: str


@dataclass(frozen=True)
class TokenRow:
    id: str
    kind: str
    label: str | None
    created_at: str
    last_used_at: str | None
    expires_at: str | None
    last_ip: str | None


# ── invite minting ────────────────────────────────────────────────────


def mint_invite(index: Index, *, label: str, ttl_seconds: int = INVITE_TTL_SECONDS) -> IssuedInvite:
    plain = generate_token()
    tid = _new_id()
    expires = (_now_dt() + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
    index.conn.execute(
        """
        INSERT INTO tokens(id, token_hash, kind, label, created_at, expires_at)
        VALUES (?, ?, 'invite', ?, ?, ?)
        """,
        (tid, hash_token(plain), label, _now(), expires),
    )
    return IssuedInvite(id=tid, token=plain, label=label, expires_at=expires)


# ── invite redemption → bearer ────────────────────────────────────────


class InviteError(Exception):
    """Base class for redemption failures."""


class InviteNotFound(InviteError):
    pass


class InviteExpired(InviteError):
    pass


class InviteAlreadyUsed(InviteError):
    pass


def redeem_invite(
    index: Index,
    *,
    invite_token: str,
    client_label: str,
) -> IssuedBearer:
    """Burn an invite and return a fresh long-lived bearer token.

    Raises InviteNotFound, InviteExpired, or InviteAlreadyUsed.
    """
    h = hash_token(invite_token)
    row = index.conn.execute(
        "SELECT id, kind, label, expires_at FROM tokens WHERE token_hash = ?",
        (h,),
    ).fetchone()
    if row is None:
        raise InviteNotFound("invite not recognised")
    if row["kind"] != "invite":
        # Reusing a bearer as an invite is nonsense; treat as "not an invite".
        raise InviteNotFound("not an invite token")
    if row["expires_at"]:
        try:
            exp = datetime.fromisoformat(row["expires_at"])
        except ValueError:
            exp = None
        if exp is not None and _now_dt() > exp:
            # Burn it anyway so a re-attempt sees AlreadyUsed instead of leaking
            # whether it had expired vs. been redeemed.
            index.conn.execute("DELETE FROM tokens WHERE id = ?", (row["id"],))
            raise InviteExpired("invite expired")

    # Burn the invite.
    deleted = index.conn.execute(
        "DELETE FROM tokens WHERE id = ?", (row["id"],)
    )
    if deleted.rowcount == 0:
        # Lost a race with another redeemer.
        raise InviteAlreadyUsed("invite already used")

    plain = generate_token()
    bid = _new_id()
    label = client_label or row["label"] or "client"
    index.conn.execute(
        """
        INSERT INTO tokens(id, token_hash, kind, label, created_at)
        VALUES (?, ?, 'bearer', ?, ?)
        """,
        (bid, hash_token(plain), label, _now()),
    )
    return IssuedBearer(id=bid, token=plain, label=label)


# ── bearer authentication ────────────────────────────────────────────


def authenticate_bearer(index: Index, plain: str, *, ip: str | None = None) -> TokenRow | None:
    """Look up a bearer token by its plaintext. Updates last_used_at on hit.

    Returns None on miss. Returns a TokenRow on hit. Invite tokens never
    authenticate as bearers — they have to be redeemed first.
    """
    if not plain:
        return None
    h = hash_token(plain)
    row = index.conn.execute(
        """
        SELECT id, kind, label, created_at, last_used_at, expires_at, last_ip
        FROM tokens
        WHERE token_hash = ? AND kind = 'bearer'
        """,
        (h,),
    ).fetchone()
    if row is None:
        return None
    index.conn.execute(
        "UPDATE tokens SET last_used_at = ?, last_ip = ? WHERE id = ?",
        (_now(), ip, row["id"]),
    )
    return TokenRow(
        id=row["id"],
        kind=row["kind"],
        label=row["label"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        expires_at=row["expires_at"],
        last_ip=row["last_ip"],
    )


# ── listing / revocation ─────────────────────────────────────────────


def list_tokens(index: Index, *, kind: str | None = None) -> list[TokenRow]:
    sql = """
        SELECT id, kind, label, created_at, last_used_at, expires_at, last_ip
        FROM tokens
    """
    args: tuple = ()
    if kind is not None:
        sql += " WHERE kind = ?"
        args = (kind,)
    sql += " ORDER BY created_at DESC"
    rows = index.conn.execute(sql, args).fetchall()
    return [
        TokenRow(
            id=r["id"],
            kind=r["kind"],
            label=r["label"],
            created_at=r["created_at"],
            last_used_at=r["last_used_at"],
            expires_at=r["expires_at"],
            last_ip=r["last_ip"],
        )
        for r in rows
    ]


def revoke_token(index: Index, token_id: str) -> bool:
    """Delete a token by its short id. Returns True if a row was removed."""
    cur = index.conn.execute("DELETE FROM tokens WHERE id = ?", (token_id,))
    return cur.rowcount > 0


def purge_expired_invites(index: Index) -> int:
    """Drop invites past their expires_at. Returns count removed."""
    cur = index.conn.execute(
        "DELETE FROM tokens WHERE kind = 'invite' AND expires_at IS NOT NULL AND expires_at < ?",
        (_now(),),
    )
    return cur.rowcount


# ── invite URL helpers ───────────────────────────────────────────────


def build_invite_url(*, host: str, port: int, invite_token: str) -> str:
    """Return the ``claude-search://join?…`` invite string.

    fp= is omitted in plain-HTTP mode. When TLS pinning ships we'll add
    fp=sha256:<cert-hash>.
    """
    return f"claude-search://join?host={host}&port={port}&invite={invite_token}"


def parse_invite_url(url: str) -> dict[str, str]:
    """Best-effort parse of a ``claude-search://join?…`` URL.

    Returns a dict with keys host, port, invite (and fp if present).
    Raises ValueError if the URL is malformed.
    """
    prefix = "claude-search://join?"
    if not url.startswith(prefix):
        raise ValueError("not a claude-search invite URL")
    qs = url[len(prefix):]
    out: dict[str, str] = {}
    for pair in qs.split("&"):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        out[k] = v
    for required in ("host", "port", "invite"):
        if required not in out:
            raise ValueError(f"invite URL missing '{required}'")
    return out
