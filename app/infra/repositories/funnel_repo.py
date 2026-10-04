"""Repositório SQLAlchemy de funis de venda. Só faz flush; quem chama decide o commit."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.models import (
    FunnelEventModel, FunnelModel, FunnelSessionModel, FunnelStepModel, FunnelVariantModel, PlanModel,
)


class FunnelRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    # -- funis --------------------------------------------------------------
    async def create_funnel(self, **fields) -> FunnelModel:
        funnel = FunnelModel(id=uuid.uuid4(), **fields)
        self._db.add(funnel)
        await self._db.flush()
        return funnel

    async def get_funnel(self, funnel_id: uuid.UUID) -> Optional[FunnelModel]:
        return await self._db.get(FunnelModel, funnel_id)

    async def get_funnel_by_slug(self, slug: str) -> Optional[FunnelModel]:
        res = await self._db.execute(select(FunnelModel).where(FunnelModel.slug == slug))
        return res.scalar_one_or_none()

    async def list_funnels(self) -> list[FunnelModel]:
        res = await self._db.execute(select(FunnelModel).order_by(FunnelModel.created_at.desc()))
        return list(res.scalars())

    async def slug_exists(self, slug: str, exclude_id: Optional[uuid.UUID] = None) -> bool:
        stmt = select(func.count()).select_from(FunnelModel).where(FunnelModel.slug == slug)
        if exclude_id is not None:
            stmt = stmt.where(FunnelModel.id != exclude_id)
        return (await self._db.execute(stmt)).scalar_one() > 0

    async def plan_is_active(self, plan_id: Optional[uuid.UUID]) -> bool:
        if plan_id is None:
            return False
        plan = await self._db.get(PlanModel, plan_id)
        return bool(plan and plan.is_active)

    # -- variantes ----------------------------------------------------------
    async def add_variant(self, **fields) -> FunnelVariantModel:
        variant = FunnelVariantModel(id=uuid.uuid4(), **fields)
        self._db.add(variant)
        await self._db.flush()
        return variant

    async def get_variant(self, variant_id: uuid.UUID) -> Optional[FunnelVariantModel]:
        return await self._db.get(FunnelVariantModel, variant_id)

    async def list_variants(self, funnel_id: uuid.UUID) -> list[FunnelVariantModel]:
        res = await self._db.execute(
            select(FunnelVariantModel)
            .where(FunnelVariantModel.funnel_id == funnel_id)
            .order_by(FunnelVariantModel.position, FunnelVariantModel.created_at)
        )
        return list(res.scalars())

    async def delete(self, obj) -> None:
        await self._db.delete(obj)
        await self._db.flush()

    # -- etapas -------------------------------------------------------------
    async def add_step(self, **fields) -> FunnelStepModel:
        step = FunnelStepModel(id=uuid.uuid4(), **fields)
        self._db.add(step)
        await self._db.flush()
        return step

    async def get_step(self, step_id: uuid.UUID) -> Optional[FunnelStepModel]:
        return await self._db.get(FunnelStepModel, step_id)

    async def list_steps(self, variant_id: uuid.UUID) -> list[FunnelStepModel]:
        res = await self._db.execute(
            select(FunnelStepModel).where(FunnelStepModel.variant_id == variant_id).order_by(FunnelStepModel.position)
        )
        return list(res.scalars())

    async def next_step_position(self, variant_id: uuid.UUID) -> int:
        res = await self._db.execute(
            select(func.max(FunnelStepModel.position)).where(FunnelStepModel.variant_id == variant_id)
        )
        current = res.scalar_one()
        return 0 if current is None else current + 1

    async def count_steps(self, variant_id: uuid.UUID) -> int:
        res = await self._db.execute(
            select(func.count()).select_from(FunnelStepModel).where(FunnelStepModel.variant_id == variant_id)
        )
        return res.scalar_one()

    # -- sessões e eventos --------------------------------------------------
    async def count_sessions(self, funnel_id: uuid.UUID) -> int:
        res = await self._db.execute(
            select(func.count()).select_from(FunnelSessionModel).where(FunnelSessionModel.funnel_id == funnel_id)
        )
        return res.scalar_one()

    async def create_session(self, **fields) -> FunnelSessionModel:
        fs = FunnelSessionModel(id=uuid.uuid4(), **fields)
        self._db.add(fs)
        await self._db.flush()
        return fs

    async def get_session(self, session_id: uuid.UUID) -> Optional[FunnelSessionModel]:
        return await self._db.get(FunnelSessionModel, session_id)

    async def latest_session_for_user(self, user_id: uuid.UUID) -> Optional[FunnelSessionModel]:
        res = await self._db.execute(
            select(FunnelSessionModel)
            .where(FunnelSessionModel.user_id == user_id)
            .order_by(FunnelSessionModel.created_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def add_event(self, session: FunnelSessionModel, type: str, step_position: Optional[int] = None) -> bool:
        event = FunnelEventModel(
            id=uuid.uuid4(), session_id=session.id, funnel_id=session.funnel_id,
            variant_id=session.variant_id, type=type, step_position=step_position,
        )
        try:
            async with self._db.begin_nested():
                self._db.add(event)
            return True
        except IntegrityError:
            return False
