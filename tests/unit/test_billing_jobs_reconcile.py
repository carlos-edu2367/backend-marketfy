from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from unittest.mock import AsyncMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.jobs.billing_jobs import reconcile_pending_invoices


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
