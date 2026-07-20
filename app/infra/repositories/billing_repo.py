"""Repositório SQLAlchemy para BillingSubscriptionModel e BillingEventModel.

Fase 4 / PR 13.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.models import BillingSubscriptionModel, BillingEventModel


class SQLAlchemyBillingSubscriptionRepository:
    """CRUD para billing_subscriptions."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_active_by_owner(self, owner_id: uuid.UUID) -> Optional[BillingSubscriptionModel]:
        """Retorna a assinatura mais recente e operacionalmente válida do owner.

        Prioriza status trialing > active > past_due > pending.
        Retorna a mais recente pelo updated_at em caso de empate.
        """
        result = await self._db.execute(
            select(BillingSubscriptionModel)
            .where(BillingSubscriptionModel.owner_id == owner_id)
            .order_by(
                BillingSubscriptionModel.updated_at.desc()
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, subscription_id: uuid.UUID) -> Optional[BillingSubscriptionModel]:
        result = await self._db.execute(
            select(BillingSubscriptionModel).where(BillingSubscriptionModel.id == subscription_id)
        )
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(self, key: str) -> Optional[BillingSubscriptionModel]:
        result = await self._db.execute(
            select(BillingSubscriptionModel)
            .where(BillingSubscriptionModel.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def get_by_billing_subscription_id(self, billing_sub_id: str) -> Optional[BillingSubscriptionModel]:
        result = await self._db.execute(
            select(BillingSubscriptionModel)
            .where(BillingSubscriptionModel.billing_subscription_id == billing_sub_id)
        )
        return result.scalar_one_or_none()

    async def list_by_owner(self, owner_id: uuid.UUID) -> List[BillingSubscriptionModel]:
        result = await self._db.execute(
            select(BillingSubscriptionModel)
            .where(BillingSubscriptionModel.owner_id == owner_id)
            .order_by(BillingSubscriptionModel.created_at.desc())
        )
        return list(result.scalars().all())

    async def save(self, sub: BillingSubscriptionModel) -> BillingSubscriptionModel:
        self._db.add(sub)
        await self._db.flush()
        return sub

    async def list_invoice_subs_expiring_within(self, cutoff: datetime) -> List[BillingSubscriptionModel]:
        """Assinaturas modo invoice, ativas, com expires_at até o cutoff (janela de faturamento)."""
        result = await self._db.execute(
            select(BillingSubscriptionModel).where(
                BillingSubscriptionModel.billing_mode == "invoice",
                BillingSubscriptionModel.status == "active",
                BillingSubscriptionModel.expires_at.isnot(None),
                BillingSubscriptionModel.expires_at <= cutoff,
            )
        )
        return list(result.scalars().all())


class SQLAlchemyBillingEventRepository:
    """CRUD para billing_events."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_by_event_id(self, event_id: str) -> Optional[BillingEventModel]:
        result = await self._db.execute(
            select(BillingEventModel).where(BillingEventModel.event_id == event_id)
        )
        return result.scalar_one_or_none()

    async def save(self, event: BillingEventModel) -> BillingEventModel:
        self._db.add(event)
        await self._db.flush()
        return event

    async def list_by_subscription(self, subscription_id: uuid.UUID) -> List[BillingEventModel]:
        result = await self._db.execute(
            select(BillingEventModel)
            .where(BillingEventModel.subscription_id == subscription_id)
            .order_by(BillingEventModel.created_at.desc())
        )
        return list(result.scalars().all())
