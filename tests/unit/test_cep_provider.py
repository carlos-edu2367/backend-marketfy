# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest

from infra.providers.cep.viacep import ViaCepProvider


class FakeCache:
    def __init__(self, value=None):
        self.value = value
        self.saved = None

    async def get(self, key):
        return self.value

    async def set(self, key, value, ttl=None):
        self.saved = (key, value, ttl)


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "cep": "01001-000",
            "logradouro": "Praça da Sé",
            "bairro": "Sé",
            "localidade": "São Paulo",
            "uf": "SP",
        }


class FakeClient:
    async def get(self, url, timeout):
        return FakeResponse()


@pytest.mark.asyncio
async def test_lookup_normalizes_cep_and_caches_public_address():
    cache = FakeCache()
    provider = ViaCepProvider(client=FakeClient(), cache=cache)

    result = await provider.lookup("01001-000")

    assert result.postal_code == "01001000"
    assert result.city_name == "São Paulo"
    assert cache.saved[2] == 86400


@pytest.mark.asyncio
async def test_lookup_returns_cached_success_when_provider_is_unavailable():
    cached = {"postal_code": "01001000", "street_name": "Rua A", "district": "Centro",
              "city_name": "São Paulo", "state_code": "SP"}
    provider = ViaCepProvider(client=RuntimeErrorClient(), cache=FakeCache(cached))

    result = await provider.lookup("01001000")

    assert result.street_name == "Rua A"


class RuntimeErrorClient:
    async def get(self, url, timeout):
        raise TimeoutError("provider offline")
