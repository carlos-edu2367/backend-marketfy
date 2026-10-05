from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from funnel_fixtures import db, engine, seed_admin, seed_funnel, session_factory  # noqa: F401
from application.services.funnel_attribution_service import FunnelAttributionService
from infra.database.models import FunnelEventModel, FunnelSessionModel
from infra.observability.funnel_analytics import FunnelTrackingAnalytics


async def _session(db, *, user_id=None):
    funnel, variant = await seed_funnel(db, status="published")
    fs = FunnelSessionModel(funnel_id=funnel.id, variant_id=variant.id, user_id=user_id)
    db.add(fs)
    await db.commit()
    return fs


async def _reload(session_factory, fs_id):
    async with session_factory() as s:
        return await s.get(FunnelSessionModel, fs_id)


@pytest.mark.asyncio
async def test_link_registration_sets_user_and_event(db, session_factory):
    fs = await _session(db)
    user = await seed_admin(db, role="owner")
    await db.commit()
    svc = FunnelAttributionService(session_factory)
    assert await svc.link_registration(fs.id, user.id) is True
    stored = await _reload(session_factory, fs.id)
    assert stored.user_id == user.id and stored.registered_at is not None
    async with session_factory() as s:
        types = (await s.execute(select(FunnelEventModel.type))).scalars().all()
    assert types == ["registered"]


@pytest.mark.asyncio
async def test_link_registration_refuses_already_linked_or_unknown(db, session_factory):
    owner = await seed_admin(db, role="owner")
    other = await seed_admin(db, role="owner")
    fs = await _session(db, user_id=owner.id)
    svc = FunnelAttributionService(session_factory)
    assert await svc.link_registration(fs.id, other.id) is False
    assert await svc.link_registration(uuid.uuid4(), other.id) is False
    assert (await _reload(session_factory, fs.id)).user_id == owner.id


@pytest.mark.asyncio
async def test_claim_links_without_registered_milestone(db, session_factory):
    fs = await _session(db)
    user = await seed_admin(db, role="owner")
    await db.commit()
    svc = FunnelAttributionService(session_factory)
    assert await svc.claim(fs.id, user.id) is True
    stored = await _reload(session_factory, fs.id)
    assert stored.user_id == user.id and stored.registered_at is None


@pytest.mark.asyncio
async def test_mark_is_idempotent_and_stores_amount(db, session_factory):
    user = await seed_admin(db, role="owner")
    fs = await _session(db, user_id=user.id)
    svc = FunnelAttributionService(session_factory)
    assert await svc.mark(user.id, "invoice_paid", Decimal("99.90")) is True
    first = (await _reload(session_factory, fs.id)).paid_at
    assert await svc.mark(user.id, "invoice_paid", Decimal("10.00")) is False
    stored = await _reload(session_factory, fs.id)
    assert stored.paid_at == first and stored.first_payment_amount == Decimal("99.90")


@pytest.mark.asyncio
async def test_mark_without_session_or_unknown_event_is_noop(db, session_factory):
    user = await seed_admin(db, role="owner")
    await db.commit()
    svc = FunnelAttributionService(session_factory)
    assert await svc.mark(user.id, "trial_activated") is False
    assert await svc.mark(user.id, "qualquer_coisa") is False


@pytest.mark.asyncio
async def test_mark_swallows_errors():
    def broken_factory():
        raise RuntimeError("db fora")
    svc = FunnelAttributionService(broken_factory)
    assert await svc.mark(uuid.uuid4(), "trial_activated") is False


class _Inner:
    def __init__(self):
        self.calls = []

    async def track_event(self, distinct_id, event, properties=None):
        self.calls.append((distinct_id, event, properties))


class _Attribution:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def mark(self, user_id, event, amount=None):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((user_id, event, amount))
        return True


@pytest.mark.asyncio
async def test_tracking_analytics_forwards_and_marks_milestones():
    inner, attribution = _Inner(), _Attribution()
    analytics = FunnelTrackingAnalytics(inner, attribution)
    uid = uuid.uuid4()
    await analytics.track_event(str(uid), "invoice_paid", {"amount": "49.90"})
    await analytics.track_event(str(uid), "fiscal_credits_purchased", {"quantity": 10})
    assert [c[1] for c in inner.calls] == ["invoice_paid", "fiscal_credits_purchased"]
    assert attribution.calls == [(uid, "invoice_paid", Decimal("49.90"))]


@pytest.mark.asyncio
async def test_tracking_analytics_never_raises():
    analytics = FunnelTrackingAnalytics(_Inner(), _Attribution(fail=True))
    await analytics.track_event(str(uuid.uuid4()), "trial_activated", {})
    await analytics.track_event("nao-e-uuid", "trial_activated", {})
