"""Repositório SQLAlchemy para BillingInvoiceModel (faturas de assinatura)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.models import BillingInvoiceModel


class SQLAlchemyBillingInvoiceRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(self, **fields) -> BillingInvoiceModel:
        m = BillingInvoiceModel(id=uuid.uuid4(), **fields)
        self._db.add(m)
        try:
            await self._db.flush()
        except IntegrityError:
            await self._db.rollback()
            existing = await self.get_by_idempotency_key(fields.get("idempotency_key"))
            if existing:
                return existing
            raise
        return m

    async def get_by_id(self, invoice_id: uuid.UUID) -> Optional[BillingInvoiceModel]:
        r = await self._db.execute(select(BillingInvoiceModel).where(BillingInvoiceModel.id == invoice_id))
        return r.scalar_one_or_none()

    async def get_by_idempotency_key(self, key: Optional[str]) -> Optional[BillingInvoiceModel]:
        if not key:
            return None
        r = await self._db.execute(select(BillingInvoiceModel).where(BillingInvoiceModel.idempotency_key == key))
        return r.scalar_one_or_none()

    async def get_by_payment_id(self, payment_id: str) -> Optional[BillingInvoiceModel]:
        r = await self._db.execute(select(BillingInvoiceModel).where(BillingInvoiceModel.bc_payment_id == payment_id))
        return r.scalar_one_or_none()

    async def get_by_job_id(self, job_id: str) -> Optional[BillingInvoiceModel]:
        r = await self._db.execute(select(BillingInvoiceModel).where(BillingInvoiceModel.bc_job_id == job_id))
        return r.scalar_one_or_none()

    async def get_latest_pending_by_owner(self, owner_id: uuid.UUID) -> Optional[BillingInvoiceModel]:
        r = await self._db.execute(
            select(BillingInvoiceModel)
            .where(BillingInvoiceModel.owner_id == owner_id, BillingInvoiceModel.status == "pending")
            .order_by(BillingInvoiceModel.created_at.desc())
            .limit(1)
        )
        return r.scalar_one_or_none()

    async def get_open_invoice_for_subscription(self, subscription_id: uuid.UUID) -> Optional[BillingInvoiceModel]:
        r = await self._db.execute(
            select(BillingInvoiceModel)
            .where(BillingInvoiceModel.subscription_id == subscription_id, BillingInvoiceModel.status == "pending")
            .order_by(BillingInvoiceModel.created_at.desc())
            .limit(1)
        )
        return r.scalar_one_or_none()

    async def list_by_owner(self, owner_id: uuid.UUID, limit: int = 20, offset: int = 0) -> List[BillingInvoiceModel]:
        r = await self._db.execute(
            select(BillingInvoiceModel)
            .where(BillingInvoiceModel.owner_id == owner_id)
            .order_by(BillingInvoiceModel.created_at.desc())
            .limit(limit).offset(offset)
        )
        return list(r.scalars().all())

    async def update_checkout(self, invoice_id: uuid.UUID, *, bc_job_id: str | None = None,
                              checkout_url: str | None = None, bc_payment_id: str | None = None,
                              clear_checkout: bool = False) -> None:
        values = {}
        if bc_job_id is not None:
            values["bc_job_id"] = bc_job_id
        if clear_checkout:
            values["checkout_url"] = None
        elif checkout_url is not None:
            values["checkout_url"] = checkout_url
        if bc_payment_id is not None:
            values["bc_payment_id"] = bc_payment_id
        if not values:
            return
        await self._db.execute(update(BillingInvoiceModel).where(BillingInvoiceModel.id == invoice_id).values(**values))
        await self._db.flush()

    async def mark_paid(self, invoice_id: uuid.UUID, *, bc_payment_id: str, paid_at: datetime) -> int:
        result = await self._db.execute(
            update(BillingInvoiceModel)
            .where(BillingInvoiceModel.id == invoice_id, BillingInvoiceModel.status == "pending")
            .values(status="paid", bc_payment_id=bc_payment_id, paid_at=paid_at)
        )
        await self._db.flush()
        return result.rowcount

    async def mark_status(self, invoice_id: uuid.UUID, status: str) -> None:
        await self._db.execute(update(BillingInvoiceModel).where(BillingInvoiceModel.id == invoice_id).values(status=status))
        await self._db.flush()

    async def mark_notified(self, invoice_id: uuid.UUID, when: datetime) -> None:
        await self._db.execute(update(BillingInvoiceModel).where(BillingInvoiceModel.id == invoice_id).values(notified_at=when))
        await self._db.flush()

    async def get_pending_with_payment_id_older_than(self, cutoff: datetime, limit: int = 50) -> List[BillingInvoiceModel]:
        r = await self._db.execute(
            select(BillingInvoiceModel)
            .where(
                BillingInvoiceModel.status == "pending",
                BillingInvoiceModel.bc_payment_id.isnot(None),
                BillingInvoiceModel.created_at < cutoff,
            )
            .limit(limit)
        )
        return list(r.scalars().all())
