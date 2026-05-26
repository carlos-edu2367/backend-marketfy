from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from infra.config.logger import get_logger

logger = get_logger("mercado_pago_client")

_RETRYABLE_STATUS = {429, 503, 504}
_MAX_RETRIES = 3


class MercadoPagoAuthError(Exception):
    pass


class MercadoPagoValidationError(Exception):
    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


class MercadoPagoUnavailableError(Exception):
    pass


class MercadoPagoClient:
    BASE_URL = "https://api.mercadopago.com"

    def __init__(
        self,
        access_token: str,
        sandbox: bool = False,
        base_url: str | None = None,
        timeout_seconds: int = 15,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.access_token = access_token
        self.sandbox = sandbox
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(float(self.timeout_seconds)),
                transport=self._transport,
            )
        return self._client

    async def create_preference(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/checkout/preferences", json=payload)

    async def get_payment(self, payment_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/payments/{payment_id}")

    async def search_payments_by_external_reference(self, external_reference: str) -> list[dict[str, Any]]:
        result = await self._request(
            "GET",
            "/v1/payments/search",
            params={"external_reference": external_reference},
        )
        return list(result.get("results", []))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._get_client()
        last_timeout: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                response = await client.request(method, path, json=json, params=params)
            except httpx.TimeoutException as exc:
                last_timeout = exc
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.warning(
                    "mercado_pago_timeout",
                    extra={"extra_data": {"method": method, "path": path, "attempt": attempt + 1}},
                )
                raise MercadoPagoUnavailableError("Timeout ao contatar Mercado Pago.") from exc

            if response.status_code in (401, 403):
                raise MercadoPagoAuthError("Credenciais Mercado Pago invalidas ou sem permissao.")

            if response.status_code in (400, 422):
                try:
                    details = response.json()
                except Exception:
                    details = {}
                raise MercadoPagoValidationError("Payload invalido para Mercado Pago.", details)

            if response.status_code in _RETRYABLE_STATUS:
                if attempt < _MAX_RETRIES - 1:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else float(2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                raise MercadoPagoUnavailableError(f"Mercado Pago indisponivel: HTTP {response.status_code}.")

            response.raise_for_status()
            if response.status_code == 204 or not response.content:
                return {}
            return response.json()

        raise MercadoPagoUnavailableError("Mercado Pago indisponivel.") from last_timeout

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
