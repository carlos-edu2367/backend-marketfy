from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.recurring_service import RecurringService, CYCLE_MAP


@dataclass
class StubPlan:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "PRO"
    is_active: bool = True
    price_monthly: Decimal = Decimal("50.00")
    price_180days: Decimal = Decimal("270.00")
    price_annual: Decimal = Decimal("510.00")


@dataclass
class StubUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Fulano"
    email: str = "f@t.com"
    asaas_customer_id: Optional[str] = None


class SubRepo:
    def __init__(self):
        self.saved = []
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


class UserRepo:
    def __init__(self):
        self.updated = []
    async def update_asaas_customer_id(self, uid, cid):
        self.updated.append((uid, cid))


@dataclass
class StubSettings:
    BILLING_CORE_SYSTEM: str = "marketfy"
    BILLING_CORE_WEBHOOK_HOST: Optional[str] = "https://api-marketfy.neectify.com"


def test_cycle_map_uses_billing_enum_values():
    assert CYCLE_MAP["monthly"] == "MONTHLY"
    assert CYCLE_MAP["semiannual"] == "SEMIANNUALLY"
    assert CYCLE_MAP["annual"] == "YEARLY"


@pytest.mark.asyncio
async def test_contract_creates_customer_and_subscription():
    plan = StubPlan()
    user = StubUser()
    bc = AsyncMock()
    bc.create_customer.return_value = {"provider_customer_id": "cus_1"}
    bc.create_subscription.return_value = {"job_id": "job_1"}
    bc.get_job.return_value = {"status": "done", "result": {"checkout_url": "https://pay/x", "subscription_id": "sub_bc_1"}}

    svc = RecurringService(SubRepo(), PlanRepo(plan), UserRepo(), bc, StubSettings())
    result = await svc.contract(user, plan.id, "monthly", document="12345678901", idempotency_key="idem-1")

    assert result["checkout_url"] == "https://pay/x"
    bc.create_customer.assert_awaited_once()
    bc.create_subscription.assert_awaited_once()
    # ciclo mapeado corretamente
    _, kwargs = bc.create_subscription.call_args
    assert kwargs["subscription_type"] == "MONTHLY"
