from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from funnel_fixtures import db, engine, seed_admin, seed_plan, session_factory  # noqa: F401
from infra.database.setup import get_db
from infra.web.routers import funnels_admin, funnels_public


@pytest_asyncio.fixture
async def client(session_factory):
    async with session_factory() as s:
        admin = await seed_admin(s)
        plan = await seed_plan(s)
        await s.commit()
    state = {"user": SimpleNamespace(id=admin.id, role="admin")}

    app = FastAPI()
    app.include_router(funnels_public.router, prefix="/api/v1/funnels")
    app.include_router(funnels_admin.router, prefix="/api/v1/admin/funnels")

    async def _db():
        async with session_factory() as s:
            yield s

    def _require_admin():
        from fastapi import HTTPException
        if state["user"].role != "admin":
            raise HTTPException(status_code=403, detail="Acesso negado.")
        return state["user"]

    class _Attribution:
        claims = []

        async def claim(self, fsid, user_id):
            self.claims.append((fsid, user_id))
            return True

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[funnels_admin.require_admin] = _require_admin
    app.dependency_overrides[funnels_admin.get_audit_service] = lambda: None
    app.dependency_overrides[funnels_public.get_current_user] = lambda: state["user"]
    app.dependency_overrides[funnels_public.get_funnel_attribution_service] = lambda: _Attribution()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.state, c.plan_id = state, plan.id
        yield c


async def _published_funnel(client, slug="oferta"):
    r = await client.post("/api/v1/admin/funnels", json={"name": "Oferta", "slug": slug, "plan_id": str(client.plan_id)})
    assert r.status_code == 201, r.text
    detail = r.json()
    vid = detail["variants"][0]["id"]
    for name in ("Intro", "Oferta"):
        r = await client.post(f"/api/v1/admin/funnels/variants/{vid}/steps", json={"name": name, "html": f"<p>{name}</p>"})
        assert r.status_code == 201
    r = await client.post(f"/api/v1/admin/funnels/{detail['funnel']['id']}/publish")
    assert r.status_code == 200, r.text
    return detail["funnel"]["id"], vid


@pytest.mark.asyncio
async def test_full_public_flow_and_metrics(client):
    funnel_id, _ = await _published_funnel(client)
    r = await client.post("/api/v1/funnels/public/oferta/session", json={"utm_source": "meta"})
    assert r.status_code == 200
    body = r.json()
    assert [s["name"] for s in body["steps"]] == ["Intro", "Oferta"]
    fsid = body["fsid"]
    for payload in ({"type": "step_view", "step_position": 0}, {"type": "step_next", "step_position": 0},
                    {"type": "step_view", "step_position": 1}, {"type": "funnel_completed"}):
        r = await client.post("/api/v1/funnels/public/events", json={"fsid": fsid, **payload})
        assert r.status_code == 204, r.text

    metrics = (await client.get(f"/api/v1/admin/funnels/{funnel_id}/metrics")).json()
    assert metrics["summary"]["sessions"] == 1 and metrics["summary"]["completed"] == 1
    assert metrics["sources"][0]["utm_source"] == "meta"
    listing = (await client.get("/api/v1/admin/funnels")).json()
    assert listing[0]["sessions_30d"] == 1 and listing[0]["variant_count"] == 1


@pytest.mark.asyncio
async def test_public_errors(client):
    assert (await client.post("/api/v1/funnels/public/nada/session", json={})).status_code == 404
    funnel_id, _ = await _published_funnel(client)
    fsid = (await client.post("/api/v1/funnels/public/oferta/session", json={})).json()["fsid"]
    r = await client.post("/api/v1/funnels/public/events", json={"fsid": fsid, "type": "invoice_paid"})
    assert r.status_code == 422
    r = await client.post("/api/v1/funnels/public/events", json={"fsid": str(uuid.uuid4()), "type": "step_view", "step_position": 0})
    assert r.status_code == 404
    await client.post(f"/api/v1/admin/funnels/{funnel_id}/unpublish")
    r = await client.post("/api/v1/funnels/public/events", json={"fsid": fsid, "type": "step_view", "step_position": 0})
    assert r.status_code == 410


@pytest.mark.asyncio
async def test_publish_invalid_returns_errors(client):
    r = await client.post("/api/v1/admin/funnels", json={"name": "X", "slug": "x"})
    r = await client.post(f"/api/v1/admin/funnels/{r.json()['funnel']['id']}/publish")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "funnel.publish_invalid"
    assert "Selecione um plano de destino ativo." in r.json()["detail"]["errors"]


@pytest.mark.asyncio
async def test_invalid_slug_is_422(client):
    r = await client.post("/api/v1/admin/funnels", json={"name": "X", "slug": "Com Espaço"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "funnel.invalid_slug"


@pytest.mark.asyncio
async def test_preview_returns_draft_steps_without_session(client):
    r = await client.post("/api/v1/admin/funnels", json={"name": "X", "slug": "x"})
    vid = r.json()["variants"][0]["id"]
    await client.post(f"/api/v1/admin/funnels/variants/{vid}/steps", json={"name": "S", "html": "<b>s</b>"})
    r = await client.get(f"/api/v1/admin/funnels/variants/{vid}/preview")
    assert r.status_code == 200 and r.json()["steps"][0]["html"] == "<b>s</b>"


@pytest.mark.asyncio
async def test_admin_routes_forbidden_for_non_admin(client):
    client.state["user"] = SimpleNamespace(id=uuid.uuid4(), role="owner")
    assert (await client.get("/api/v1/admin/funnels")).status_code == 403


@pytest.mark.asyncio
async def test_claim_calls_attribution(client):
    fsid = uuid.uuid4()
    r = await client.post(f"/api/v1/funnels/sessions/{fsid}/claim")
    assert r.status_code == 200 and r.json() == {"linked": True}
