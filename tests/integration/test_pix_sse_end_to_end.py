# ruff: noqa: E402
"""Requer TEST_POSTGRES_URL (Postgres descartável) e um Redis real acessível em
REDIS_URL (default redis://localhost:6379/0) — o SSE consome PixEventBus, que
usa pub/sub Redis de verdade; não há como fazer isso com fakes."""
import os
import sys
import uuid
import hmac
import hashlib
import time
import json
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

app_dir = Path(__file__).resolve().parents[2] / "app"
if str(app_dir) not in sys.path:
    sys.path.append(str(app_dir))

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

import pytest
import pytest_asyncio
import respx
from httpx import AsyncClient, ASGITransport, Response
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


def _sig(secret, data_id, req_id, ts):
    manifest = f"id:{data_id};request-id:{req_id};ts:{ts};"
    return f"ts={ts},v1=" + hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()


def _token_for(user_id: uuid.UUID) -> str:
    from infra.security.auth_handler import AuthHandler
    return AuthHandler.create_access_token(data={"sub": str(user_id)})


async def _seed(session):
    from infra.database.models import (
        UserModel, MarketModel, TerminalModel, BoxModel, ProductModel,
        MercadoPagoConnectionModel, SaleModel, SaleItemModel, PixPaymentAttemptModel,
    )
    from infra.security.secret_cipher import SecretCipher

    owner_id = uuid.uuid4()
    market_id = uuid.uuid4()
    terminal_id = uuid.uuid4()
    box_id = uuid.uuid4()
    product_id = uuid.uuid4()
    sale_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    order_id = f"ord-{uuid.uuid4().hex[:12]}"
    amount = Decimal("20.00")

    session.add(UserModel(id=owner_id, name="Owner", email=f"owner-{owner_id}@x.test",
                          password_hash="x", role="owner", cpf=str(owner_id.int)[:11]))
    await session.flush()

    session.add(MarketModel(id=market_id, owner_id=owner_id, name="Loja",
                            document=str(market_id.int)[:14], address="Rua X"))
    await session.flush()

    session.add(TerminalModel(id=terminal_id, market_id=market_id, name="Terminal 1"))
    await session.flush()

    session.add(BoxModel(id=box_id, market_id=market_id, terminal_id=terminal_id,
                        operator_id=owner_id, status="aberto"))
    await session.flush()

    session.add(ProductModel(id=product_id, market_id=market_id, name="Produto Teste",
                            code="P1", price=Decimal("10.00"), current_stock=Decimal("100")))
    await session.flush()

    cipher = SecretCipher("k" * 32)
    session.add(MercadoPagoConnectionModel(
        market_id=market_id, status="connected", mp_user_id="42",
        access_token_ciphertext=cipher.encrypt("AT"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    ))

    session.add(SaleModel(id=sale_id, market_id=market_id, box_id=box_id, operator_id=owner_id,
                          status="aguardando_pagamento", total_amount=amount))
    await session.flush()

    session.add(SaleItemModel(id=uuid.uuid4(), sale_id=sale_id, product_id=product_id,
                              product_name_snapshot="Produto Teste", quantity=2,
                              unit_price=Decimal("10.00"), total=amount))

    session.add(PixPaymentAttemptModel(
        id=attempt_id, market_id=market_id, sale_id=sale_id, box_id=box_id,
        terminal_id=terminal_id, operator_id=owner_id, amount=amount, currency="BRL",
        external_reference=f"pixref{uuid.uuid4().hex}"[:64],
        idempotency_key=str(uuid.uuid4()), status="pending", order_id=order_id,
        receiver_account_id="42",
    ))

    await session.commit()

    return {"market_id": market_id, "owner_id": owner_id, "sale_id": sale_id,
            "attempt_id": attempt_id, "order_id": order_id, "amount": amount}


@pytest_asyncio.fixture
async def app_client():
    from infra.web.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=10.0) as client:
        yield client


@pytest.mark.asyncio
@respx.mock
async def test_webhook_pushes_approved_event_to_sse(app_client, monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "whsec")
    from infra.config import settings as sm
    sm.get_settings.cache_clear()

    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        seeded = await _seed(session)
    await engine.dispose()

    order_id = seeded["order_id"]
    amount = seeded["amount"]
    respx.get(f"https://api.mercadopago.com/v1/orders/{order_id}").mock(return_value=Response(200, json={
        "id": order_id, "status": "processed", "status_detail": "accredited",
        "total_amount": f"{amount:.2f}", "user_id": "42"}))

    headers = {"Authorization": f"Bearer {_token_for(seeded['owner_id'])}",
              "accept": "text/event-stream"}

    async def read_first_approved():
        async with app_client.stream(
            "GET", f"/api/v1/pix/{seeded['market_id']}/attempts/{seeded['attempt_id']}/events",
            headers=headers,
        ) as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if "payment.approved" in line:
                    return True
        return False

    sse_task = asyncio.create_task(read_first_approved())
    await asyncio.sleep(0.3)  # dá tempo do stream assinar o canal antes do webhook publicar

    ts = int(time.time() * 1000)
    body = json.dumps({"type": "order", "action": "order.processed",
                       "data": {"id": order_id}, "user_id": "42"})
    webhook_headers = {"x-signature": _sig("whsec", order_id, "r1", ts), "x-request-id": "r1",
                       "content-type": "application/json"}
    r = await app_client.post("/api/v1/webhooks/mercado-pago", content=body, headers=webhook_headers)
    assert r.status_code == 200, r.text

    got = await asyncio.wait_for(sse_task, timeout=10)
    assert got is True
