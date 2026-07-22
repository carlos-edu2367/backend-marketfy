from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.database.setup import Base
import infra.database.models  # noqa: F401  (registra os models)
from infra.database.models import UserModel, PlanModel, BillingSubscriptionModel
from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository
from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _seed(session):
    owner_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    session.add(PlanModel(id=plan_id, name="PRO", type="pago", max_markets=5,
                          max_terminals=10, price_monthly=Decimal("50")))
    session.add(UserModel(id=owner_id, name="x", email=f"{owner_id}@t.com",
                          password_hash="h", role="owner"))
    sub = BillingSubscriptionModel(owner_id=owner_id, plan_id=plan_id,
                                   billing_mode="invoice", status="pending",
                                   billing_system_sub_id=str(owner_id))
    session.add(sub)
    await session.flush()
    return owner_id, plan_id, sub.id


@pytest.mark.asyncio
async def test_create_and_get_latest_pending(session):
    owner_id, plan_id, sub_id = await _seed(session)
    repo = SQLAlchemyBillingInvoiceRepository(session)
    now = datetime.utcnow()
    inv = await repo.create(
        owner_id=owner_id, subscription_id=sub_id, plan_id=plan_id,
        period_start=now, period_end=now + timedelta(days=30), due_date=now,
        amount=Decimal("50.00"), idempotency_key=f"inv-{sub_id}-1",
    )
    assert inv.status == "pending"
    latest = await repo.get_latest_pending_by_owner(owner_id)
    assert latest is not None and latest.id == inv.id


@pytest.mark.asyncio
async def test_mark_paid_is_idempotent(session):
    owner_id, plan_id, sub_id = await _seed(session)
    repo = SQLAlchemyBillingInvoiceRepository(session)
    now = datetime.utcnow()
    inv = await repo.create(
        owner_id=owner_id, subscription_id=sub_id, plan_id=plan_id,
        period_start=now, period_end=now + timedelta(days=30), due_date=now,
        amount=Decimal("50.00"), idempotency_key=f"inv-{sub_id}-1",
    )
    first = await repo.mark_paid(inv.id, bc_payment_id="pay_1", paid_at=now)
    second = await repo.mark_paid(inv.id, bc_payment_id="pay_1", paid_at=now)
    assert first == 1
    assert second == 0


@pytest.mark.asyncio
async def test_subscription_create_if_absent_reuses_the_idempotent_retry(session):
    owner_id, plan_id, _ = await _seed(session)
    repo = SQLAlchemyBillingSubscriptionRepository(session)
    key = "invoice-retry:invoice-1"
    first = BillingSubscriptionModel(
        owner_id=owner_id,
        plan_id=plan_id,
        billing_mode="invoice",
        status="pending",
        billing_system_sub_id=str(owner_id),
        idempotency_key=key,
    )
    second = BillingSubscriptionModel(
        owner_id=owner_id,
        plan_id=plan_id,
        billing_mode="invoice",
        status="pending",
        billing_system_sub_id=str(owner_id),
        idempotency_key=key,
    )

    created, was_created = await repo.create_if_absent_by_idempotency_key(first)
    existing, was_created_again = await repo.create_if_absent_by_idempotency_key(second)

    assert was_created is True
    assert was_created_again is False
    assert existing.id == created.id
