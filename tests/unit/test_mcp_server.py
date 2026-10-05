from __future__ import annotations

import base64
import hashlib
import json
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from funnel_fixtures import engine, seed_plan, session_factory  # noqa: F401
from application.services.funnel_admin_service import FunnelAdminService
from infra.database.models import UserModel
from infra.database.setup import get_db
from infra.mcp import oauth as mcp_oauth, server as mcp_server
from infra.repositories.funnel_repo import FunnelRepository
from infra.security.auth_handler import AuthHandler
from infra.web.routers import funnels_public

PASSWORD = "senha-forte-123"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


async def _user(s, role):
    user = UserModel(id=uuid.uuid4(), name=role, email=f"{role}-{uuid.uuid4().hex[:6]}@t.com",
                     password_hash=AuthHandler.get_password_hash(PASSWORD), role=role, is_active=True)
    s.add(user)
    await s.flush()
    return user


@pytest_asyncio.fixture
async def client(session_factory):
    async with session_factory() as s:
        admin = await _user(s, "admin")
        owner = await _user(s, "owner")
        plan = await seed_plan(s)
        await s.commit()

    app = FastAPI()
    app.include_router(mcp_oauth.router)
    app.include_router(mcp_server.router)
    app.include_router(funnels_public.router, prefix="/api/v1/funnels")

    async def _db():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[funnels_public.get_current_user] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.admin, c.owner, c.plan_id, c.session_factory = admin, owner, plan.id, session_factory
        yield c


async def _register(client):
    r = await client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [REDIRECT]})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _auth_form(client_id, **extra):
    return {"client_id": client_id, "redirect_uri": REDIRECT, "state": "xyz", "code_challenge": CHALLENGE,
            "code_challenge_method": "S256", "response_type": "code", **extra}


async def _code(client, client_id, email):
    r = await client.post("/oauth/authorize", data=_auth_form(client_id, email=email, password=PASSWORD))
    assert r.status_code == 302, r.text
    query = parse_qs(urlparse(r.headers["location"]).query)
    assert query["state"] == ["xyz"]
    return query["code"][0]


async def _token(client, email=None):
    client_id = await _register(client)
    code = await _code(client, client_id, email or client.admin.email)
    r = await client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                                "client_id": client_id, "redirect_uri": REDIRECT,
                                                "code_verifier": VERIFIER})
    assert r.status_code == 200, r.text
    return r.json(), client_id


async def _rpc(client, token, method, params=None, req_id=1):
    r = await client.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                          json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
    assert r.status_code == 200, r.text
    return r.json()


async def _call(client, token, name, arguments=None):
    body = await _rpc(client, token, "tools/call", {"name": name, "arguments": arguments or {}})
    result = body["result"]
    text = result["content"][0]["text"]
    try:
        return result["isError"], json.loads(text)
    except ValueError:
        return result["isError"], text


@pytest.mark.asyncio
async def test_discovery_and_unauthorized(client):
    r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r.status_code == 401
    assert 'resource_metadata="http://t/.well-known/oauth-protected-resource"' in r.headers["www-authenticate"]

    resource = (await client.get("/.well-known/oauth-protected-resource")).json()
    assert resource["resource"] == "http://t/mcp" and resource["authorization_servers"] == ["http://t"]
    meta = (await client.get("/.well-known/oauth-authorization-server")).json()
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["registration_endpoint"] == "http://t/oauth/register"
    assert (await client.get("/mcp")).status_code == 405


