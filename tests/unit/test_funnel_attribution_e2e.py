"""Sessão → cadastro → trial → assinatura → pagamento → métricas, com sessões de banco separadas."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from funnel_fixtures import db, engine, seed_admin, seed_funnel, session_factory  # noqa: F401
from application.services.funnel_attribution_service import FunnelAttributionService
from application.services.funnel_metrics import aggregate_metrics
from application.services.funnel_public_service import FunnelPublicService
from infra.observability.funnel_analytics import FunnelTrackingAnalytics
from infra.repositories.funnel_metrics_repo import FunnelMetricsRepository
from infra.repositories.funnel_repo import FunnelRepository


class _NoopPostHog:
    async def track_event(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_visit_to_paid(db, session_factory):
    funnel, variant = await seed_funnel(db, status="published", steps=2)
    await db.commit()

    public = FunnelPublicService(FunnelRepository(db))
    started = await public.start_session("oferta", fsid=None, utm={"utm_source": "meta"}, referrer=None)
    await public.record_event(started["fsid"], "step_view", 0)
    await public.record_event(started["fsid"], "step_view", 1)
    await public.record_event(started["fsid"], "funnel_completed", None)
    await db.commit()

    async with session_factory() as s:
        user = await seed_admin(s, role="owner")
        await s.commit()

    attribution = FunnelAttributionService(session_factory)
    analytics = FunnelTrackingAnalytics(_NoopPostHog(), attribution)
    assert await attribution.link_registration(started["fsid"], user.id) is True
    await analytics.track_event(str(user.id), "trial_activated", {})
    await analytics.track_event(str(user.id), "subscription_created", {})
    await analytics.track_event(str(user.id), "invoice_paid", {"amount": "49.90"})
    await analytics.track_event(str(user.id), "invoice_paid", {"amount": "49.90"})  # renovação: ignorada

    async with session_factory() as s:
        metrics = FunnelMetricsRepository(s)
        now = datetime.now(timezone.utc)
        rows = await metrics.cohort_rows(funnel.id, now - timedelta(days=1), now + timedelta(minutes=1))
        out = aggregate_metrics(rows=rows, step_views=await metrics.step_view_counts([r.id for r in rows]),
                                variants=[(variant.id, "A", 100)], step_names={})
    assert out["summary"] == {"sessions": 1, "completed": 1, "registered": 1, "trials": 1, "subscribed": 1,
                              "paid": 1, "paid_conversion": 1.0, "revenue": "49.90"}
