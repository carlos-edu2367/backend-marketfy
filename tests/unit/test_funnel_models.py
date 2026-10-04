from __future__ import annotations

import os
import sys
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.database.setup import Base
import infra.database.models  # noqa: F401
from infra.database.models import (
    FunnelEventModel, FunnelModel, FunnelSessionModel, FunnelStepModel, FunnelVariantModel, UserModel,
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _funnel(session):
    admin = UserModel(id=uuid.uuid4(), name="adm", email=f"{uuid.uuid4()}@t.com", password_hash="h", role="admin")
    session.add(admin)
    await session.flush()
    funnel = FunnelModel(slug="oferta", name="Oferta", created_by=admin.id)
    session.add(funnel)
    await session.flush()
    variant = FunnelVariantModel(funnel_id=funnel.id, name="A", weight=100, position=0)
    session.add(variant)
    await session.flush()
    return funnel, variant


@pytest.mark.asyncio
async def test_defaults_and_relations(session):
    funnel, variant = await _funnel(session)
    session.add(FunnelStepModel(variant_id=variant.id, position=0, name="Intro", html="<h1>Oi</h1>"))
    fs = FunnelSessionModel(funnel_id=funnel.id, variant_id=variant.id, utm_source="meta")
    session.add(fs)
    await session.flush()
    session.add(FunnelEventModel(session_id=fs.id, funnel_id=funnel.id, variant_id=variant.id,
                                 type="step_view", step_position=0))
    await session.flush()
    assert funnel.status == "draft"
    assert variant.is_active is True
    assert fs.created_at is not None


@pytest.mark.asyncio
async def test_step_position_unique_per_variant(session):
    _, variant = await _funnel(session)
    session.add(FunnelStepModel(variant_id=variant.id, position=0, name="1", html=""))
    session.add(FunnelStepModel(variant_id=variant.id, position=0, name="2", html=""))
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_step_view_unique_per_session_position(session):
    funnel, variant = await _funnel(session)
    fs = FunnelSessionModel(funnel_id=funnel.id, variant_id=variant.id)
    session.add(fs)
    await session.flush()
    common = dict(session_id=fs.id, funnel_id=funnel.id, variant_id=variant.id, step_position=0)
    session.add(FunnelEventModel(type="step_next", **common))
    session.add(FunnelEventModel(type="step_next", **common))
    await session.flush()  # step_next pode repetir
    session.add(FunnelEventModel(type="step_view", **common))
    session.add(FunnelEventModel(type="step_view", **common))
    with pytest.raises(IntegrityError):
        await session.flush()
