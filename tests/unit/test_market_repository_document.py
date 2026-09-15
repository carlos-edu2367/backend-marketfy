from __future__ import annotations

import os
import sys
import uuid

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from infra.database.models import Base
from infra.repositories.sqlalchemy_repos import SQLAlchemyMarketRepository
from domain.identity import Market


@pytest.mark.asyncio
async def test_market_document_round_trips_as_plain_string_cpf_length():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as session:
        repo = SQLAlchemyMarketRepository(session)
        market = Market(owner_id=uuid.uuid4(), name="Loja", document="11144477735", address="Rua X")

        saved = await repo.save(market)
        assert saved.document == "11144477735"

        from infra.database.models import MarketModel
        model = await session.get(MarketModel, saved.id)
        reloaded = repo._to_entity(model)
        assert reloaded.document == "11144477735"


@pytest.mark.asyncio
async def test_market_document_round_trips_as_plain_string_cnpj_length():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as session:
        repo = SQLAlchemyMarketRepository(session)
        market = Market(owner_id=uuid.uuid4(), name="Loja", document="12345678000195", address="Rua X")

        saved = await repo.save(market)

        from infra.database.models import MarketModel
        model = await session.get(MarketModel, saved.id)
        reloaded = repo._to_entity(model)
        assert reloaded.document == "12345678000195"
