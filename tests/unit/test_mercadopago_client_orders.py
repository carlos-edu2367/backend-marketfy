# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from decimal import Decimal
import pytest
import respx
from httpx import Response

from domain.pix import PixItem, PixAttemptStatus
from infra.clients.mercadopago_client import MercadoPagoClient


@pytest.mark.asyncio
@respx.mock
async def test_create_qr_order_sends_headers_and_parses(monkeypatch):
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    route = respx.post("https://api.mercadopago.com/v1/orders").mock(
        return_value=Response(201, json={
            "id": "ORD01", "status": "created", "status_detail": "created",
            "total_amount": "54.90",
            "type_response": {"qr_data": "000201QRDATA"},
            "user_id": "42",
        })
    )
    client = MercadoPagoClient()
    res = await client.create_qr_order(
        access_token="AT", amount=Decimal("54.90"), external_reference="pixabc",
        external_pos_id="CX01", description="Venda", items=[PixItem("Item", Decimal("54.90"), 1)],
        expiration="PT5M", idempotency_key="idem-1",
    )
    assert route.called
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer AT"
    assert req.headers["X-Idempotency-Key"] == "idem-1"
    assert res.order_id == "ORD01"
    assert res.mapped_status is PixAttemptStatus.PENDING
    assert res.qr_data == "000201QRDATA"
    assert res.total_amount == Decimal("54.90")


@pytest.mark.asyncio
@respx.mock
async def test_get_order_maps_processed(monkeypatch):
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    respx.get("https://api.mercadopago.com/v1/orders/ORD01").mock(
        return_value=Response(200, json={
            "id": "ORD01", "status": "processed", "status_detail": "accredited",
            "total_amount": "54.90", "user_id": "42",
        })
    )
    res = await MercadoPagoClient().get_order(access_token="AT", order_id="ORD01")
    assert res.mapped_status is PixAttemptStatus.APPROVED
    assert res.receiver_account_id == "42"
