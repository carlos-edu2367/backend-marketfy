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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)
os.environ.setdefault("DATABASE_URL", TEST_POSTGRES_URL or "postgresql://x:y@localhost/z")
os.environ.setdefault("SECRET_KEY", "test-secret-key-com-mais-de-32-caracteres-ok")

from infra.database.models import UserModel, MarketModel, TerminalModel, BoxModel, SaleModel, PixPaymentAttemptModel
from infra.repositories.pix_repo import PixPaymentAttemptRepository


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _seed(session):
    user_id = uuid.uuid4()
    market_id = uuid.uuid4()
    terminal_id = uuid.uuid4()
    box_id = uuid.uuid4()
    session.add(UserModel(
        id=user_id, name="Operador Teste", email=f"op-{user_id}@example.test",
        password_hash="test", role="operator",
    ))
    await session.flush()
    session.add(MarketModel(
        id=market_id, owner_id=user_id, name="Mercado Teste",
        document=str(market_id.int)[:14], address="Rua Teste",
    ))
    await session.flush()
    session.add(TerminalModel(id=terminal_id, market_id=market_id, name="Terminal 1"))
    await session.flush()
    session.add(BoxModel(id=box_id, market_id=market_id, terminal_id=terminal_id, operator_id=user_id))
    await session.flush()
    return market_id, user_id, terminal_id, box_id


@pytest.mark.asyncio
async def test_active_attempt_unique_per_sale():
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        market_id, user_id, terminal_id, box_id = await _seed(session)
        await session.commit()

        repo = PixPaymentAttemptRepository(session)
        sale_id = uuid.uuid4()
        session.add(SaleModel(
            id=sale_id, market_id=market_id, box_id=box_id, operator_id=user_id,
            status="aguardando_pagamento", total_amount=Decimal("10.00"),
        ))
        await session.flush()
        a = PixPaymentAttemptModel(
            market_id=market_id, sale_id=sale_id, box_id=box_id, terminal_id=terminal_id,
            operator_id=user_id, amount=Decimal("10.00"), external_reference=f"pix-{sale_id.hex}",
            idempotency_key=f"k-{uuid.uuid4()}", status="pending",
        )
        await repo.save(a)
        found = await repo.get_active_by_sale(sale_id)
        assert found is not None and found.external_reference == f"pix-{sale_id.hex}"

        await session.rollback()
    await engine.dispose()
