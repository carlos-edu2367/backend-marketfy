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
import infra.database.models  # noqa: F401  (registra os models)
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


def _create_dto(**overrides):
    data = dict(
        name="Essencial", type="pago", max_markets=1, max_terminals=2,
        price_monthly=Decimal("79.90"), price_180days=Decimal("429.90"),
        price_annual=Decimal("799.90"), fiscal_monthly_limit=200,
    )
    data.update(overrides)
    return PlanCreateDTO(**data)


async def _reload(session, plan_id):
    session.expunge_all()
    return await SQLAlchemyPlanRepository(session).get_by_id(plan_id)


@pytest.mark.asyncio
async def test_create_plan_persists_fiscal_monthly_limit(session):
    service = AdminService(SQLAlchemyPlanRepository(session))

    plan = await service.create_plan(_create_dto())

    assert (await _reload(session, plan.id)).fiscal_monthly_limit == 200


@pytest.mark.asyncio
async def test_update_plan_changes_fiscal_monthly_limit(session):
    service = AdminService(SQLAlchemyPlanRepository(session))
    plan = await service.create_plan(_create_dto())

    await service.update_plan(plan.id, PlanUpdateDTO(fiscal_monthly_limit=500))

    assert (await _reload(session, plan.id)).fiscal_monthly_limit == 500


@pytest.mark.asyncio
async def test_update_without_fiscal_limit_keeps_current_value(session):
    service = AdminService(SQLAlchemyPlanRepository(session))
    plan = await service.create_plan(_create_dto())

    await service.update_plan(plan.id, PlanUpdateDTO(name="Essencial 2"))

    assert (await _reload(session, plan.id)).fiscal_monthly_limit == 200


def test_negative_fiscal_limit_is_rejected():
    with pytest.raises(ValidationError):
        _create_dto(fiscal_monthly_limit=-1)
