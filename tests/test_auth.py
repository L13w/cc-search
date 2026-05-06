"""Auth lifecycle tests at the module level (no HTTP)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from claude_search import auth as auth_mod
from claude_search.index import Index


@pytest.fixture
def index(tmp_path: Path) -> Index:
    idx = Index(tmp_path / "index.sqlite")
    idx.open()
    yield idx
    idx.close()


def test_mint_invite_persists_hashed_only(index: Index):
    issued = auth_mod.mint_invite(index, label="lewbook")
    assert issued.token  # plaintext returned to caller
    rows = auth_mod.list_tokens(index, kind="invite")
    assert len(rows) == 1
    # The plaintext is never stored.
    stored = index.conn.execute("SELECT token_hash FROM tokens").fetchone()
    assert stored["token_hash"] != issued.token
    assert stored["token_hash"] == auth_mod.hash_token(issued.token)


def test_redeem_burns_invite_and_returns_bearer(index: Index):
    issued = auth_mod.mint_invite(index, label="dev-vm")
    bearer = auth_mod.redeem_invite(index, invite_token=issued.token, client_label="lewbook")
    # The invite is gone, the bearer is here.
    rows = auth_mod.list_tokens(index)
    kinds = {r.kind for r in rows}
    assert kinds == {"bearer"}
    # Bearer authenticates.
    auth = auth_mod.authenticate_bearer(index, bearer.token)
    assert auth is not None
    assert auth.kind == "bearer"
    assert auth.label == "lewbook"


def test_redeem_twice_raises_already_used(index: Index):
    issued = auth_mod.mint_invite(index, label="x")
    auth_mod.redeem_invite(index, invite_token=issued.token, client_label="c")
    with pytest.raises(auth_mod.InviteNotFound):
        auth_mod.redeem_invite(index, invite_token=issued.token, client_label="c")


def test_redeem_wrong_token_raises_not_found(index: Index):
    with pytest.raises(auth_mod.InviteNotFound):
        auth_mod.redeem_invite(index, invite_token="not-a-real-token", client_label="c")


def test_redeem_expired_raises_expired(index: Index, monkeypatch):
    # Force the invite to look expired.
    issued = auth_mod.mint_invite(index, label="x", ttl_seconds=60)
    past = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat(timespec="seconds")
    index.conn.execute("UPDATE tokens SET expires_at = ? WHERE id = ?", (past, issued.id))
    with pytest.raises(auth_mod.InviteExpired):
        auth_mod.redeem_invite(index, invite_token=issued.token, client_label="c")


def test_authenticate_bearer_rejects_invite(index: Index):
    issued = auth_mod.mint_invite(index, label="x")
    # An invite must be redeemed first; raw use as a bearer fails.
    assert auth_mod.authenticate_bearer(index, issued.token) is None


def test_authenticate_bearer_updates_last_used(index: Index):
    issued = auth_mod.mint_invite(index, label="x")
    bearer = auth_mod.redeem_invite(index, invite_token=issued.token, client_label="c")
    before = index.conn.execute(
        "SELECT last_used_at FROM tokens WHERE id = ?", (bearer.id,)
    ).fetchone()["last_used_at"]
    assert before is None
    row = auth_mod.authenticate_bearer(index, bearer.token, ip="1.2.3.4")
    assert row is not None
    after = index.conn.execute(
        "SELECT last_used_at, last_ip FROM tokens WHERE id = ?", (bearer.id,)
    ).fetchone()
    assert after["last_used_at"] is not None
    assert after["last_ip"] == "1.2.3.4"


def test_revoke_kills_bearer(index: Index):
    issued = auth_mod.mint_invite(index, label="x")
    bearer = auth_mod.redeem_invite(index, invite_token=issued.token, client_label="c")
    assert auth_mod.authenticate_bearer(index, bearer.token) is not None
    assert auth_mod.revoke_token(index, bearer.id) is True
    assert auth_mod.authenticate_bearer(index, bearer.token) is None


def test_purge_expired_invites(index: Index):
    issued = auth_mod.mint_invite(index, label="keep")
    expired = auth_mod.mint_invite(index, label="drop")
    past = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat(timespec="seconds")
    index.conn.execute("UPDATE tokens SET expires_at = ? WHERE id = ?", (past, expired.id))
    n = auth_mod.purge_expired_invites(index)
    assert n == 1
    remaining = {r.id for r in auth_mod.list_tokens(index, kind="invite")}
    assert remaining == {issued.id}


def test_invite_url_round_trip():
    url = auth_mod.build_invite_url(host="10.0.0.4", port=8765, invite_token="abc-def")
    parsed = auth_mod.parse_invite_url(url)
    assert parsed["host"] == "10.0.0.4"
    assert parsed["port"] == "8765"
    assert parsed["invite"] == "abc-def"


def test_invite_url_parse_rejects_garbage():
    with pytest.raises(ValueError):
        auth_mod.parse_invite_url("https://example.com/")
    with pytest.raises(ValueError):
        auth_mod.parse_invite_url("claude-search://join?host=x")  # missing fields
