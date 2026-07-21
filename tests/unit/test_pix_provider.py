# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from decimal import Decimal
import pytest
from domain.pix import PixItem, PixOrderResult, PixAttemptStatus
from infra.providers.pix.mercadopago import MercadoPagoPixProvider


class FakeClient:
    def __init__(self): self.calls = []
    async def create_qr_order(self, **kw):
        self.calls.append(("create", kw))
        return PixOrderResult("ORD1", "created", "created", PixAttemptStatus.PENDING,
                              Decimal("10.00"), "BRL", "QRDATA", "42")
    async def get_order(self, **kw):
        return PixOrderResult("ORD1", "processed", "accredited", PixAttemptStatus.APPROVED,
                              Decimal("10.00"), "BRL", None, "42")
    async def cancel_order(self, **kw):
        return PixOrderResult("ORD1", "canceled", "canceled", PixAttemptStatus.CANCELED,
                              Decimal("10.00"), "BRL", None, "42")


@pytest.mark.asyncio
async def test_provider_delegates_create():
    prov = MercadoPagoPixProvider(client=FakeClient())
    res = await prov.create_qr_payment(access_token="AT", amount=Decimal("10.00"),
        external_reference="pixabc", external_pos_id="CX01", description="Venda",
        items=[PixItem("X", Decimal("10.00"), 1)], expiration="PT5M", idempotency_key="i1")
    assert res.order_id == "ORD1" and res.qr_data == "QRDATA"
