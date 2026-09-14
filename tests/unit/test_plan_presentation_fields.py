from __future__ import annotations

import os
import sys
from decimal import Decimal

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.database.setup import Base
import infra.database.models  # noqa: F401
from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository
from application.services.admin_service import AdminService
from application.dtos import PlanCreateDTO, PlanUpdateDTO


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _dto(name, **overrides):
    data = dict(
        name=name, type="pago", max_markets=1, max_terminals=2,
        price_monthly=Decimal("79.90"), price_180days=Decimal("429.90"),
        price_annual=Decimal("799.90"),
    )
    data.update(overrides)
    return PlanCreateDTO(**data)


async def _reload_all(session):
    session.expunge_all()
    return {p.name: p for p in await SQLAlchemyPlanRepository(session).list_all()}


@pytest.mark.asyncio
async def test_presentation_fields_round_trip(session):
    service = AdminService(SQLAlchemyPlanRepository(session))

    await service.create_plan(_dto("Pro", description="  Para quem tem 2 caixas.  ", display_order=2, is_recommended=True))

    pro = (await _reload_all(session))["Pro"]
    assert pro.description == "Para quem tem 2 caixas."
    assert pro.display_order == 2
    assert pro.is_recommended is True


@pytest.mark.asyncio
async def test_only_one_plan_stays_recommended(session):
    service = AdminService(SQLAlchemyPlanRepository(session))
    essencial = await service.create_plan(_dto("Essencial", is_recommended=True))
    pro = await service.create_plan(_dto("Pro"))

    await service.update_plan(pro.id, PlanUpdateDTO(is_recommended=True))

    plans = await _reload_all(session)
    assert plans["Pro"].is_recommended is True
    assert plans["Essencial"].is_recommended is False


@pytest.mark.asyncio
async def test_update_can_clear_description(session):
    service = AdminService(SQLAlchemyPlanRepository(session))
    plan = await service.create_plan(_dto("Pro", description="Texto antigo"))

    await service.update_plan(plan.id, PlanUpdateDTO(description=None))

    assert (await _reload_all(session))["Pro"].description is None


@pytest.mark.asyncio
async def test_update_without_description_keeps_it(session):
    service = AdminService(SQLAlchemyPlanRepository(session))
    plan = await service.create_plan(_dto("Pro", description="Mantém"))

    await service.update_plan(plan.id, PlanUpdateDTO(name="Pro"))

    assert (await _reload_all(session))["Pro"].description == "Mantém"


def test_description_longer_than_280_chars_is_rejected():
    with pytest.raises(ValidationError):
        _dto("Pro", description="x" * 281)
