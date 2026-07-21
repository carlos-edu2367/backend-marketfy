# ruff: noqa: E402
"""Requer TEST_POSTGRES_URL apontando para um Postgres descartável de teste."""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
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


def _token_for(user_id: uuid.UUID) -> str:
    from infra.security.auth_handler import AuthHandler
    return AuthHandler.create_access_token(data={"sub": str(user_id)})


async def _seed(session, *, attempt_status="pending"):
    from infra.database.models import (
        UserModel, MarketModel, TerminalModel, BoxModel, PixPaymentAttemptModel, SaleModel,
    )

    owner_id = uuid.uuid4()
    market_id = uuid.uuid4()
    terminal_id = uuid.uuid4()
    box_id = uuid.uuid4()
    sale_id = uuid.uuid4()
    attempt_id = uuid.uuid4()

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

    session.add(SaleModel(id=sale_id, market_id=market_id, box_id=box_id, operator_id=owner_id,
                          status="aguardando_pagamento", total_amount=Decimal("20.00")))
    await session.flush()

    session.add(PixPaymentAttemptModel(
        id=attempt_id, market_id=market_id, sale_id=sale_id, box_id=box_id,
        terminal_id=terminal_id, operator_id=owner_id, amount=Decimal("20.00"), currency="BRL",
        external_reference=f"pixref{uuid.uuid4().hex}"[:64],
        idempotency_key=str(uuid.uuid4()), status=attempt_status,
        order_id=f"ord-{uuid.uuid4().hex[:12]}",
    ))
    await session.commit()

    return {"market_id": market_id, "owner_id": owner_id, "attempt_id": attempt_id}


@pytest_asyncio.fixture
async def app_client():
    from infra.web.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_sse_requires_market_and_attempt(app_client):
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        seeded = await _seed(session)
    await engine.dispose()

    headers = {"Authorization": f"Bearer {_token_for(seeded['owner_id'])}"}
    r = await app_client.get(
        f"/api/v1/pix/{seeded['market_id']}/attempts/{uuid.uuid4()}/events", headers=headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_sse_emits_current_state_first_and_closes_on_final_status(app_client):
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        # status já final (approved) => o endpoint deve emitir o estado atual e
        # encerrar o stream sem assinar o bus (não precisamos de Redis real aqui)
        seeded = await _seed(session, attempt_status="approved")
    await engine.dispose()

    headers = {"Authorization": f"Bearer {_token_for(seeded['owner_id'])}",
              "accept": "text/event-stream"}
    async with app_client.stream(
        "GET", f"/api/v1/pix/{seeded['market_id']}/attempts/{seeded['attempt_id']}/events",
        headers=headers,
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = b""
        async for chunk in resp.aiter_bytes():
            body += chunk
        text = body.decode("utf-8")
        assert "event: payment.approved" in text
        assert f'"attempt_id": "{seeded["attempt_id"]}"' in text
        assert '"sale_completed": true' in text


@pytest.mark.asyncio
async def test_sse_requires_market_access_permission(app_client):
    """Outro usuário sem vínculo com a loja não deve conseguir assinar o stream."""
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        seeded = await _seed(session, attempt_status="approved")
    await engine.dispose()

    from infra.security.auth_handler import AuthHandler
    stranger_id = uuid.uuid4()
    headers = {"Authorization": f"Bearer {AuthHandler.create_access_token(data={'sub': str(stranger_id)})}"}
    r = await app_client.get(
        f"/api/v1/pix/{seeded['market_id']}/attempts/{seeded['attempt_id']}/events", headers=headers)
    assert r.status_code in (401, 403, 404)
