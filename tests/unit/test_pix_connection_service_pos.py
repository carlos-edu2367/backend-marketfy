# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import uuid
import pytest


class FakePosRepo:
    def __init__(self): self.saved = None
    async def get_by_market_and_terminal(self, market_id, terminal_id): return None
    async def get_store_id_for_market(self, market_id): return None
    async def save(self, model, commit=True): self.saved = model; return model


class FakeClient:
    async def create_store(self, **kw): return {"id": 1234567}
    async def create_pos(self, **kw): return {"id": 2711382, "external_id": kw["external_id"]}


@pytest.mark.asyncio
async def test_ensure_pos_registered_creates_store_and_pos(monkeypatch):
    monkeypatch.setenv("MP_SECRET_KEY", "k" * 32)
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    from application.services.pix.connection_service import MercadoPagoConnectionService

    svc = MercadoPagoConnectionService(connection_repo=None, client=FakeClient(), lock=None)
    svc.pos_repo = FakePosRepo()  # injeção mínima para o teste; ajustar assinatura real do __init__
    external_pos_id = await svc.ensure_pos_registered(
        market_id=uuid.uuid4(), terminal_id=uuid.uuid4(), access_token="AT",
        market_name="Loja", location={"street_number": "1", "street_name": "R",
                                      "city_name": "SP", "state_name": "SP",
                                      "latitude": -23.5, "longitude": -46.6},
        mp_user_id="42",
    )
    assert external_pos_id  # gerado a partir do terminal_id (curto, alfanumérico, <=40 chars)
