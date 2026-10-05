from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.jobs.billing_jobs import reconcile_pending_invoices, reconcile_provisional_subscriptions


@dataclass
class StubInvoice:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    bc_payment_id: str = "pay_1"


class InvRepo:
    def __init__(self, invs):
        self._invs = invs
    async def get_pending_with_payment_id_older_than(self, cutoff, limit=50):
        return self._invs


@pytest.mark.asyncio
async def test_reconcile_activates_confirmed_payment():
    inv = StubInvoice()
    bc = AsyncMock()
    bc.get_payment.return_value = {"payment_id": "pay_1", "payment_status": "CONFIRMED"}
    svc = AsyncMock()
    result = await reconcile_pending_invoices({}, invoice_repo=InvRepo([inv]), bc_client=bc, invoice_service=svc)
    assert result["activated"] == 1
    svc.activate_invoice.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_keeps_expired_checkout_invoice_available_for_reissue():
    inv = StubInvoice()
    bc = AsyncMock()
    bc.get_payment.return_value = {"payment_id": "pay_1", "payment_status": "EXPIRED"}
    svc = AsyncMock()

    result = await reconcile_pending_invoices({}, invoice_repo=InvRepo([inv]), bc_client=bc, invoice_service=svc)

    assert result["failed"] == 0
    svc.mark_invoice_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_provisional_subscriptions_grants_provisional_access_when_gateway_active():
    sub = SimpleNamespace(
        id=uuid.uuid4(), status="pending", provisional=False,
        billing_subscription_id="preapproval_1", subscription_type="monthly",
    )

    sub_repo = AsyncMock()
    sub_repo.list_pending_recurring_with_gateway_id.return_value = [sub]

    bc_client = AsyncMock()
    bc_client.get_subscription_status.return_value = {"gateway_status": "ACTIVE"}

    result = await reconcile_provisional_subscriptions({}, sub_repo=sub_repo, bc_client=bc_client)

    assert result == {"checked": 1, "granted": 1, "errors": 0}
    assert sub.status == "active"
    assert sub.provisional is True
    sub_repo.save.assert_awaited_once_with(sub)


@pytest.mark.asyncio
async def test_reconcile_provisional_subscriptions_skips_when_gateway_still_pending():
    sub = SimpleNamespace(
        id=uuid.uuid4(), status="pending", provisional=False,
        billing_subscription_id="preapproval_1", subscription_type="monthly",
    )
    sub_repo = AsyncMock()
    sub_repo.list_pending_recurring_with_gateway_id.return_value = [sub]
    bc_client = AsyncMock()
    bc_client.get_subscription_status.return_value = {"gateway_status": "PENDING"}

    result = await reconcile_provisional_subscriptions({}, sub_repo=sub_repo, bc_client=bc_client)

    assert result == {"checked": 1, "granted": 0, "errors": 0}
    sub_repo.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_provisional_subscriptions_counts_errors_and_continues():
    ok_sub = SimpleNamespace(
        id=uuid.uuid4(), status="pending", provisional=False,
        billing_subscription_id="preapproval_2", subscription_type="monthly",
    )
    broken_sub = SimpleNamespace(
        id=uuid.uuid4(), status="pending", provisional=False,
        billing_subscription_id="preapproval_1", subscription_type="monthly",
    )
    sub_repo = AsyncMock()
    sub_repo.list_pending_recurring_with_gateway_id.return_value = [broken_sub, ok_sub]

    bc_client = AsyncMock()
    bc_client.get_subscription_status.side_effect = [Exception("timeout"), {"gateway_status": "ACTIVE"}]

    result = await reconcile_provisional_subscriptions({}, sub_repo=sub_repo, bc_client=bc_client)

    assert result == {"checked": 2, "granted": 1, "errors": 1}
    assert ok_sub.status == "active"
    assert broken_sub.status == "pending"
