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
    async def get_by_id(self, sid):
        return self._sub
    async def get_by_idempotency_key(self, key):
        return None
    async def save(self, sub):
        self.saved.append(sub)
        return sub


class PlanRepo:
    def __init__(self, plan):
        self._plan = plan
    async def get_by_id(self, pid):
        return self._plan


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
async def test_contract_creates_subscription_invoice_and_checkout():
    owner = uuid.uuid4()
    plan = StubPlan()
    inv_repo = InvoiceRepo()
    sub_repo = SubRepo(None)
    plan_repo = PlanRepo(plan)
    bc = AsyncMock()
    bc.create_payment.return_value = {"job_id": "job_1"}
    bc.get_job.return_value = {"status": "done", "result": {"checkout_url": "https://pay/x", "payment_id": "pay_1"}}

    svc = InvoiceService(inv_repo, sub_repo, plan_repo, bc, StubSettings())
    result = await svc.contract(owner, plan.id, "monthly", idempotency_key="idem-1")

    assert result["checkout_url"] == "https://pay/x"
    assert result["invoice_id"] is not None
    bc.create_payment.assert_awaited_once()


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
