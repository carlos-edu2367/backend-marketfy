from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.invoice_service import InvoiceService, price_for_period, PERIOD_DAYS


@dataclass
class StubPlan:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "PRO"
    type: str = "pago"
    is_active: bool = True
    price_monthly: Decimal = Decimal("50.00")
    price_180days: Decimal = Decimal("270.00")
    price_annual: Decimal = Decimal("510.00")


@dataclass
class StubInvoice:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    owner_id: uuid.UUID = field(default_factory=uuid.uuid4)
    subscription_id: uuid.UUID = field(default_factory=uuid.uuid4)
    plan_id: Optional[uuid.UUID] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    due_date: Optional[datetime] = None
    amount: Decimal = Decimal("0")
    status: str = "pending"
    bc_payment_id: Optional[str] = None
    checkout_url: Optional[str] = None
    bc_job_id: Optional[str] = None
    idempotency_key: Optional[str] = None


@dataclass
class StubSub:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    owner_id: uuid.UUID = field(default_factory=uuid.uuid4)
    plan_id: Optional[uuid.UUID] = None
    billing_mode: str = "invoice"
    subscription_type: str = "monthly"
    status: str = "pending"
    expires_at: Optional[datetime] = None
    idempotency_key: Optional[str] = None


class InvoiceRepo:
    def __init__(self):
        self.items = {}
        self.open_by_sub = {}
    async def get_by_idempotency_key(self, key):
        for i in self.items.values():
            if i.idempotency_key == key:
                return i
        return None
    async def get_open_invoice_for_subscription(self, sub_id):
        return self.open_by_sub.get(sub_id)
    async def create(self, **f):
        inv = StubInvoice(id=uuid.uuid4(), **f)
        self.items[inv.id] = inv
        if inv.status == "pending":
            self.open_by_sub[inv.subscription_id] = inv
        return inv
    async def get_by_id(self, iid):
        return self.items.get(iid)
    async def get_by_payment_id(self, pid):
        for i in self.items.values():
            if i.bc_payment_id == pid:
                return i
        return None
    async def update_checkout(self, iid, **kw):
        inv = self.items[iid]
        for k, v in kw.items():
            if v is not None:
                setattr(inv, k, v)
    async def mark_paid(self, iid, *, bc_payment_id, paid_at):
        inv = self.items[iid]
        if inv.status != "pending":
            return 0
        inv.status = "paid"; inv.bc_payment_id = bc_payment_id
        self.open_by_sub.pop(inv.subscription_id, None)
        return 1


class SubRepo:
    def __init__(self, sub):
        self._sub = sub
        self.saved = []
        self.by_idempotency_key = {}
    async def get_by_id(self, sid):
        return self._sub
    async def get_by_idempotency_key(self, key):
        return self.by_idempotency_key.get(key)
    async def save(self, sub):
        if sub.id is None:
            sub.id = uuid.uuid4()
        self.saved.append(sub)
        if getattr(sub, "idempotency_key", None):
            self.by_idempotency_key[sub.idempotency_key] = sub
        return sub
    async def create_if_absent_by_idempotency_key(self, sub):
        existing = await self.get_by_idempotency_key(sub.idempotency_key)
        if existing is not None:
            return existing, False
        return await self.save(sub), True


class PlanRepo:
    def __init__(self, plan):
        self._plan = plan
    async def get_by_id(self, pid):
        return self._plan


@dataclass
class StubUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    plan_id: Optional[uuid.UUID] = None
    plan_expiration: Optional[datetime] = None
    is_active: bool = False


class UserRepo:
    def __init__(self, user):
        self._user = user
        self.saved = []
    async def get_by_id(self, uid):
        return self._user
    async def save(self, user):
        self.saved.append(user)
        return user


@dataclass
class StubSettings:
    BILLING_CORE_SYSTEM: str = "marketfy"
    BILLING_CORE_WEBHOOK_INVOICE_URL: str = "https://api-marketfy/api/v1/webhooks/billing-invoices"
    BILLING_CORE_CHECKOUT_EXPIRATION_MINUTES: int = 30
    PUBLIC_FRONTEND_URL: str = "https://app.marketfy.com"


def test_price_for_period_maps_correctly():
    plan = StubPlan()
    assert price_for_period(plan, "monthly") == Decimal("50.00")
    assert price_for_period(plan, "semiannual") == Decimal("270.00")
    assert price_for_period(plan, "annual") == Decimal("510.00")
    assert PERIOD_DAYS["monthly"] == 30


@pytest.mark.asyncio
async def test_contract_creates_subscription_and_invoice_without_checkout():
    owner = uuid.uuid4()
    plan = StubPlan()
    inv_repo = InvoiceRepo()
    sub_repo = SubRepo(None)
    plan_repo = PlanRepo(plan)
    bc = AsyncMock()
    svc = InvoiceService(inv_repo, sub_repo, plan_repo, bc, StubSettings())
    result = await svc.contract(owner, plan.id, "monthly", idempotency_key="idem-1")

    assert result["checkout_url"] is None
    assert result["job_id"] is None
    assert result["invoice_id"] is not None
    bc.create_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_checkout_creates_the_first_checkout_only_after_payment_click():
    owner = uuid.uuid4()
    plan = StubPlan()
    inv_repo = InvoiceRepo()
    bc = AsyncMock()
    bc.create_payment.return_value = {"job_id": "job_1"}
    bc.get_job.return_value = {
        "status": "completed",
        "result": {"checkout_url": "https://pay/x", "payment_id": "pay_1"},
    }
    svc = InvoiceService(inv_repo, SubRepo(None), PlanRepo(plan), bc, StubSettings())
    contracted = await svc.contract(owner, plan.id, "monthly", idempotency_key="idem-1")

    checkout = await svc.ensure_checkout(uuid.UUID(contracted["invoice_id"]))

    assert checkout == {"status": "completed", "checkout_url": "https://pay/x"}
    bc.create_payment.assert_awaited_once()
    assert inv_repo.items[uuid.UUID(contracted["invoice_id"])].bc_payment_id == "pay_1"


