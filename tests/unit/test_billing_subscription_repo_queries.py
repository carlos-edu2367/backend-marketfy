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


async def _seed_owner_and_plan(session):
    owner_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    session.add(PlanModel(id=plan_id, name="PRO", type="pago", max_markets=5,
                          max_terminals=10, price_monthly=Decimal("50")))
    session.add(UserModel(id=owner_id, name="x", email=f"{owner_id}@t.com",
                          password_hash="h", role="owner"))
    await session.flush()
    return owner_id, plan_id


@pytest.mark.asyncio
async def test_list_invoice_subs_expiring_within_excludes_cancel_at_period_end(session):
    owner_id, plan_id = await _seed_owner_and_plan(session)
    now = datetime.utcnow()
    repo = SQLAlchemyBillingSubscriptionRepository(session)

    keep = BillingSubscriptionModel(
        owner_id=owner_id, plan_id=plan_id, billing_mode="invoice", status="active",
        expires_at=now + timedelta(days=1), cancel_at_period_end=False,
    )
    skip = BillingSubscriptionModel(
        owner_id=owner_id, plan_id=plan_id, billing_mode="invoice", status="active",
        expires_at=now + timedelta(days=1), cancel_at_period_end=True,
    )
    session.add_all([keep, skip])
    await session.flush()

    result = await repo.list_invoice_subs_expiring_within(now + timedelta(days=5))
    result_ids = {s.id for s in result}
    assert keep.id in result_ids
    assert skip.id not in result_ids


@pytest.mark.asyncio
async def test_get_current_for_owner_prefers_active_over_abandoned_pending(session):
    owner_id, plan_id = await _seed_owner_and_plan(session)
    now = datetime.utcnow()
    repo = SQLAlchemyBillingSubscriptionRepository(session)

    active = BillingSubscriptionModel(
        owner_id=owner_id, plan_id=plan_id, billing_mode="invoice", status="active",
        expires_at=now + timedelta(days=20),
    )
    session.add(active)
    await session.flush()

    abandoned_pending = BillingSubscriptionModel(
        owner_id=owner_id, plan_id=plan_id, billing_mode="recurring", status="pending",
    )
    session.add(abandoned_pending)
    await session.flush()

    result = await repo.get_current_for_owner(owner_id)
    assert result.id == active.id


@pytest.mark.asyncio
async def test_get_current_for_owner_returns_none_when_no_subscriptions(session):
    owner_id, _ = await _seed_owner_and_plan(session)
    repo = SQLAlchemyBillingSubscriptionRepository(session)

    result = await repo.get_current_for_owner(owner_id)
    assert result is None


@pytest.mark.asyncio
async def test_list_pending_recurring_with_gateway_id_filters_correctly(session):
    owner_id, plan_id = await _seed_owner_and_plan(session)
    repo = SQLAlchemyBillingSubscriptionRepository(session)

    candidate = BillingSubscriptionModel(
        owner_id=owner_id, plan_id=plan_id, billing_mode="recurring", status="pending",
        billing_subscription_id="preapproval_1",
    )
    no_gateway_id_yet = BillingSubscriptionModel(
        owner_id=owner_id, plan_id=plan_id, billing_mode="recurring", status="pending",
        billing_subscription_id=None,
    )
    wrong_mode = BillingSubscriptionModel(
        owner_id=owner_id, plan_id=plan_id, billing_mode="invoice", status="pending",
        billing_subscription_id="ignored",
    )
    already_active = BillingSubscriptionModel(
        owner_id=owner_id, plan_id=plan_id, billing_mode="recurring", status="active",
        billing_subscription_id="preapproval_2",
    )
    session.add_all([candidate, no_gateway_id_yet, wrong_mode, already_active])
    await session.flush()

    result = await repo.list_pending_recurring_with_gateway_id()
    result_ids = {s.id for s in result}
    assert result_ids == {candidate.id}
