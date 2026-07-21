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


class Product:
    def __init__(self, pid, price, name="Item", market_id=None, ncm=None):
        self.id, self.price, self.name, self.market_id = pid, price, name, market_id
        self.code = "C1"


class FakeProductRepo:
    def __init__(self, products): self.products = {p.id: p for p in products}
    async def get_by_id(self, pid): return self.products.get(pid)


class FakeAttemptRepo:
    def __init__(self): self.saved = []
    async def get_active_by_sale(self, sale_id): return None
    async def save(self, m, commit=True): self.saved.append(m); return m


class FakeSaleRepo:
    def __init__(self): self.saved = None
    async def save(self, s, commit=True): self.saved = s; return s


class FakeBox:
    def __init__(self, market_id): self.id=uuid.uuid4(); self.market_id=market_id; self.status=None


class FakeBoxRepo:
    def __init__(self, box): self.box = box
    async def get_by_id(self, bid): return self.box


class FakeConnection:
    mp_user_id = "42"
    status = "connected"
    enabled_in_pdv = True
    allowed_terminal_ids = None


class FakeConnRepoForCreate:
    async def get_by_market(self, market_id): return FakeConnection()


class FakeConnSvc:
    def __init__(self): self.repo = FakeConnRepoForCreate()
    async def get_valid_access_token(self, market_id): return "AT"
    async def ensure_pos_registered(self, **kw): return f"T{kw['terminal_id'].hex[:38]}"


class FakeMarket:
    def __init__(self): self.name = "Loja Fake"


class FakeMarketRepo:
    async def get_by_id(self, market_id): return FakeMarket()


class FakePosLocationProvider:
    async def get_location(self, market_id):
        return {"street_number": "1", "street_name": "R", "city_name": "SP",
                "state_name": "SP", "latitude": -23.5, "longitude": -46.6}


class FakeProvider:
    def __init__(self): self.called_with=None
    async def create_qr_payment(self, **kw):
        self.called_with = kw
        return PixOrderResult("ORD1", "created", "created", PixAttemptStatus.PENDING,
                              Decimal("30.00"), "BRL", "QRDATA", "42")


class FakeLock:
    async def acquire(self, k, ttl): return True
    async def release(self, k): return None


@pytest.mark.asyncio
async def test_create_qr_uses_backend_total(monkeypatch):
    monkeypatch.setenv("MP_ORDER_DEFAULT_EXPIRATION", "PT5M")
    monkeypatch.setenv("MP_ENABLED", "true")
    monkeypatch.setenv("MP_APP_ID", "app-1")
    monkeypatch.setenv("MP_CLIENT_SECRET", "secret")
    monkeypatch.setenv("MP_OAUTH_REDIRECT_URI", "https://cb")
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "whsec")
    monkeypatch.setenv("MP_SECRET_KEY", "k" * 32)
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "redis")
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    from application.services.pix.payment_service import PixPaymentService

    market_id = uuid.uuid4()
    p1 = Product(uuid.uuid4(), Decimal("10.00"), market_id=market_id)
    p2 = Product(uuid.uuid4(), Decimal("10.00"), market_id=market_id)
    from domain.sales import BoxStatus
    box = FakeBox(market_id); box.status = BoxStatus.OPEN
    provider = FakeProvider()
    svc = PixPaymentService(
        attempt_repo=FakeAttemptRepo(), sale_repo=FakeSaleRepo(), box_repo=FakeBoxRepo(box),
        product_repo=FakeProductRepo([p1, p2]), connection_service=FakeConnSvc(),
        provider=provider, lock=FakeLock(),
        market_repo=FakeMarketRepo(), pos_location_provider=FakePosLocationProvider(),
    )
    # cliente pede 2x p1 + 1x p2 = 30.00; NENHUM valor é enviado pelo cliente
    attempt = await svc.create_qr(
        market_id=market_id, terminal_id=uuid.uuid4(), box_id=box.id, operator_id=uuid.uuid4(),
        items=[{"product_id": p1.id, "quantity": 2}, {"product_id": p2.id, "quantity": 1}],
    )
    assert attempt.amount == Decimal("30.00")
    assert provider.called_with["amount"] == Decimal("30.00")  # backend calculou
    assert attempt.order_id == "ORD1" and attempt.qr_data == "QRDATA"
    assert attempt.external_reference and "-" not in attempt.external_reference  # sem PII, formato seguro
