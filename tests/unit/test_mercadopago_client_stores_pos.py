# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
import respx
from httpx import Response
from infra.clients.mercadopago_client import MercadoPagoClient


@pytest.mark.asyncio
@respx.mock
async def test_create_store_and_pos(monkeypatch):
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    respx.post("https://api.mercadopago.com/users/42/stores").mock(
        return_value=Response(201, json={"id": 1234567, "name": "Loja"})
    )
    respx.post("https://api.mercadopago.com/pos").mock(
        return_value=Response(201, json={"id": 2711382, "name": "Caixa 1"})
    )
    client = MercadoPagoClient()
    store = await client.create_store(
        access_token="AT", user_id="42", name="Loja", external_id="M1",
        location={"street_number": "1", "street_name": "R", "city_name": "SP",
                  "state_name": "SP", "latitude": -23.5, "longitude": -46.6},
    )
    assert store["id"] == 1234567
    pos = await client.create_pos(
        access_token="AT", name="Caixa 1", store_id=store["id"],
        external_store_id="M1", external_id="T1",
    )
    assert pos["id"] == 2711382
