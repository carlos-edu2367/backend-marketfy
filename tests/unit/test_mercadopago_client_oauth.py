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

from infra.clients.mercadopago_client import MercadoPagoClient, MercadoPagoAuthError


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_returns_credentials(monkeypatch):
    monkeypatch.setenv("MP_APP_ID", "app-1")
    monkeypatch.setenv("MP_CLIENT_SECRET", "sec-1")
    from infra.config import settings as sm
    sm.get_settings.cache_clear()

    route = respx.post("https://api.mercadopago.com/oauth/token").mock(
        return_value=Response(200, json={
            "access_token": "AT", "refresh_token": "RT", "expires_in": 15552000,
            "user_id": 1403498245, "scope": "offline_access read write", "token_type": "bearer",
        })
    )
    client = MercadoPagoClient()
    creds = await client.exchange_code(code="C", redirect_uri="https://cb", code_verifier="V")
    assert route.called
    assert creds.access_token == "AT"
    assert creds.refresh_token == "RT"
    assert creds.expires_in == 15552000
    assert creds.mp_user_id == "1403498245"


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_maps_401_to_auth_error(monkeypatch):
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    respx.post("https://api.mercadopago.com/oauth/token").mock(return_value=Response(401, json={"message": "invalid"}))
    with pytest.raises(MercadoPagoAuthError):
        await MercadoPagoClient().exchange_code(code="bad", redirect_uri="https://cb")
