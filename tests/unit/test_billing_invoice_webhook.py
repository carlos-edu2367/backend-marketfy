from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import AsyncMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.web.routers.billing_invoice_webhooks import InvoiceWebhookProcessor


@pytest.mark.asyncio
async def test_paid_status_activates_invoice():
    invoice_service = AsyncMock()
    invoice_id = uuid.uuid4()
    proc = InvoiceWebhookProcessor(invoice_service)
    payload = {"payment_id": "pay_1", "system_payment_id": str(invoice_id), "payment_status": "PAID"}
    code = await proc.process(payload)
    assert code == 200
    invoice_service.activate_invoice.assert_awaited_once()


@pytest.mark.asyncio
async def test_overdue_status_marks_failed():
    invoice_service = AsyncMock()
    invoice_id = uuid.uuid4()
    proc = InvoiceWebhookProcessor(invoice_service)
    payload = {"payment_id": "pay_2", "system_payment_id": str(invoice_id), "payment_status": "OVERDUE"}
    code = await proc.process(payload)
    assert code == 200
    invoice_service.mark_invoice_failed.assert_awaited_once()
