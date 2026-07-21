# ruff: noqa: E402
"""Requer TEST_POSTGRES_URL apontando para um Postgres descartável de teste."""
import os
import sys
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
        UserModel, MarketModel, TerminalModel, BoxModel, SaleModel, PixPaymentAttemptModel,
    )

    owner_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    market_id = uuid.uuid4()
    terminal_id = uuid.uuid4()
    box_id = uuid.uuid4()
    sale_id = uuid.uuid4()
    attempt_id = uuid.uuid4()

    session.add(UserModel(id=owner_id, name="Owner", email=f"owner-{owner_id}@x.test",
                          password_hash="x", role="owner", cpf=str(owner_id.int)[:11]))
    session.add(UserModel(id=admin_id, name="Admin", email=f"admin-{admin_id}@x.test",
                          password_hash="x", role="admin", cpf=str(admin_id.int)[:11]))
    await session.flush()

    session.add(MarketModel(id=market_id, owner_id=owner_id, name="Loja",
                            document=str(market_id.int)[:14], address="Rua X"))
    await session.flush()

    session.add(TerminalModel(id=terminal_id, market_id=market_id, name="Terminal 1"))
    await session.flush()

    session.add(BoxModel(id=box_id, market_id=market_id, terminal_id=terminal_id,
                        operator_id=owner_id, status="aberto"))
    await session.flush()

    session.add(SaleModel(id=sale_id, market_id=market_id, box_id=box_id,
                          operator_id=owner_id, status="aguardando_pagamento",
                          total_amount=Decimal("10.00")))
    await session.flush()

    session.add(PixPaymentAttemptModel(
        id=attempt_id, market_id=market_id, sale_id=sale_id, box_id=box_id,
        terminal_id=terminal_id, operator_id=owner_id, amount=Decimal("10.00"),
        external_reference=f"pixref{uuid.uuid4().hex}"[:64], idempotency_key=str(uuid.uuid4()),
        status="divergent", order_id=f"ORD-{uuid.uuid4()}",
    ))
    await session.commit()

    return {"market_id": market_id, "owner_id": owner_id, "admin_id": admin_id,
            "attempt_id": attempt_id}


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
async def test_reconciliation_requires_admin(app_client, seeded):
    headers = {"Authorization": f"Bearer {_token_for(seeded['owner_id'])}"}
    r = await app_client.get("/api/v1/admin/pix/reconciliation", headers=headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_reconciliation_returns_counters(app_client, seeded):
    headers = {"Authorization": f"Bearer {_token_for(seeded['admin_id'])}"}
    r = await app_client.get("/api/v1/admin/pix/reconciliation", headers=headers)
    assert r.status_code == 200
    body = r.json()
    for key in ("paid_not_completed", "completed_not_confirmed", "divergent", "stale_pending"):
        assert key in body
    assert body["divergent"] >= 1
    assert "qr_data" not in r.text


@pytest.mark.asyncio
async def test_reprocess_requires_admin(app_client, seeded):
    headers = {"Authorization": f"Bearer {_token_for(seeded['owner_id'])}"}
    r = await app_client.post(
        f"/api/v1/admin/pix/attempts/{seeded['attempt_id']}/reprocess", headers=headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_reprocess_unknown_attempt_returns_404(app_client, seeded):
    headers = {"Authorization": f"Bearer {_token_for(seeded['admin_id'])}"}
    r = await app_client.post(
        f"/api/v1/admin/pix/attempts/{uuid.uuid4()}/reprocess", headers=headers)
    assert r.status_code == 404
