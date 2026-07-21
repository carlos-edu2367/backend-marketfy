# ruff: noqa: E402
"""Requer TEST_POSTGRES_URL apontando para um Postgres descartável de teste."""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

app_dir = Path(__file__).resolve().parents[2] / "app"
if str(app_dir) not in sys.path:
    sys.path.append(str(app_dir))

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)
os.environ.setdefault("DATABASE_URL", TEST_POSTGRES_URL or "postgresql://x:y@localhost/z")
os.environ.setdefault("SECRET_KEY", "test-secret-key-com-mais-de-32-caracteres-ok")

from infra.database.models import UserModel, MarketModel
from infra.repositories.pix_repo import (
    MercadoPagoConnectionRepository, MercadoPagoOAuthStateRepository,
)


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _seed_market_and_user(session):
    user_id = uuid.uuid4()
    market_id = uuid.uuid4()
    session.add(UserModel(
        id=user_id, name="Owner Teste", email=f"owner-{user_id}@example.test",
        password_hash="test", role="owner",
    ))
    await session.flush()
    session.add(MarketModel(
        id=market_id, owner_id=user_id, name="Mercado Teste",
        document=str(market_id.int)[:14], address="Rua Teste",
    ))
    await session.flush()
    return market_id, user_id


@pytest.mark.asyncio
async def test_state_consume_is_single_use():
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        market_id, user_id = await _seed_market_and_user(session)
        await session.commit()

        repo = MercadoPagoOAuthStateRepository(session)
        now = datetime.now(timezone.utc)
        state_value = f"st-{uuid.uuid4()}"
        await repo.create(
            state=state_value, market_id=market_id, initiated_by_user_id=user_id,
            code_verifier_ciphertext="enc:x", redirect_uri="https://cb",
            expires_at=now + timedelta(minutes=10),
        )
        first = await repo.consume(state_value, now)
        second = await repo.consume(state_value, now)
        assert first is not None and first.market_id == market_id
        assert second is None  # já consumido

        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_state_consume_rejects_expired():
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        market_id, user_id = await _seed_market_and_user(session)
        await session.commit()

        repo = MercadoPagoOAuthStateRepository(session)
        now = datetime.now(timezone.utc)
        state_value = f"st-{uuid.uuid4()}"
        await repo.create(
            state=state_value, market_id=market_id, initiated_by_user_id=user_id,
            code_verifier_ciphertext=None, redirect_uri="https://cb",
            expires_at=now - timedelta(minutes=1),
        )
        assert await repo.consume(state_value, now) is None

        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_connection_repository_upsert_by_market():
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        market_id, _ = await _seed_market_and_user(session)
        await session.commit()

        from infra.database.models import MercadoPagoConnectionModel
        repo = MercadoPagoConnectionRepository(session)
        assert await repo.get_by_market(market_id) is None

        conn = MercadoPagoConnectionModel(market_id=market_id, status="connected", mp_user_id="42")
        await repo.save(conn)
        found = await repo.get_by_market(market_id)
        assert found is not None
        assert found.mp_user_id == "42"

        await session.rollback()
    await engine.dispose()
