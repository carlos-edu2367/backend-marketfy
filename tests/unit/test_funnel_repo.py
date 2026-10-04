from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from funnel_fixtures import db, engine, seed_admin, seed_funnel, seed_plan, session_factory  # noqa: F401
from infra.repositories.funnel_repo import FunnelRepository


@pytest.mark.asyncio
async def test_lookup_by_slug_and_slug_exists(db):
    funnel, _ = await seed_funnel(db, slug="black")
    repo = FunnelRepository(db)
    assert (await repo.get_funnel_by_slug("black")).id == funnel.id
    assert await repo.slug_exists("black") is True
    assert await repo.slug_exists("black", exclude_id=funnel.id) is False
    assert await repo.get_funnel_by_slug("nada") is None


@pytest.mark.asyncio
async def test_steps_ordered_and_next_position(db):
    _, variant = await seed_funnel(db, steps=3)
    repo = FunnelRepository(db)
    assert [s.position for s in await repo.list_steps(variant.id)] == [0, 1, 2]
    assert await repo.next_step_position(variant.id) == 3
    assert await repo.count_steps(variant.id) == 3


@pytest.mark.asyncio
async def test_plan_is_active(db):
    repo = FunnelRepository(db)
    assert await repo.plan_is_active((await seed_plan(db)).id) is True
    assert await repo.plan_is_active((await seed_plan(db, active=False)).id) is False
    assert await repo.plan_is_active(None) is False
    assert await repo.plan_is_active(uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_add_event_dedupes_step_view(db):
    funnel, variant = await seed_funnel(db)
    repo = FunnelRepository(db)
    fs = await repo.create_session(funnel_id=funnel.id, variant_id=variant.id)
    assert await repo.add_event(fs, "step_view", 0) is True
    assert await repo.add_event(fs, "step_view", 0) is False
    assert await repo.add_event(fs, "step_next", 0) is True
    assert await repo.add_event(fs, "step_next", 0) is True
    assert await repo.count_sessions(funnel.id) == 1


@pytest.mark.asyncio
async def test_latest_session_for_user(db):
    funnel, variant = await seed_funnel(db)
    repo = FunnelRepository(db)
    user = await seed_admin(db, role="owner")
    older = await repo.create_session(funnel_id=funnel.id, variant_id=variant.id, user_id=user.id)
    newer = await repo.create_session(funnel_id=funnel.id, variant_id=variant.id, user_id=user.id)
    older.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db.flush()
    assert (await repo.latest_session_for_user(user.id)).id == newer.id
    assert await repo.latest_session_for_user(uuid.uuid4()) is None
