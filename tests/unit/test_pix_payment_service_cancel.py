# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import uuid
import pytest
from decimal import Decimal
from domain.pix import PixOrderResult, PixAttemptStatus
from infra.database.models import PixPaymentAttemptModel


class FakeAttemptRepo:
    def __init__(self, a): self.attempt=a
    async def get_by_id_for_update(self, aid, m): return self.attempt
    async def save(self, m, commit=True): self.attempt=m; return m
    async def record_query(self, **kw): pass


class FakeConnSvc:
    async def get_valid_access_token(self, m): return "AT"


class ProviderCancels:
    async def get_payment(self, **kw):
        return PixOrderResult("ORD1","created","created",PixAttemptStatus.PENDING,Decimal("30.00"),"BRL",None,"42")
    async def cancel_payment(self, **kw):
        return PixOrderResult("ORD1","canceled","canceled",PixAttemptStatus.CANCELED,Decimal("30.00"),"BRL",None,"42")


class ProviderPaidDuringCancel:
    async def get_payment(self, **kw):
        return PixOrderResult("ORD1","processed","accredited",PixAttemptStatus.APPROVED,Decimal("30.00"),"BRL",None,"42")
    async def cancel_payment(self, **kw):
        return PixOrderResult("ORD1","processed","accredited",PixAttemptStatus.APPROVED,Decimal("30.00"),"BRL",None,"42")


class FakeCompleter:
    def __init__(self): self.completed=False
    async def complete_sale(self, a): self.completed=True


def _a(m):
    return PixPaymentAttemptModel(id=uuid.uuid4(), market_id=m, sale_id=uuid.uuid4(), box_id=uuid.uuid4(),
        terminal_id=uuid.uuid4(), operator_id=uuid.uuid4(), amount=Decimal("30.00"),
        external_reference="p1", idempotency_key="k1", status="pending", order_id="ORD1")


@pytest.mark.asyncio
async def test_cancel_happy(monkeypatch):
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    from application.services.pix.payment_service import PixPaymentService
    m=uuid.uuid4(); a=_a(m)
    svc = PixPaymentService(attempt_repo=FakeAttemptRepo(a), sale_repo=None, box_repo=None,
        product_repo=None, connection_service=FakeConnSvc(), provider=ProviderCancels(),
        lock=None, completer=FakeCompleter())
    out = await svc.cancel(market_id=m, attempt_id=a.id)
    assert out.status == "canceled" and out.qr_data is None


@pytest.mark.asyncio
async def test_cancel_but_paid_prevails(monkeypatch):
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    from application.services.pix.payment_service import PixPaymentService
    m=uuid.uuid4(); a=_a(m); comp=FakeCompleter()
    svc = PixPaymentService(attempt_repo=FakeAttemptRepo(a), sale_repo=None, box_repo=None,
        product_repo=None, connection_service=FakeConnSvc(), provider=ProviderPaidDuringCancel(),
        lock=None, completer=comp)
    out = await svc.cancel(market_id=m, attempt_id=a.id)
    assert out.status == "approved" and comp.completed is True  # pagamento prevalece
