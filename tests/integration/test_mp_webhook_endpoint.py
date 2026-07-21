# ruff: noqa: E402
"""Requer TEST_POSTGRES_URL apontando para um Postgres descartável de teste."""
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path

app_dir = Path(__file__).resolve().parents[2] / "app"
if str(app_dir) not in sys.path:
    sys.path.append(str(app_dir))

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)
os.environ.setdefault("DATABASE_URL", TEST_POSTGRES_URL or "postgresql://x:y@localhost/z")
os.environ.setdefault("SECRET_KEY", "test-secret-key-com-mais-de-32-caracteres-ok")
os.environ.setdefault("MP_APP_ID", "app-1")
os.environ.setdefault("MP_OAUTH_REDIRECT_URI", "https://cb")
os.environ.setdefault("MP_SECRET_KEY", "k" * 32)
os.environ.setdefault("PUBLIC_FRONTEND_URL", "https://front")


def _sig(secret, data_id, req_id, ts):
    manifest = f"id:{data_id.lower()};request-id:{req_id};ts:{ts};"
    return f"ts={ts},v1=" + hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()


@pytest_asyncio.fixture
async def app_client():
    from infra.web.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature(app_client, monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "whsec")
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    data_id = f"ORD-{uuid.uuid4().hex[:8]}"
    r = await app_client.post(
        f"/api/v1/webhooks/mercado-pago?data.id={data_id}&type=order",
        content=json.dumps({"type": "order", "action": "order.processed", "data": {"id": data_id}}),
        headers={"x-signature": "ts=1,v1=bad", "x-request-id": "r1", "content-type": "application/json"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_webhook_accepts_valid_signature_unknown_order(app_client, monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "whsec")
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    ts = int(time.time() * 1000)
    data_id = f"ORDX-{uuid.uuid4().hex[:8]}"
    body = json.dumps({"type": "order", "action": "order.processed", "user_id": "42"})
    r = await app_client.post(
        f"/api/v1/webhooks/mercado-pago?data.id={data_id}&type=order",
        content=body,
        headers={
            "x-signature": _sig("whsec", data_id, "r1", ts),
            "x-request-id": "r1",
            "content-type": "application/json",
        },
    )
    assert r.status_code == 200  # assinatura válida; order desconhecida → conciliação


@pytest.mark.asyncio
async def test_webhook_data_id_falls_back_to_body_when_query_missing(app_client, monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "whsec")
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    ts = int(time.time() * 1000)
    data_id = f"ORDB-{uuid.uuid4().hex[:8]}"
    body = json.dumps({"type": "order", "action": "order.processed", "data": {"id": data_id}, "user_id": "42"})
    r = await app_client.post(
        "/api/v1/webhooks/mercado-pago",
        content=body,
        headers={
            "x-signature": _sig("whsec", data_id, "r1", ts),
            "x-request-id": "r1",
            "content-type": "application/json",
        },
    )
    assert r.status_code == 200
