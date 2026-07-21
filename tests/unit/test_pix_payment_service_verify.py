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
    def __init__(self, attempt): self.attempt = attempt; self.queries=[]
    async def get_by_id_for_update(self, aid, market_id): return self.attempt
    async def save(self, m, commit=True): self.attempt = m; return m
    async def record_query(self, **kw): self.queries.append(kw)


class FakeConnSvc:
    async def get_valid_access_token(self, market_id): return "AT"


class ProviderProcessed:
    async def get_payment(self, **kw):
        return PixOrderResult("ORD1", "processed", "accredited", PixAttemptStatus.APPROVED,
                              Decimal("30.00"), "BRL", None, "42")


class ProviderDivergent:
    async def get_payment(self, **kw):
        return PixOrderResult("ORD1", "processed", "accredited", PixAttemptStatus.APPROVED,
                              Decimal("99.99"), "BRL", None, "42")


class FakeCompleter:
    def __init__(self): self.completed=False
    async def complete_sale(self, attempt): self.completed=True


def _attempt(market_id):
    return PixPaymentAttemptModel(id=uuid.uuid4(), market_id=market_id, sale_id=uuid.uuid4(),
        box_id=uuid.uuid4(), terminal_id=uuid.uuid4(), operator_id=uuid.uuid4(),
        amount=Decimal("30.00"), external_reference="pix1", idempotency_key="k1",
        status="pending", order_id="ORD1")


@pytest.mark.asyncio
async def test_verify_processed_completes_once(monkeypatch):
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    from application.services.pix.payment_service import PixPaymentService
    market_id = uuid.uuid4()
    attempt = _attempt(market_id)
    repo = FakeAttemptRepo(attempt); completer = FakeCompleter()
    svc = PixPaymentService(attempt_repo=repo, sale_repo=None, box_repo=None, product_repo=None,
        connection_service=FakeConnSvc(), provider=ProviderProcessed(), lock=None, completer=completer)
    out = await svc.verify(market_id=market_id, attempt_id=attempt.id)
    assert out.status == "approved" and completer.completed is True


@pytest.mark.asyncio
async def test_verify_amount_mismatch_is_divergent(monkeypatch):
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    from application.services.pix.payment_service import PixPaymentService
    market_id = uuid.uuid4()
    attempt = _attempt(market_id)
    repo = FakeAttemptRepo(attempt); completer = FakeCompleter()
    svc = PixPaymentService(attempt_repo=repo, sale_repo=None, box_repo=None, product_repo=None,
        connection_service=FakeConnSvc(), provider=ProviderDivergent(), lock=None, completer=completer)
    out = await svc.verify(market_id=market_id, attempt_id=attempt.id)
    assert out.status == "divergent" and completer.completed is False