@pytest.mark.asyncio
async def test_app_access_token_is_not_accepted(client):
    app_token = AuthHandler.create_access_token({"sub": str(client.admin.id), "role": "admin"})
    r = await client.post("/mcp", headers={"Authorization": f"Bearer {app_token}"},
                          json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_oauth_guards(client):
    assert (await client.post("/oauth/register", json={"redirect_uris": ["http://evil.com/cb"]})).status_code == 400
    client_id = await _register(client)
    assert (await client.get("/oauth/authorize", params=_auth_form(client_id))).status_code == 200
    bad_redirect = _auth_form(client_id, redirect_uri="https://evil.com/cb")
    assert (await client.get("/oauth/authorize", params=bad_redirect)).status_code == 400
    no_pkce = _auth_form(client_id, code_challenge_method="plain")
    assert (await client.get("/oauth/authorize", params=no_pkce)).status_code == 400

    wrong = await client.post("/oauth/authorize", data=_auth_form(client_id, email=client.admin.email, password="x"))
    assert wrong.status_code == 401 and "incorretos" in wrong.text
    not_admin = await client.post("/oauth/authorize",
                                  data=_auth_form(client_id, email=client.owner.email, password=PASSWORD))
    assert not_admin.status_code == 401 and "administrador" in not_admin.text

    code = await _code(client, client_id, client.admin.email)
    exchange = {"grant_type": "authorization_code", "code": code, "client_id": client_id, "redirect_uri": REDIRECT}
    assert (await client.post("/oauth/token", data={**exchange, "code_verifier": "errado"})).status_code == 400
    assert (await client.post("/oauth/token", data={**exchange, "code_verifier": VERIFIER})).status_code == 200
    replay = await client.post("/oauth/token", data={**exchange, "code_verifier": VERIFIER})
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_refresh_token(client):
    tokens, client_id = await _token(client)
    r = await client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": client_id,
                                                "refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    assert (await _rpc(client, r.json()["access_token"], "ping"))["result"] == {}
    other_client = await _register(client)
    r = await client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": other_client + "x",
                                                "refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_protocol_handshake(client):
    tokens, _ = await _token(client)
    token = tokens["access_token"]
    init = await _rpc(client, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                    "clientInfo": {"name": "t", "version": "1"}})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
    notif = await client.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                              json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert notif.status_code == 202
    names = {t["name"] for t in (await _rpc(client, token, "tools/list"))["result"]["tools"]}
    assert names == {"funnel_create", "funnel_list", "funnel_get", "funnel_step_metrics", "funnel_overview",
                     "funnel_list_plans"}
    assert (await _rpc(client, token, "nada/aqui"))["error"]["code"] == -32601
    assert (await _rpc(client, token, "tools/call", {"name": "nao_existe"}))["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_funnel_tools_end_to_end(client):
    tokens, _ = await _token(client)
    token = tokens["access_token"]

    is_error, plans = await _call(client, token, "funnel_list_plans")
    assert not is_error and plans["plans"][0]["id"] == str(client.plan_id)

    is_error, created = await _call(client, token, "funnel_create", {
        "name": "Oferta Pro", "slug": "oferta-pro", "plan_id": str(client.plan_id),
        "steps": [{"name": "Promessa", "html": "<button data-funnel-next>Ir</button>"},
                  {"name": "Oferta", "html": "<button data-funnel-finish>Assinar</button>"}],
    })
    assert not is_error, created
    funnel = created["funnel"]
    assert funnel["status"] == "draft"
    assert [s["name"] for s in funnel["variants"][0]["steps"]] == ["Promessa", "Oferta"]

    is_error, dup = await _call(client, token, "funnel_create", {"name": "X", "slug": "oferta-pro"})
    assert is_error and dup["error"] == "funnel.slug_taken"
    is_error, invalid = await _call(client, token, "funnel_create", {"name": "X"})
    assert is_error and "slug" in invalid

    # publica e gera uma sessão pública que vê a primeira etapa
    async with client.session_factory() as s:
        await FunnelAdminService(FunnelRepository(s)).publish(uuid.UUID(funnel["id"]))
        await s.commit()
    session = (await client.post("/api/v1/funnels/public/oferta-pro/session", json={"utm_source": "meta"})).json()
    r = await client.post("/api/v1/funnels/public/events",
                          json={"fsid": session["fsid"], "type": "step_view", "step_position": 0})
    assert r.status_code == 204

    is_error, listing = await _call(client, token, "funnel_list", {"status": "published"})
    assert not is_error and listing["funnels"][0]["sessions_30d"] == 1
    is_error, listing = await _call(client, token, "funnel_list", {"status": "archived"})
    assert listing["count"] == 0

    is_error, detail = await _call(client, token, "funnel_get", {"slug": "oferta-pro", "include_html": True})
    assert not is_error and detail["variants"][0]["steps"][1]["html"].startswith("<button")
    is_error, msg = await _call(client, token, "funnel_get", {})
    assert is_error and "exatamente um" in msg
    is_error, missing = await _call(client, token, "funnel_get", {"funnel_id": str(uuid.uuid4())})
    assert is_error and missing["error"] == "funnel.not_found"

    is_error, steps = await _call(client, token, "funnel_step_metrics", {"funnel_id": funnel["id"]})
    assert not is_error
    assert steps["sessions"] == 1
    assert [(s["label"], s["count"]) for s in steps["steps"][:3]] == [("Promessa", 1), ("Concluiu o funil", 0),
                                                                       ("Cadastro", 0)]
    assert any("Amostra pequena" in n for n in steps["notes"])

    is_error, ov = await _call(client, token, "funnel_overview", {"funnel_id": funnel["id"], "utm_source": "meta"})
    assert not is_error
    assert ov["summary"]["sessions"] == 1 and ov["filters"] == {"utm_source": "meta"}
    assert ov["biggest_drop"]["stage"] == "Concluiu o funil"
    assert ov["top_sources"][0]["utm_source"] == "meta"
    assert ov["funnel"]["plan"]["name"] == "Pro"
