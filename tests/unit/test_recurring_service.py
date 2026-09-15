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
        self._by_id = {}
    async def get_by_idempotency_key(self, key):
        return None
    async def get_by_id(self, sub_id):
        return self._by_id.get(sub_id)
    async def save(self, sub):
        if sub.id is None:
            sub.id = uuid.uuid4()  # simula o default do SQLAlchemy aplicado no flush()
        self.saved.append(sub)
        self._by_id[sub.id] = sub
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
async def test_contract_reuses_existing_subscription_for_same_idempotency_key():
    from infra.database.models import BillingSubscriptionModel

    plan = StubPlan()
    user = StubUser()
    existing = BillingSubscriptionModel(
        id=uuid.uuid4(), owner_id=user.id, billing_mode="recurring",
        status="pending", billing_job_id="job_existing", idempotency_key="idem-repeat",
    )
    sub_repo = SubRepo()
    sub_repo.saved.append(existing)
    sub_repo._by_id[existing.id] = existing

    class RepoWithIdempotency(SubRepo):
        async def get_by_idempotency_key(self, key):
            return existing if key == "idem-repeat" else None

    bc = AsyncMock()
    svc = RecurringService(RepoWithIdempotency(), PlanRepo(plan), UserRepo(), bc, StubSettings())
    result = await svc.contract(user, plan.id, "monthly", document="12345678901", idempotency_key="idem-repeat")

    assert result == {"subscription_id": str(existing.id), "job_id": "job_existing"}
    bc.create_subscription.assert_not_awaited()


@pytest.mark.asyncio
async def test_contract_creates_local_subscription_and_enqueues_without_polling():
    plan = StubPlan()
    user = StubUser()
    bc = AsyncMock()
    bc.create_customer.return_value = {"provider_customer_id": "cus_1"}
    bc.create_subscription.return_value = {"job_id": "job_1"}

    sub_repo = SubRepo()
    svc = RecurringService(sub_repo, PlanRepo(plan), UserRepo(), bc, StubSettings())
    result = await svc.contract(user, plan.id, "monthly", document="12345678901", idempotency_key="idem-1")

    assert "checkout_url" not in result
    assert result["job_id"] == "job_1"
    assert result["subscription_id"] is not None
    bc.get_job.assert_not_awaited()

    saved = sub_repo.saved[-1]
    assert saved.status == "pending"
    bc.create_subscription.assert_awaited_once()
    _, kwargs = bc.create_subscription.call_args
    assert kwargs["system_sub_id"] == str(saved.id)
    assert kwargs["idempotency_key"] == f"bc-sub-{saved.id}"
    assert kwargs["subscription_type"] == "MONTHLY"


@pytest.mark.asyncio
async def test_contract_passes_back_url_derived_from_local_subscription_id():
    plan = StubPlan()
    user = StubUser()
    bc = AsyncMock()
    bc.create_customer.return_value = {"provider_customer_id": "cus_1"}
    bc.create_subscription.return_value = {"job_id": "job_1"}

    settings = StubSettings()
    settings.PUBLIC_FRONTEND_URL = "https://app.marketfy.com"
    svc = RecurringService(SubRepo(), PlanRepo(plan), UserRepo(), bc, settings)
    result = await svc.contract(user, plan.id, "monthly", document="12345678901", idempotency_key="idem-1")

    _, kwargs = bc.create_subscription.call_args
    assert kwargs["back_url"] == f"https://app.marketfy.com/billing/retorno?tipo=subscription&ref={result['subscription_id']}"


@pytest.mark.asyncio
async def test_contract_tracks_subscription_created_for_recurring_mode():
    plan = StubPlan()
    user = StubUser()
    bc = AsyncMock()
    bc.create_customer.return_value = {"provider_customer_id": "cus_1"}
    bc.create_subscription.return_value = {"job_id": "job_1"}
    analytics = AsyncMock()

    svc = RecurringService(SubRepo(), PlanRepo(plan), UserRepo(), bc, StubSettings(), analytics=analytics)
    await svc.contract(user, plan.id, "monthly", document="12345678901", idempotency_key="idem-analytics-1")

    analytics.track_event.assert_awaited_once_with(
        str(user.id), "subscription_created",
        {"plan_id": str(plan.id), "subscription_type": "monthly", "billing_mode": "recurring"},
    )


@pytest.mark.asyncio
async def test_ensure_checkout_polls_job_and_persists_checkout_url():
    from infra.database.models import BillingSubscriptionModel

    local_id = uuid.uuid4()
    sub_repo = AsyncMock()
    local_sub = BillingSubscriptionModel(
        id=local_id, owner_id=uuid.uuid4(), billing_mode="recurring",
        status="pending", billing_job_id="job_1", checkout_url=None,
    )
    sub_repo.get_by_id.return_value = local_sub

    bc = AsyncMock()
    bc.get_job.return_value = {
        "status": "completed",
        "result": {"checkout_url": "https://pay/mp/x", "subscription_id": "preapproval_1"},
    }

    svc = RecurringService(sub_repo, PlanRepo(StubPlan()), UserRepo(), bc, StubSettings())
    result = await svc.ensure_checkout(local_id)

    assert result == {"status": "completed", "checkout_url": "https://pay/mp/x"}
    assert local_sub.billing_subscription_id == "preapproval_1"
    assert local_sub.checkout_url == "https://pay/mp/x"
    sub_repo.save.assert_awaited_once_with(local_sub)


@pytest.mark.asyncio
async def test_ensure_checkout_returns_pending_when_job_not_done():
    from infra.database.models import BillingSubscriptionModel

    local_id = uuid.uuid4()
    sub_repo = AsyncMock()
    local_sub = BillingSubscriptionModel(
        id=local_id, owner_id=uuid.uuid4(), billing_mode="recurring",
        status="pending", billing_job_id="job_1", checkout_url=None,
    )
    sub_repo.get_by_id.return_value = local_sub

    bc = AsyncMock()
    bc.get_job.return_value = {"status": "processing"}

    svc = RecurringService(sub_repo, PlanRepo(StubPlan()), UserRepo(), bc, StubSettings())
    result = await svc.ensure_checkout(local_id)

    assert result == {"status": "processing", "checkout_url": None}
    sub_repo.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_checkout_returns_completed_immediately_when_already_persisted():
    from infra.database.models import BillingSubscriptionModel

    local_id = uuid.uuid4()
    sub_repo = AsyncMock()
    local_sub = BillingSubscriptionModel(
        id=local_id, owner_id=uuid.uuid4(), billing_mode="recurring",
        status="pending", billing_job_id="job_1", checkout_url="https://pay/mp/already-there",
    )
    sub_repo.get_by_id.return_value = local_sub

    bc = AsyncMock()
    svc = RecurringService(sub_repo, PlanRepo(StubPlan()), UserRepo(), bc, StubSettings())
    result = await svc.ensure_checkout(local_id)

    assert result == {"status": "completed", "checkout_url": "https://pay/mp/already-there"}
    bc.get_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_checkout_returns_not_found_for_unknown_subscription():
    sub_repo = AsyncMock()
    sub_repo.get_by_id.return_value = None
    bc = AsyncMock()

    svc = RecurringService(sub_repo, PlanRepo(StubPlan()), UserRepo(), bc, StubSettings())
    result = await svc.ensure_checkout(uuid.uuid4())

    assert result == {"status": "not_found", "checkout_url": None}
