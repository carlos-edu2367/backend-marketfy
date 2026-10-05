from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from funnel_fixtures import db, engine, seed_admin, seed_funnel, session_factory  # noqa: F401
from application.services.funnel_metrics import SessionRow, aggregate_metrics
from infra.repositories.funnel_metrics_repo import FunnelMetricsRepository
from infra.repositories.funnel_repo import FunnelRepository

T0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
VA, VB = uuid.uuid4(), uuid.uuid4()


def _row(variant=VA, *, source=None, paid=False, registered=False, completed=False, day=0, amount="99.90"):
    created = T0 + timedelta(days=day)
    return SessionRow(
        id=uuid.uuid4(), variant_id=variant, utm_source=source, utm_campaign=None, created_at=created,
        completed_at=created if completed or registered or paid else None,
        registered_at=created if registered or paid else None,
        trial_at=created if registered or paid else None,
        subscribed_at=created if paid else None,
        paid_at=created + timedelta(days=3) if paid else None,
        first_payment_amount=Decimal(amount) if paid else None,
    )


def test_aggregate_summary_steps_and_drop():
    rows = [_row(paid=True), _row(registered=True), _row(completed=True), _row()]
    out = aggregate_metrics(rows=rows, step_views={0: 4, 1: 3}, variants=[(VA, "A", 100)],
                            step_names={0: "Intro", 1: "Oferta"})
    assert out["summary"] == {"sessions": 4, "completed": 3, "registered": 2, "trials": 2, "subscribed": 1,
                              "paid": 1, "paid_conversion": 0.25, "revenue": "99.90"}
    keys = [s["key"] for s in out["steps"]]
    assert keys == ["step:0", "step:1", "completed", "registered", "trial", "subscribed", "paid"]
    step1 = out["steps"][1]
    assert step1["label"] == "Oferta" and step1["count"] == 3
    assert step1["pct_of_total"] == 0.75 and step1["drop_from_previous"] == 0.25
    assert out["steps"][0]["drop_from_previous"] is None


def test_aggregate_variants_control_and_low_sample():
    rows = [_row(VA) for _ in range(150)] + [_row(VA, paid=True) for _ in range(5)]
    rows += [_row(VB) for _ in range(140)] + [_row(VB, paid=True) for _ in range(15)]
    out = aggregate_metrics(rows=rows, step_views={}, variants=[(VA, "A", 50), (VB, "B", 50)], step_names={})
    a, b = out["variants"]
    assert a["is_control"] is True and a["confidence"] is None and a["low_sample"] is False
    assert b["is_control"] is False and b["low_sample"] is False and b["confidence"] > 0.9
    assert b["paid"] == 15 and b["revenue_per_session"] == "9.67"


def test_aggregate_sources_and_timeseries():
    rows = [_row(source="meta", paid=True), _row(source="meta"), _row(day=1)]
    out = aggregate_metrics(rows=rows, step_views={}, variants=[(VA, "A", 100)], step_names={})
    assert out["sources"][0] == {"utm_source": "meta", "utm_campaign": None, "sessions": 2,
                                 "registered": 1, "paid": 1, "conversion": 0.5}
    assert out["sources"][1]["utm_source"] is None
    series = {p["date"]: p for p in out["timeseries"]}
    assert series["2026-10-01"]["sessions"] == 2
    assert series["2026-10-04"]["paid"] == 1


def test_aggregate_empty():
    out = aggregate_metrics(rows=[], step_views={}, variants=[(VA, "A", 100)], step_names={0: "x"})
    assert out["summary"]["sessions"] == 0 and out["summary"]["paid_conversion"] == 0.0


@pytest.mark.asyncio
async def test_repo_cohort_excludes_admin_and_out_of_range(db):
    funnel, variant = await seed_funnel(db, status="published")
    admin = await seed_admin(db)
    owner = await seed_admin(db, role="owner")
    repo, metrics = FunnelRepository(db), FunnelMetricsRepository(db)
    inside = await repo.create_session(funnel_id=funnel.id, variant_id=variant.id, user_id=owner.id, utm_source="meta")
    await repo.create_session(funnel_id=funnel.id, variant_id=variant.id, user_id=admin.id)
    old = await repo.create_session(funnel_id=funnel.id, variant_id=variant.id)
    old.created_at = datetime.now(timezone.utc) - timedelta(days=90)
    await repo.add_event(inside, "step_view", 0)
    await repo.add_event(inside, "step_view", 1)
    await db.flush()

    start = datetime.now(timezone.utc) - timedelta(days=30)
    end = datetime.now(timezone.utc) + timedelta(minutes=1)
    rows = await metrics.cohort_rows(funnel.id, start, end)
    assert [r.id for r in rows] == [inside.id]
    assert await metrics.step_view_counts([r.id for r in rows]) == {0: 1, 1: 1}
    assert await metrics.cohort_rows(funnel.id, start, end, utm_source="google") == []
    kpis = await metrics.list_kpis(start)
    assert kpis[funnel.id] == (1, 0)
