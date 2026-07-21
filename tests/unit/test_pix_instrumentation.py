import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest

from domain.pix import PixOrderResult, PixAttemptStatus
from infra.database.models import PixPaymentAttemptModel


class FakeAttemptRepo:
    def __init__(self, a): self.a = a
    async def get_by_id_for_update(self, i, m): return self.a
    async def save(self, m, commit=True): self.a = m; return m
    async def record_query(self, **kw): pass


class FakeConnSvc:
    async def get_valid_access_token(self, m): return "AT"


class ProviderProcessed:
    async def get_payment(self, **kw):
        return PixOrderResult("ORD1", "processed", "accredited", PixAttemptStatus.APPROVED,
                              Decimal("10.00"), "BRL", None, "42")


class Completer:
    async def complete_sale(self, a): pass


@pytest.mark.asyncio
async def test_verify_records_approved_metric():
    from infra.config import settings as sm; sm.get_settings.cache_clear()
    from application.services.pix.payment_service import PixPaymentService
    m = uuid.uuid4()
    a = PixPaymentAttemptModel(id=uuid.uuid4(), market_id=m, sale_id=uuid.uuid4(),
        box_id=uuid.uuid4(), terminal_id=uuid.uuid4(), operator_id=uuid.uuid4(),
        amount=Decimal("10.00"), external_reference="p1", idempotency_key="k1",
        status="pending", order_id="ORD1")
    svc = PixPaymentService(attempt_repo=FakeAttemptRepo(a), sale_repo=None, box_repo=None,
        product_repo=None, connection_service=FakeConnSvc(), provider=ProviderProcessed(),
        lock=None, completer=Completer())
    with patch("application.services.pix.payment_service.metrics_registry") as mreg:
        await svc.verify(market_id=m, attempt_id=a.id, source="manual_button")
        mreg.record_pix_payment_approved.assert_called_once_with(source="manual_button")
