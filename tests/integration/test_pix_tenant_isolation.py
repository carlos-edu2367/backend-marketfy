# ruff: noqa: E402
"""Requer TEST_POSTGRES_URL apontando para um Postgres descartável de teste."""
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

app_dir = Path(__file__).resolve().parents[2] / "app"
if str(app_dir) not in sys.path:
    sys.path.append(str(app_dir))

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _seed(session):
    from infra.database.models import (
        UserModel, MarketModel, TerminalModel, BoxModel, SaleModel,
        PixPaymentAttemptModel, MercadoPagoConnectionModel,
    )
    from infra.security.secret_cipher import SecretCipher

    def _market(mp_user_id):
        owner_id = uuid.uuid4()
        market_id = uuid.uuid4()
        terminal_id = uuid.uuid4()
        box_id = uuid.uuid4()
        sale_id = uuid.uuid4()
        attempt_id = uuid.uuid4()
        return {
            "owner_id": owner_id, "market_id": market_id, "terminal_id": terminal_id,
            "box_id": box_id, "sale_id": sale_id, "attempt_id": attempt_id,
            "mp_user_id": mp_user_id,
        }

    a = _market("111")
    b = _market("222")
    cashier_id = uuid.uuid4()

    for m in (a, b):
        session.add(UserModel(id=m["owner_id"], name="Owner", email=f"owner-{m['owner_id']}@x.test",
                              password_hash="x", role="owner", cpf=str(m["owner_id"].int)[:11]))
    session.add(UserModel(id=cashier_id, name="Caixa", email=f"cashier-{cashier_id}@x.test",
                          password_hash="x", role="cashier", cpf=str(cashier_id.int)[:11]))
    await session.flush()

    for m in (a, b):
        session.add(MarketModel(id=m["market_id"], owner_id=m["owner_id"], name="Loja",
                                document=str(m["market_id"].int)[:14], address="Rua X"))
    await session.flush()

    for m in (a, b):
        session.add(TerminalModel(id=m["terminal_id"], market_id=m["market_id"], name="Terminal 1"))
    await session.flush()

    for m in (a, b):
        session.add(BoxModel(id=m["box_id"], market_id=m["market_id"], terminal_id=m["terminal_id"],
                            operator_id=m["owner_id"], status="aberto"))
    await session.flush()

    for m in (a, b):
        session.add(SaleModel(id=m["sale_id"], market_id=m["market_id"], box_id=m["box_id"],
                              operator_id=m["owner_id"], status="aguardando_pagamento",
                              total_amount=Decimal("10.00")))
    await session.flush()

    order_id_b = f"ORD-{uuid.uuid4()}"
    for m, order_id in ((a, f"ORD-{uuid.uuid4()}"), (b, order_id_b)):
        session.add(PixPaymentAttemptModel(
            id=m["attempt_id"], market_id=m["market_id"], sale_id=m["sale_id"], box_id=m["box_id"],
            terminal_id=m["terminal_id"], operator_id=m["owner_id"], amount=Decimal("10.00"),
            external_reference=f"pixref{uuid.uuid4().hex}"[:64], idempotency_key=str(uuid.uuid4()),
            status="pending", order_id=order_id,
        ))
    b["order_id"] = order_id_b

    cipher = SecretCipher("k" * 32)
    session.add(MercadoPagoConnectionModel(
        market_id=b["market_id"], status="connected", mp_user_id=b["mp_user_id"],
        access_token_ciphertext=cipher.encrypt("AT"),
    ))
    await session.commit()

    return {"market_a": a, "market_b": b, "cashier_id": cashier_id}


def _token_for(user_id: uuid.UUID) -> str:
    from infra.security.auth_handler import AuthHandler
    return AuthHandler.create_access_token(data={"sub": str(user_id)})


@pytest_asyncio.fixture
async def app_client():
    from infra.web.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def seeded():
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        data = await _seed(session)
    await engine.dispose()
    return data


@pytest.mark.asyncio
async def test_tenant_a_cannot_read_tenant_b_attempt(app_client, seeded):
    a, b = seeded["market_a"], seeded["market_b"]
    headers = {"Authorization": f"Bearer {_token_for(a['owner_id'])}"}
    r = await app_client.get(
        f"/api/v1/pix/{a['market_id']}/attempts/{b['attempt_id']}", headers=headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_tenant_a_cannot_verify_tenant_b_attempt(app_client, seeded):
    a, b = seeded["market_a"], seeded["market_b"]
    headers = {"Authorization": f"Bearer {_token_for(a['owner_id'])}"}
    r = await app_client.post(
        f"/api/v1/pix/{a['market_id']}/attempts/{b['attempt_id']}/verify", headers=headers)
    assert r.status_code in (404, 403)


@pytest.mark.asyncio
async def test_tenant_a_cannot_cancel_tenant_b_attempt(app_client, seeded):
    a, b = seeded["market_a"], seeded["market_b"]
    headers = {"Authorization": f"Bearer {_token_for(a['owner_id'])}"}
    r = await app_client.post(
        f"/api/v1/pix/{a['market_id']}/attempts/{b['attempt_id']}/cancel", headers=headers)
    assert r.status_code in (404, 403)


@pytest.mark.asyncio
async def test_cashier_cannot_connect_or_disconnect(app_client, seeded):
    a = seeded["market_a"]
    headers = {"Authorization": f"Bearer {_token_for(seeded['cashier_id'])}"}
    r1 = await app_client.post(f"/api/v1/pix/{a['market_id']}/oauth/authorize", headers=headers)
    assert r1.status_code == 403
    r2 = await app_client.delete(f"/api/v1/pix/{a['market_id']}/oauth", headers=headers)
    assert r2.status_code == 403


@pytest.mark.asyncio
async def test_webhook_tenant_mismatch_does_not_complete(app_client, seeded, monkeypatch):
    b = seeded["market_b"]
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "whsec")
    from infra.config import settings as sm
    sm.get_settings.cache_clear()

    ts = int(time.time() * 1000)
    manifest = f"id:{b['order_id'].lower()};request-id:r1;ts:{ts};"
    v1 = hmac.new(b"whsec", manifest.encode(), hashlib.sha256).hexdigest()
    r = await app_client.post(
        "/api/v1/webhooks/mercado-pago",
        content=json.dumps({"type": "order", "action": "order.processed",
                            "data": {"id": b["order_id"]}, "user_id": "999999"}),
        headers={"x-signature": f"ts={ts},v1={v1}", "x-request-id": "r1",
                 "content-type": "application/json"})
    assert r.status_code == 200

    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        from infra.repositories.pix_repo import PixPaymentAttemptRepository
        repo = PixPaymentAttemptRepository(session)
        attempt = await repo.get_by_id(b["attempt_id"], b["market_id"])
        assert attempt.status == "pending"
    await engine.dispose()
