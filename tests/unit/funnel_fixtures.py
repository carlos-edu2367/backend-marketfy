"""Fixtures compartilhadas pelos testes de funis (importe com `from funnel_fixtures import ...`)."""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.database.setup import Base
import infra.database.models  # noqa: F401
from infra.database.models import FunnelModel, FunnelStepModel, FunnelVariantModel, PlanModel, UserModel


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(session_factory):
    async with session_factory() as s:
        yield s


async def seed_admin(db, *, role="admin") -> UserModel:
    user = UserModel(id=uuid.uuid4(), name=role, email=f"{uuid.uuid4()}@t.com", password_hash="h", role=role)
    db.add(user)
    await db.flush()
    return user


async def seed_plan(db, *, active=True) -> PlanModel:
    plan = PlanModel(id=uuid.uuid4(), name="Pro", type="pago", max_markets=1, max_terminals=1,
                     price_monthly=Decimal("99.90"), is_active=active)
    db.add(plan)
    await db.flush()
    return plan


async def seed_funnel(db, *, slug="oferta", status="draft", steps=2, plan=None):
    admin = await seed_admin(db)
    plan = plan or await seed_plan(db)
    funnel = FunnelModel(slug=slug, name="Oferta", status=status, plan_id=plan.id, created_by=admin.id)
    db.add(funnel)
    await db.flush()
    variant = FunnelVariantModel(funnel_id=funnel.id, name="A", weight=100, position=0)
    db.add(variant)
    await db.flush()
    for i in range(steps):
        db.add(FunnelStepModel(variant_id=variant.id, position=i, name=f"Etapa {i + 1}", html=f"<p>{i}</p>"))
    await db.flush()
    return funnel, variant
