from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.plan_access_service import PlanAccessService, SubscriptionStatus


@dataclass
class StubSub:
    owner_id: uuid.UUID
    plan_id: Optional[uuid.UUID] = None
    status: str = "active"
    billing_mode: str = "invoice"
    expires_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class StubPlan:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "PRO"
    type: str = "pago"
    is_active: bool = True
    max_markets: int = 5
    max_terminals: int = 10


class SubRepo:
    def __init__(self, sub):
        self._sub = sub
    async def get_active_by_owner(self, owner_id):
        return self._sub


class PlanRepo:
    def __init__(self, plan):
        self._plan = plan
    async def get_by_id(self, pid):
        return self._plan


class UserRepo:
    async def get_by_id(self, uid):
        return None


def _svc(sub, plan):
    return PlanAccessService(UserRepo(), PlanRepo(plan), SubRepo(sub))


@pytest.mark.asyncio
async def test_within_grace_is_past_due_and_not_locked():
    owner = uuid.uuid4()
    plan = StubPlan()
    sub = StubSub(owner_id=owner, plan_id=plan.id, status="active",
                  billing_mode="invoice", expires_at=datetime.utcnow() - timedelta(days=1))
    res = await _svc(sub, plan).get_subscription_status(owner)
    assert res.subscription_status == SubscriptionStatus.PAST_DUE
    assert res.locked is False


@pytest.mark.asyncio
async def test_after_grace_is_expired_and_locked():
    owner = uuid.uuid4()
    plan = StubPlan()
    sub = StubSub(owner_id=owner, plan_id=plan.id, status="active",
                  billing_mode="invoice", expires_at=datetime.utcnow() - timedelta(days=4))
    res = await _svc(sub, plan).get_subscription_status(owner)
    assert res.subscription_status == SubscriptionStatus.EXPIRED
    assert res.locked is True


@pytest.mark.asyncio
async def test_active_before_expiry_not_locked():
    owner = uuid.uuid4()
    plan = StubPlan()
    sub = StubSub(owner_id=owner, plan_id=plan.id, status="active",
                  billing_mode="invoice", expires_at=datetime.utcnow() + timedelta(days=10))
    res = await _svc(sub, plan).get_subscription_status(owner)
    assert res.subscription_status == SubscriptionStatus.ACTIVE
    assert res.locked is False
    assert res.billing_mode == "invoice"
