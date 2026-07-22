from __future__ import annotations

import re

import httpx

from infra.cache.redis_adapter import redis_client
from infra.providers.cep.base import CepAddress


class ViaCepProvider:
    def __init__(self, client=None, cache=None, timeout_seconds: float = 2.0):
        self.client = client
        self.cache = cache or redis_client
        self.timeout_seconds = timeout_seconds

    async def lookup(self, postal_code: str) -> CepAddress | None:
        normalized = re.sub(r"\D", "", str(postal_code or ""))
        if not re.fullmatch(r"\d{8}", normalized):
            return None

        key = f"address:cep:{normalized}"
        cached = await self.cache.get(key)
        if cached:
            return self._from_cached(cached)

        try:
            response = await self._get(f"https://viacep.com.br/ws/{normalized}/json/")
            if response.status_code != 200:
                return None
            payload = response.json()
            if payload.get("erro"):
                return None
            result = CepAddress(
                postal_code=normalized,
                street_name=str(payload.get("logradouro") or "").strip(),
                city_name=str(payload.get("localidade") or "").strip(),
                state_code=str(payload.get("uf") or "").strip().upper(),
                district=str(payload.get("bairro") or "").strip() or None,
            )
            if not result.street_name or not result.city_name or not result.state_code:
                return None
            await self.cache.set(key, result.public_dict(), ttl=86400)
            return result
        except (httpx.HTTPError, TimeoutError, OSError):
            return None

    async def _get(self, url: str):
        if self.client is not None:
            return await self.client.get(url, timeout=self.timeout_seconds)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            return await client.get(url)

    @staticmethod
    def _from_cached(payload: dict) -> CepAddress:
        return CepAddress(
            postal_code=payload["postal_code"],
            street_name=payload["street_name"],
            city_name=payload["city_name"],
            state_code=payload["state_code"],
            district=payload.get("district"),
        )
