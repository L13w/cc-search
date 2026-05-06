"""HTTP API tests using FastAPI's TestClient."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claude_search.server import _is_loopback, make_app


def test_is_loopback_helper():
    assert _is_loopback("127.0.0.1") is True
    assert _is_loopback("::1") is True
    assert _is_loopback("localhost") is True
    assert _is_loopback("0.0.0.0") is False
    assert _is_loopback("10.0.0.4") is False
    assert _is_loopback("") is False
    assert _is_loopback("not-an-ip") is False

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def app_client(tmp_path: Path):
    # Build a fake projects dir with one fixture file so startup ingest
    # has something to do.
    projects = tmp_path / "projects" / "-home-u-projA"
    projects.mkdir(parents=True)
    shutil.copy(FIXTURES / "normal.jsonl", projects / "session.jsonl")

    app = make_app(
        db_path=tmp_path / "index.sqlite",
        projects_dir=tmp_path / "projects",
        enable_watcher=False,  # tests run synchronously, no watcher thread
    )
    with TestClient(app) as client:
        yield client


def test_status_returns_index_stats(app_client: TestClient):
    r = app_client.get("/v1/status")
    assert r.status_code == 200
    body = r.json()
    assert body["messages"] >= 1
    assert body["files"] >= 1
    assert "user_typed" in body["by_kind"] or "tool_use" in body["by_kind"]
    assert body["version"]
    assert body["db_path"]


def test_search_returns_hits(app_client: TestClient):
    r = app_client.post(
        "/v1/search",
        json={"q": "ZK proof", "limit": 5},
    )
    assert r.status_code == 200
    hits = r.json()
    assert len(hits) >= 1
    assert any("ZK" in h["snippet"] or "zk" in h["snippet"].lower() for h in hits)
    h = hits[0]
    for key in ("message_id", "session_id", "project_path", "timestamp", "role", "kind", "snippet", "score"):
        assert key in h


def test_search_default_filter_excludes_tool_kinds(app_client: TestClient):
    # The fixture has a tool_use and tool_result row; the default filter
    # (user_typed + assistant_prose) should drop them.
    r = app_client.post("/v1/search", json={"q": "cargo"})
    assert r.status_code == 200
    hits = r.json()
    for h in hits:
        assert h["kind"] in {"user_typed", "assistant_prose"}


def test_search_kinds_empty_means_all(app_client: TestClient):
    r = app_client.post(
        "/v1/search",
        json={"q": "cargo", "kinds": []},
    )
    assert r.status_code == 200
    hits = r.json()
    kinds = {h["kind"] for h in hits}
    # An empty kinds list lifts the filter; should reach into tool rows.
    assert "tool_use" in kinds or "tool_result" in kinds


def test_projects_endpoint(app_client: TestClient):
    r = app_client.get("/v1/projects")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    # Sorted by message count desc.
    counts = [row["messages"] for row in body]
    assert counts == sorted(counts, reverse=True)
    # Fixture has cwd /home/u/proj.
    assert any(row["project_path"] == "/home/u/proj" for row in body)


def test_search_with_project_paths_filter(app_client: TestClient):
    r = app_client.post(
        "/v1/search",
        json={"q": "ZK proof", "project_paths": ["/home/u/proj"]},
    )
    assert r.status_code == 200
    hits = r.json()
    assert all(h["project_path"] == "/home/u/proj" for h in hits)

    # Filter to a non-matching path → zero hits.
    r2 = app_client.post(
        "/v1/search",
        json={"q": "ZK proof", "project_paths": ["/nowhere/at/all"]},
    )
    assert r2.status_code == 200
    assert r2.json() == []


def test_session_endpoint_returns_messages_in_order(app_client: TestClient):
    # Pull a known session_id out of any indexed message.
    hits = app_client.post("/v1/search", json={"q": "ZK proof", "limit": 1}).json()
    assert hits
    sid = hits[0]["session_id"]

    r = app_client.get(f"/v1/session/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert len(body) >= 1
    # All rows belong to the same session.
    assert all(m["session_id"] == sid for m in body)
    # Ordered oldest-first.
    timestamps = [m["timestamp"] for m in body if m["timestamp"]]
    assert timestamps == sorted(timestamps)
    # Full content (no FTS5 ** markup).
    assert all("**" not in m["snippet"] for m in body)
    # Tool kinds are returned alongside prose ones (not filtered out).
    kinds = {m["kind"] for m in body}
    # Fixture has tool_use and tool_result rows.
    assert "tool_use" in kinds or "tool_result" in kinds


def test_session_endpoint_rejects_bad_limit(app_client: TestClient):
    assert app_client.get("/v1/session/x?limit=0").status_code == 400
    assert app_client.get("/v1/session/x?limit=999999").status_code == 400


def test_message_endpoint_returns_full_content(app_client: TestClient):
    # Pull a known id out of a search result, then fetch its full content.
    search = app_client.post("/v1/search", json={"q": "ZK proof", "limit": 1}).json()
    assert search
    mid = search[0]["message_id"]
    r = app_client.get(f"/v1/message/{mid}")
    assert r.status_code == 200
    body = r.json()
    assert body["message_id"] == mid
    # The /v1/message endpoint returns full content, not an FTS snippet,
    # so '**term**' markup shouldn't be present.
    assert "**" not in body["snippet"]
    # project_path is the cwd, which we want exposed in the UI.
    assert body["project_path"]


def test_message_endpoint_404_on_unknown(app_client: TestClient):
    r = app_client.get("/v1/message/does-not-exist")
    assert r.status_code == 404


def test_recent_returns_messages_newest_first(app_client: TestClient):
    r = app_client.get("/v1/recent?limit=10")
    assert r.status_code == 200
    hits = r.json()
    assert len(hits) >= 1
    timestamps = [h["timestamp"] for h in hits if h["timestamp"]]
    assert timestamps == sorted(timestamps, reverse=True)
    # Default-kind filter applies — no tool rows here.
    for h in hits:
        assert h["kind"] in {"user_typed", "assistant_prose"}


def test_recent_rejects_bad_limit(app_client: TestClient):
    assert app_client.get("/v1/recent?limit=0").status_code == 400
    assert app_client.get("/v1/recent?limit=999").status_code == 400


def test_search_invalid_query_returns_400(app_client: TestClient):
    r = app_client.post("/v1/search", json={"q": '"unbalanced'})
    assert r.status_code == 400


def test_cors_localhost_allowed(app_client: TestClient):
    r = app_client.options(
        "/v1/search",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_redeem_then_use_bearer_flow(app_client: TestClient):
    # Mint an invite via the operator endpoint (loopback, no auth needed).
    issue = app_client.post(
        "/v1/auth/issue", json={"label": "lewbook", "ttl_seconds": 600}
    )
    assert issue.status_code == 200
    invite_url = issue.json()["invite_url"]
    # Pull the raw invite token out of the URL.
    invite_token = invite_url.split("invite=", 1)[1].split("&", 1)[0]

    # Redeem it for a bearer.
    redeem = app_client.post(
        "/v1/auth/redeem",
        json={"invite": invite_token, "client_label": "lewbook"},
    )
    assert redeem.status_code == 200
    bearer = redeem.json()["token"]
    assert bearer

    # Use the bearer on /v1/whoami.
    who = app_client.get("/v1/auth/whoami", headers={"authorization": f"Bearer {bearer}"})
    assert who.status_code == 200
    body = who.json()
    assert body["kind"] == "bearer"
    assert body["label"] == "lewbook"


def test_redeem_unknown_invite_returns_404(app_client: TestClient):
    r = app_client.post(
        "/v1/auth/redeem",
        json={"invite": "definitely-not-a-real-token-abcdef", "client_label": "x"},
    )
    assert r.status_code == 404


def test_redeem_twice_returns_404(app_client: TestClient):
    issue = app_client.post("/v1/auth/issue", json={"label": "x"})
    invite = issue.json()["invite_url"].split("invite=", 1)[1].split("&", 1)[0]
    first = app_client.post(
        "/v1/auth/redeem", json={"invite": invite, "client_label": "x"}
    )
    assert first.status_code == 200
    second = app_client.post(
        "/v1/auth/redeem", json={"invite": invite, "client_label": "x"}
    )
    assert second.status_code == 404


def test_clients_list_and_revoke(app_client: TestClient):
    # Mint + redeem, then list the resulting bearer, then revoke it.
    issue = app_client.post("/v1/auth/issue", json={"label": "doomed"})
    invite = issue.json()["invite_url"].split("invite=", 1)[1].split("&", 1)[0]
    redeem = app_client.post(
        "/v1/auth/redeem", json={"invite": invite, "client_label": "doomed"}
    )
    bearer = redeem.json()["token"]

    # List shows the bearer.
    listing = app_client.get("/v1/auth/clients").json()
    bearers = [r for r in listing if r["kind"] == "bearer"]
    assert any(r["label"] == "doomed" for r in bearers)
    bearer_id = next(r["id"] for r in bearers if r["label"] == "doomed")

    # Bearer works before revoke.
    pre = app_client.get("/v1/auth/whoami", headers={"authorization": f"Bearer {bearer}"})
    assert pre.status_code == 200

    # Revoke and confirm subsequent calls fail.
    rev = app_client.post(f"/v1/auth/revoke/{bearer_id}")
    assert rev.status_code == 200

    post = app_client.get("/v1/auth/whoami", headers={"authorization": f"Bearer {bearer}"})
    # On loopback bind, whoami without bearer is fine; with an invalid
    # bearer it raises 401 from _check_bearer.
    assert post.status_code == 401


def test_loopback_bind_does_not_require_token(app_client: TestClient):
    # Same fixture is bound to 127.0.0.1; search without any header works.
    r = app_client.post("/v1/search", json={"q": "ZK proof"})
    assert r.status_code == 200


def test_remote_bind_requires_bearer(tmp_path: Path):
    """Build an app bound to 0.0.0.0; data endpoints then need a bearer.

    TestClient's simulated client.host is `testclient` (not loopback),
    which is exactly the situation we want for this test: a request
    that *isn't* coming through the loopback interface.
    """
    import shutil
    from claude_search import auth as auth_mod
    from claude_search.index import Index

    projects = tmp_path / "projects" / "-home-u-projA"
    projects.mkdir(parents=True)
    shutil.copy(FIXTURES / "normal.jsonl", projects / "session.jsonl")

    db = tmp_path / "index.sqlite"
    # Mint an invite directly so we have a token to test the gate with.
    with Index(db) as idx:
        invite = auth_mod.mint_invite(idx, label="dev-vm")

    app = make_app(
        db_path=db,
        projects_dir=tmp_path / "projects",
        enable_watcher=False,
        host="0.0.0.0",
    )
    with TestClient(app) as client:
        # No bearer → 401.
        r = client.post("/v1/search", json={"q": "ZK proof"})
        assert r.status_code == 401

        # Redeeming an invite is always public — even on remote binds.
        redeem = client.post(
            "/v1/auth/redeem",
            json={"invite": invite.token, "client_label": "lewbook"},
        )
        assert redeem.status_code == 200
        bearer = redeem.json()["token"]

        # With the bearer the search now works.
        r2 = client.post(
            "/v1/search",
            json={"q": "ZK proof"},
            headers={"authorization": f"Bearer {bearer}"},
        )
        assert r2.status_code == 200

        # Operator endpoints stay locked down: TestClient looks like a
        # remote peer, so /v1/auth/issue must reject it.
        issue = client.post("/v1/auth/issue", json={"label": "x"})
        assert issue.status_code == 403


def test_static_ui_served_from_root(app_client: TestClient):
    # The bundled UI should be reachable on the same origin as the API.
    r = app_client.get("/index.html")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower()
    # / falls back to index.html via StaticFiles(html=True).
    r_root = app_client.get("/")
    assert r_root.status_code == 200
    assert "<!doctype html>" in r_root.text.lower()


def test_static_ui_serves_settings_html(app_client: TestClient):
    r = app_client.get("/settings.html")
    assert r.status_code == 200
    assert "claude-search" in r.text.lower()


def test_cors_external_origin_rejected(app_client: TestClient):
    r = app_client.options(
        "/v1/search",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    # CORSMiddleware echoes the allow-origin only if approved. Either
    # the response lacks the header, or the status is 400.
    allow = r.headers.get("access-control-allow-origin")
    assert allow != "https://evil.example.com"
