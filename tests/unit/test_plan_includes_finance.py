from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

import pytest
import pytest_asyncio
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
from application.services.plan_access_service import PlanAccessService, PlanFeature
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
async def test_new_plan_includes_finance_by_default(session):
    """Default true: um campo novo nunca some silenciosamente de planos existentes."""
    service = AdminService(SQLAlchemyPlanRepository(session))

    await service.create_plan(_dto("Padrão"))

    assert (await _reload_all(session))["Padrão"].includes_finance is True


@pytest.mark.asyncio
async def test_admin_can_exclude_finance_from_a_plan(session):
    service = AdminService(SQLAlchemyPlanRepository(session))
    plan = await service.create_plan(_dto("Básico", includes_finance=True))

    await service.update_plan(plan.id, PlanUpdateDTO(includes_finance=False))

    assert (await _reload_all(session))["Básico"].includes_finance is False


# --- PlanAccessService.get_plan_features / check_feature respeitam o flag ---

@dataclass
class StubSub:
    owner_id: uuid.UUID
    plan_id: Optional[uuid.UUID] = None
    status: str = "active"
    billing_mode: str = "invoice"
    expires_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class StubPlan:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Básico"
    type: str = "pago"
    is_active: bool = True
    max_markets: int = 1
    max_terminals: int = 1
    includes_finance: bool = False


class SubRepo:
    def __init__(self, sub):
        self._sub = sub

    async def get_active_by_owner(self, owner_id):
        return self._sub

    async def get_current_for_owner(self, owner_id):
        return self._sub


class PlanRepo:
    def __init__(self, plan):
        self._plan = plan

    async def get_by_id(self, plan_id):
        return self._plan


class UserRepo:
    async def get_by_id(self, uid):
        return None


def _svc(plan, expires_in_days=30):
    owner = uuid.uuid4()
    sub = StubSub(owner_id=owner, plan_id=plan.id, status="active", expires_at=datetime.utcnow() + timedelta(days=expires_in_days))
    return owner, PlanAccessService(UserRepo(), PlanRepo(plan), SubRepo(sub))


@pytest.mark.asyncio
async def test_get_plan_features_hides_finance_when_plan_excludes_it():
    owner, service = _svc(StubPlan(includes_finance=False))

    features = await service.get_plan_features(owner)

    assert features["features"][PlanFeature.FINANCE] is False
    # Reports não é tocado por essa mudança — continua liberado pra qualquer pago.
    assert features["features"][PlanFeature.REPORTS] is True


@pytest.mark.asyncio
async def test_get_plan_features_shows_finance_when_plan_includes_it():
    owner, service = _svc(StubPlan(includes_finance=True, name="PRO"))

    features = await service.get_plan_features(owner)

    assert features["features"][PlanFeature.FINANCE] is True


@pytest.mark.asyncio
async def test_check_feature_blocks_finance_for_a_plan_that_excludes_it():
    owner, service = _svc(StubPlan(includes_finance=False))

    result = await service.check_feature(owner, PlanFeature.FINANCE)

    assert result.allowed is False


@pytest.mark.asyncio
async def test_check_feature_allows_finance_for_a_plan_that_includes_it():
    owner, service = _svc(StubPlan(includes_finance=True, name="PRO"))

    result = await service.check_feature(owner, PlanFeature.FINANCE)

    assert result.allowed is True
