"""Consultas de métricas de funil: coorte de sessões, contagem por etapa e KPIs da lista."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.services.funnel_metrics import SessionRow
from infra.database.models import FunnelEventModel, FunnelSessionModel, UserModel

_S = FunnelSessionModel


def _not_admin():
    return or_(_S.user_id.is_(None), UserModel.role != "admin")


class FunnelMetricsRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def cohort_rows(self, funnel_id: uuid.UUID, start: datetime, end: datetime, *,
                          variant_id: Optional[uuid.UUID] = None, utm_source: Optional[str] = None,
                          utm_campaign: Optional[str] = None) -> list[SessionRow]:
        stmt = (
            select(_S.id, _S.variant_id, _S.utm_source, _S.utm_campaign, _S.created_at, _S.completed_at,
                   _S.registered_at, _S.trial_at, _S.subscribed_at, _S.paid_at, _S.first_payment_amount)
            .outerjoin(UserModel, UserModel.id == _S.user_id)
            .where(_S.funnel_id == funnel_id, _S.created_at >= start, _S.created_at < end, _not_admin())
            .order_by(_S.created_at)
        )
        if variant_id is not None:
            stmt = stmt.where(_S.variant_id == variant_id)
        if utm_source is not None:
            stmt = stmt.where(_S.utm_source == utm_source)
        if utm_campaign is not None:
            stmt = stmt.where(_S.utm_campaign == utm_campaign)
        return [SessionRow(*row) for row in (await self._db.execute(stmt)).all()]

    async def step_view_counts(self, session_ids: list[uuid.UUID]) -> dict[int, int]:
        if not session_ids:
            return {}
        counts: dict[int, int] = {}
        for i in range(0, len(session_ids), 500):  # evita IN gigante
            chunk = session_ids[i:i + 500]
            stmt = (
                select(FunnelEventModel.step_position, func.count(func.distinct(FunnelEventModel.session_id)))
                .where(FunnelEventModel.type == "step_view", FunnelEventModel.session_id.in_(chunk))
                .group_by(FunnelEventModel.step_position)
            )
            for position, value in (await self._db.execute(stmt)).all():
                counts[position] = counts.get(position, 0) + value
        return counts

    async def list_kpis(self, since: datetime) -> dict[uuid.UUID, tuple[int, int]]:
        stmt = (
            select(_S.funnel_id, func.count(_S.id), func.sum(case((_S.paid_at.isnot(None), 1), else_=0)))
            .outerjoin(UserModel, UserModel.id == _S.user_id)
            .where(and_(_S.created_at >= since, _not_admin()))
            .group_by(_S.funnel_id)
        )
        return {fid: (int(n), int(p or 0)) for fid, n, p in (await self._db.execute(stmt)).all()}
