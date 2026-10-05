from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import select

from funnel_fixtures import db, engine, seed_funnel, session_factory  # noqa: F401
from application.services.funnel_public_service import FunnelGone, FunnelPublicService
from domain.funnels import FunnelError, FunnelNotFound
from infra.database.models import FunnelEventModel
from infra.repositories.funnel_repo import FunnelRepository


def _svc(db):
    repo = FunnelRepository(db)
    return FunnelPublicService(repo, rng=random.Random(1)), repo


@pytest.mark.asyncio
async def test_new_session_captures_utms_and_returns_steps(db):
    svc, repo = _svc(db)
    funnel, variant = await seed_funnel(db, status="published", steps=2)
    data = await svc.start_session(
        "oferta", fsid=None, utm={"utm_source": "meta", "utm_campaign": "bf", "utm_medium": None},
        referrer="https://instagram.com",
    )
    fs = await repo.get_session(data["fsid"])
    assert fs.utm_source == "meta" and fs.utm_campaign == "bf" and fs.referrer == "https://instagram.com"
    assert data["variant"]["id"] == variant.id
    assert [s["position"] for s in data["steps"]] == [0, 1]
    assert data["resume_position"] == 0
    assert data["funnel"]["plan_id"] == funnel.plan_id


@pytest.mark.asyncio
async def test_resume_keeps_session_and_variant(db):
    svc, repo = _svc(db)
    await seed_funnel(db, status="published", steps=3)
    first = await svc.start_session("oferta", fsid=None, utm={}, referrer=None)
    await svc.record_event(first["fsid"], "step_view", 2)
    again = await svc.start_session("oferta", fsid=first["fsid"], utm={"utm_source": "outra"}, referrer=None)
    assert again["fsid"] == first["fsid"]
    assert again["resume_position"] == 2
    assert (await repo.get_session(first["fsid"])).utm_source is None  # UTM só na criação


@pytest.mark.asyncio
async def test_unknown_fsid_creates_new_session(db):
    svc, _ = _svc(db)
    await seed_funnel(db, status="published")
    bogus = uuid.uuid4()
    data = await svc.start_session("oferta", fsid=bogus, utm={}, referrer=None)
    assert data["fsid"] != bogus


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "archived"])
async def test_unpublished_funnel_is_not_found(db, status):
    svc, _ = _svc(db)
    await seed_funnel(db, status=status)
    with pytest.raises(FunnelNotFound):
        await svc.start_session("oferta", fsid=None, utm={}, referrer=None)


@pytest.mark.asyncio
async def test_no_eligible_variant_is_not_found(db):
    svc, _ = _svc(db)
    _, variant = await seed_funnel(db, status="published")
    variant.weight = 0
    await db.flush()
    with pytest.raises(FunnelNotFound):
        await svc.start_session("oferta", fsid=None, utm={}, referrer=None)


@pytest.mark.asyncio
async def test_record_event_rules(db):
    svc, repo = _svc(db)
    funnel, _ = await seed_funnel(db, status="published")
    data = await svc.start_session("oferta", fsid=None, utm={}, referrer=None)
    await svc.record_event(data["fsid"], "step_view", 1)
    await svc.record_event(data["fsid"], "step_view", 1)  # dedupe
    await svc.record_event(data["fsid"], "funnel_completed", None)
    rows = (await db.execute(select(FunnelEventModel.type))).scalars().all()
    assert sorted(rows) == ["funnel_completed", "step_view"]
    fs = await repo.get_session(data["fsid"])
    assert fs.last_step_position == 1 and fs.completed_at is not None

    with pytest.raises(FunnelError) as exc:
        await svc.record_event(data["fsid"], "invoice_paid", None)
    assert exc.value.code == "funnel.invalid_event"
    with pytest.raises(FunnelNotFound):
        await svc.record_event(uuid.uuid4(), "step_view", 0)

    funnel.status = "draft"
    await db.flush()
    with pytest.raises(FunnelGone):
        await svc.record_event(data["fsid"], "step_view", 0)
