"""Consultas de leitura dos funis (lista com KPIs e métricas por coorte), compartilhadas pela API admin e pelo MCP."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from application.services.funnel_metrics import aggregate_metrics
from domain.funnels import FunnelNotFound
from infra.repositories.funnel_metrics_repo import FunnelMetricsRepository
from infra.repositories.funnel_repo import FunnelRepository

DEFAULT_PERIOD_DAYS = 30
MATURING_DAYS = 14


async def list_funnels_with_kpis(db: AsyncSession) -> list[dict]:
    repo, metrics = FunnelRepository(db), FunnelMetricsRepository(db)
    kpis = await metrics.list_kpis(datetime.now(timezone.utc) - timedelta(days=DEFAULT_PERIOD_DAYS))
    out = []
    for f in await repo.list_funnels():
        sessions, paid = kpis.get(f.id, (0, 0))
        out.append({"id": f.id, "slug": f.slug, "name": f.name, "status": f.status, "plan_id": f.plan_id,
                    "variant_count": len(await repo.list_variants(f.id)), "sessions_30d": sessions,
                    "paid_30d": paid, "paid_conversion_30d": round(paid / sessions, 4) if sessions else 0.0})
    return out


async def funnel_metrics_report(
    db: AsyncSession,
    funnel_id: uuid.UUID,
    *,
    from_: Optional[date] = None,
    to: Optional[date] = None,
    variant_id: Optional[uuid.UUID] = None,
    utm_source: Optional[str] = None,
    utm_campaign: Optional[str] = None,
) -> dict:
    repo = FunnelRepository(db)
    if await repo.get_funnel(funnel_id) is None:
        raise FunnelNotFound()
    today = datetime.now(timezone.utc).date()
    end_day = to or today
    start_day = from_ or (end_day - timedelta(days=DEFAULT_PERIOD_DAYS - 1))
    start = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
    end = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=timezone.utc)

    metrics = FunnelMetricsRepository(db)
    rows = await metrics.cohort_rows(funnel_id, start, end, variant_id=variant_id,
                                     utm_source=utm_source, utm_campaign=utm_campaign)
    variants = await repo.list_variants(funnel_id)
    # Nomes das etapas vêm da primeira variante (as posições são compartilhadas entre variantes).
    first_steps = await repo.list_steps(variants[0].id) if variants else []
    data = aggregate_metrics(
        rows=rows,
        step_views=await metrics.step_view_counts([r.id for r in rows]),
        variants=[(v.id, v.name, v.weight) for v in variants if variant_id is None or v.id == variant_id],
        step_names={s.position: s.name for s in first_steps},
    )
    data["period"] = {"from": start_day.isoformat(), "to": end_day.isoformat()}
    data["maturing"] = (today - end_day).days < MATURING_DAYS
    return data