@pytest.mark.asyncio
async def test_retry_canceled_invoice_creates_new_pending_subscription_and_invoice_without_checkout():
    plan = StubPlan()
    old_sub = StubSub(plan_id=plan.id, status="canceled")
    inv_repo = InvoiceRepo()
    old_invoice = await inv_repo.create(
        owner_id=old_sub.owner_id,
        subscription_id=old_sub.id,
        plan_id=plan.id,
        period_start=datetime.utcnow(),
        period_end=datetime.utcnow() + timedelta(days=30),
        due_date=datetime.utcnow(),
        amount=Decimal("50.00"),
        status="canceled",
        idempotency_key="old-invoice",
    )
    billing_client = AsyncMock()
    service = InvoiceService(inv_repo, SubRepo(old_sub), PlanRepo(plan), billing_client, StubSettings())

    result = await service.retry_canceled_invoice(old_invoice.id)

    replacement = inv_repo.items[uuid.UUID(result["invoice_id"])]
    assert replacement.id != old_invoice.id
    assert replacement.subscription_id != old_invoice.subscription_id
    assert replacement.status == "pending"
    assert replacement.checkout_url is None
    assert result["job_id"] is None
    billing_client.create_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_canceled_invoice_is_idempotent():
    plan = StubPlan()
    old_sub = StubSub(plan_id=plan.id, status="canceled")
    inv_repo = InvoiceRepo()
    old_invoice = await inv_repo.create(
        owner_id=old_sub.owner_id,
        subscription_id=old_sub.id,
        plan_id=plan.id,
        period_start=datetime.utcnow(),
        period_end=datetime.utcnow() + timedelta(days=30),
        due_date=datetime.utcnow(),
        amount=Decimal("50.00"),
        status="canceled",
        idempotency_key="old-invoice",
    )
    service = InvoiceService(inv_repo, SubRepo(old_sub), PlanRepo(plan), AsyncMock(), StubSettings())

    first = await service.retry_canceled_invoice(old_invoice.id)
    second = await service.retry_canceled_invoice(old_invoice.id)

    assert second == first
    assert len(inv_repo.items) == 2


@pytest.mark.asyncio
async def test_retry_canceled_invoice_does_not_cancel_an_active_subscription():
    plan = StubPlan()
    active_sub = StubSub(plan_id=plan.id, status="active")
    inv_repo = InvoiceRepo()
    canceled_invoice = await inv_repo.create(
        owner_id=active_sub.owner_id,
        subscription_id=active_sub.id,
        plan_id=plan.id,
        period_start=datetime.utcnow(),
        period_end=datetime.utcnow() + timedelta(days=30),
        due_date=datetime.utcnow(),
        amount=Decimal("50.00"),
        status="canceled",
        idempotency_key="old-invoice",
    )
    service = InvoiceService(inv_repo, SubRepo(active_sub), PlanRepo(plan), AsyncMock(), StubSettings())

    with pytest.raises(ValueError, match="ativa"):
        await service.retry_canceled_invoice(canceled_invoice.id)

    assert active_sub.status == "active"
    assert len(inv_repo.items) == 1


@pytest.mark.asyncio
async def test_activate_invoice_activates_subscription_idempotently():
    owner = uuid.uuid4()
    plan = StubPlan()
    now = datetime.utcnow()
    sub = StubSub(owner_id=owner, plan_id=plan.id, status="pending")
    inv_repo = InvoiceRepo()
    inv = await inv_repo.create(owner_id=owner, subscription_id=sub.id, plan_id=plan.id,
                                period_start=now, period_end=now + timedelta(days=30),
                                due_date=now, amount=Decimal("50.00"), idempotency_key="idem-1")
    sub_repo = SubRepo(sub)
    svc = InvoiceService(inv_repo, sub_repo, PlanRepo(plan), AsyncMock(), StubSettings())

    await svc.activate_invoice(inv.id, "pay_1", {})
    await svc.activate_invoice(inv.id, "pay_1", {})  # idempotente

    assert sub.status == "active"
    assert sub.expires_at is not None
    assert inv.status == "paid"


@pytest.mark.asyncio
async def test_activate_invoice_syncs_user_plan_cache():
    """Regression: paying an invoice must update the UserModel cache
    (plan_id/plan_expiration/is_active) that /auth/me and the frontend
    banner read — not just the BillingSubscriptionModel row."""
    owner = uuid.uuid4()
    plan = StubPlan()
    now = datetime.utcnow()
    sub = StubSub(owner_id=owner, plan_id=plan.id, status="pending")
    inv_repo = InvoiceRepo()
    inv = await inv_repo.create(owner_id=owner, subscription_id=sub.id, plan_id=plan.id,
                                period_start=now, period_end=now + timedelta(days=30),
                                due_date=now, amount=Decimal("50.00"), idempotency_key="idem-2")
    sub_repo = SubRepo(sub)
    user = StubUser(id=owner, plan_id=None, plan_expiration=None, is_active=False)
    user_repo = UserRepo(user)
    svc = InvoiceService(inv_repo, sub_repo, PlanRepo(plan), AsyncMock(), StubSettings(), user_repo=user_repo)

    await svc.activate_invoice(inv.id, "pay_1", {})

    assert user.plan_id == plan.id
    assert user.plan_expiration == inv.period_end
    assert user.is_active is True
    assert len(user_repo.saved) == 1
