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


class Bus:
    def __init__(self):
        self.events = []

    async def publish(self, attempt_id, event, data):
        self.events.append((attempt_id, event, data))


class FakeAttemptRepo:
    def __init__(self, a):
        self.a = a

    async def get_by_id_for_update(self, i, m):
        return self.a

    async def save(self, m, commit=True):
        self.a = m
        return m

    async def record_query(self, **kw):
        pass


class FakeConnSvc:
    async def get_valid_access_token(self, m):
        return "AT"


class ProviderProcessed:
    async def get_payment(self, **kw):
        return PixOrderResult("ORD1", "processed", "accredited", PixAttemptStatus.APPROVED,
                              Decimal("10.00"), "BRL", None, "42")


class ProviderExpired:
    async def get_payment(self, **kw):
        return PixOrderResult("ORD1", "expired", "expired", PixAttemptStatus.EXPIRED,
                              Decimal("10.00"), "BRL", None, "42")


class Completer:
    async def complete_sale(self, a):
        pass


def _make_attempt(market_id):
    return PixPaymentAttemptModel(
        id=uuid.uuid4(), market_id=market_id, sale_id=uuid.uuid4(), box_id=uuid.uuid4(),
        terminal_id=uuid.uuid4(), operator_id=uuid.uuid4(), amount=Decimal("10.00"),
        external_reference="p1", idempotency_key=str(uuid.uuid4()), status="pending", order_id="ORD1")


@pytest.mark.asyncio
async def test_verify_publishes_approved(monkeypatch):
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    from application.services.pix.payment_service import PixPaymentService

    m = uuid.uuid4()
    a = _make_attempt(m)
    bus = Bus()
    svc = PixPaymentService(attempt_repo=FakeAttemptRepo(a), sale_repo=None, box_repo=None,
        product_repo=None, connection_service=FakeConnSvc(), provider=ProviderProcessed(),
        lock=None, completer=Completer(), event_bus=bus)
    await svc.verify(market_id=m, attempt_id=a.id)
    assert (str(a.id), "payment.approved") in [(str(i), e) for i, e, _ in bus.events]
    # payload não deve conter dados sensíveis (qr_data, token)
    _, _, data = bus.events[-1]
    assert "qr_data" not in data and "access_token" not in data


@pytest.mark.asyncio
async def test_verify_publishes_expired(monkeypatch):
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    from application.services.pix.payment_service import PixPaymentService

    m = uuid.uuid4()
    a = _make_attempt(m)
    bus = Bus()
    svc = PixPaymentService(attempt_repo=FakeAttemptRepo(a), sale_repo=None, box_repo=None,
        product_repo=None, connection_service=FakeConnSvc(), provider=ProviderExpired(),
        lock=None, completer=Completer(), event_bus=bus)
    await svc.verify(market_id=m, attempt_id=a.id)
    assert (str(a.id), "payment.expired") in [(str(i), e) for i, e, _ in bus.events]


class FailingBus:
    async def publish(self, attempt_id, event, data):
        raise ConnectionError("redis unreachable")


@pytest.mark.asyncio
async def test_verify_survives_event_bus_publish_failure(monkeypatch):
    """Publicar é best-effort: uma falha no Redis nunca pode derrubar a
    confirmação de pagamento (que já persistiu com sucesso antes de publicar)."""
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    from application.services.pix.payment_service import PixPaymentService

    m = uuid.uuid4()
    a = _make_attempt(m)
    svc = PixPaymentService(attempt_repo=FakeAttemptRepo(a), sale_repo=None, box_repo=None,
        product_repo=None, connection_service=FakeConnSvc(), provider=ProviderProcessed(),
        lock=None, completer=Completer(), event_bus=FailingBus())
    result = await svc.verify(market_id=m, attempt_id=a.id)
    assert result.status == "approved"  # não propagou a exceção do bus


@pytest.mark.asyncio
async def test_verify_without_event_bus_does_not_crash(monkeypatch):
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    from application.services.pix.payment_service import PixPaymentService

    m = uuid.uuid4()
    a = _make_attempt(m)
    svc = PixPaymentService(attempt_repo=FakeAttemptRepo(a), sale_repo=None, box_repo=None,
        product_repo=None, connection_service=FakeConnSvc(), provider=ProviderProcessed(),
        lock=None, completer=Completer())  # event_bus omitido (default None)
    result = await svc.verify(market_id=m, attempt_id=a.id)
    assert result.status == "approved"
