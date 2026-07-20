from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from unittest.mock import AsyncMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.jobs.billing_jobs import generate_due_invoices


@dataclass
class StubSub:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    owner_id: uuid.UUID = field(default_factory=uuid.uuid4)
    plan_id: uuid.UUID = field(default_factory=uuid.uuid4)
    expires_at: Optional[datetime] = None


@dataclass
class StubInvoice:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    owner_id: uuid.UUID = field(default_factory=uuid.uuid4)
    amount: str = "50.00"
    due_date: Optional[datetime] = None


class SubRepo:
    def __init__(self, subs):
        self._subs = subs
    async def list_invoice_subs_expiring_within(self, cutoff):
        return self._subs


@pytest.mark.asyncio
async def test_generates_invoice_for_expiring_subscription():
    sub = StubSub(expires_at=datetime.utcnow() + timedelta(days=3))
    invoice_service = AsyncMock()
    invoice_service.generate_next_invoice.return_value = StubInvoice(owner_id=sub.owner_id)
    invoice_repo = AsyncMock()
    result = await generate_due_invoices(
        {}, sub_repo=SubRepo([sub]), invoice_service=invoice_service,
        invoice_repo=invoice_repo, email_gateway=None, user_repo=AsyncMock(),
    )
    assert result["generated"] == 1
    invoice_service.generate_next_invoice.assert_awaited_once()
    invoice_repo.mark_notified.assert_awaited_once()


@pytest.mark.asyncio
async def test_skips_when_no_invoice_generated():
    sub = StubSub(expires_at=datetime.utcnow() + timedelta(days=3))
    invoice_service = AsyncMock()
    invoice_service.generate_next_invoice.return_value = None  # já existe pendente
    result = await generate_due_invoices(
        {}, sub_repo=SubRepo([sub]), invoice_service=invoice_service,
        invoice_repo=AsyncMock(), email_gateway=None, user_repo=AsyncMock(),
    )
    assert result["generated"] == 0
