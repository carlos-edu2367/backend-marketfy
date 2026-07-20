from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.subscription_service import SubscriptionService


@dataclass
class StubSub:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    owner_id: uuid.UUID = field(default_factory=uuid.uuid4)
    plan_id: Optional[uuid.UUID] = None
    billing_subscription_id: Optional[str] = "sub_bc_1"
    status: str = "pending"
    expires_at: Optional[datetime] = None


@dataclass
class StubUser:
    id: uuid.UUID
    plan_id: Optional[uuid.UUID] = None
    plan_expiration: Optional[datetime] = None
    is_active: bool = True


class SubRepo:
    def __init__(self, sub):
        self._sub = sub
        self.saved = []
    async def get_by_billing_subscription_id(self, bid):
        return self._sub if self._sub and self._sub.billing_subscription_id == bid else None
    async def save(self, sub):
        self.saved.append(sub); return sub


class EventRepo:
    def __init__(self):
        self.events = {}
    async def get_by_event_id(self, eid):
        return self.events.get(eid)
    async def save(self, ev):
        self.events[ev.event_id] = ev; return ev


class UserRepo:
    def __init__(self, user):
        self._user = user
        self.saved = []
    async def get_by_id(self, uid):
        return self._user
    async def save(self, u):
        self.saved.append(u); return u


class PlanRepo:
    async def get_by_id(self, pid):
        return None


@pytest.mark.asyncio
async def test_payment_received_activates_and_is_idempotent():
    sub = StubSub()
    user = StubUser(id=sub.owner_id)
    svc = SubscriptionService(UserRepo(user), PlanRepo(), SubRepo(sub), EventRepo())
    exp = datetime(2027, 1, 1)
    r1 = await svc.process_recurring_event(
        event="PAYMENT_RECEIVED", billing_subscription_id="sub_bc_1",
        subscription_expires_at=exp, payment_date=datetime(2026, 1, 1), raw_payload={},
    )
    r2 = await svc.process_recurring_event(
        event="PAYMENT_RECEIVED", billing_subscription_id="sub_bc_1",
        subscription_expires_at=exp, payment_date=datetime(2026, 1, 1), raw_payload={},
    )
    assert r1["result"] == "processed"
    assert r2["result"] == "duplicate"
    assert sub.status == "active"


@pytest.mark.asyncio
async def test_subscription_inactivated_cancels():
    sub = StubSub(status="active")
    user = StubUser(id=sub.owner_id)
    svc = SubscriptionService(UserRepo(user), PlanRepo(), SubRepo(sub), EventRepo())
    r = await svc.process_recurring_event(
        event="SUBSCRIPTION_INACTIVATED", billing_subscription_id="sub_bc_1",
        subscription_expires_at=datetime(2027, 1, 1), payment_date=None, raw_payload={},
    )
    assert r["result"] == "processed"
    assert sub.status == "canceled"
