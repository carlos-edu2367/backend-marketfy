"""Cliente HTTP para a API do Mercado Pago (OAuth + Orders)."""

from __future__ import annotations
import asyncio
from typing import Optional

import httpx

from domain.pix import PixCredentials
from infra.config.logger import get_logger
from infra.config.settings import get_settings

logger = get_logger("mercadopago_client")


class MercadoPagoError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class MercadoPagoAuthError(MercadoPagoError):
    pass


class MercadoPagoUnavailableError(MercadoPagoError):
    pass


class MercadoPagoClient:
    def __init__(self):
        s = get_settings()
        self._api_base = s.MP_API_BASE_URL.rstrip("/")
        self._app_id = s.MP_APP_ID
        self._client_secret = s.MP_CLIENT_SECRET
        self._timeout = float(s.MP_HTTP_TIMEOUT_SECONDS)

    async def _post_token(self, payload: dict) -> dict:
        last_exc = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(f"{self._api_base}/oauth/token", json=payload,
                                             headers={"Accept": "application/json"})
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RequestError) as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise MercadoPagoUnavailableError("Falha de rede ao contatar Mercado Pago.") from exc
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (400, 401, 403):
                # Nunca logar o payload — contém client_secret/refresh_token.
                logger.warning("mp_oauth_token_rejected", extra={"extra_data": {"status": resp.status_code}})
                raise MercadoPagoAuthError("Credenciais Mercado Pago inválidas.", status_code=resp.status_code)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            raise MercadoPagoError(f"Mercado Pago retornou {resp.status_code}.", status_code=resp.status_code)
        raise MercadoPagoUnavailableError("Mercado Pago indisponível.") from last_exc

    @staticmethod
    def _to_credentials(data: dict) -> PixCredentials:
        return PixCredentials(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_in=int(data.get("expires_in", 0)),
            mp_user_id=str(data.get("user_id", "")),
            scope=data.get("scope"),
        )

    async def exchange_code(self, *, code: str, redirect_uri: str,
                            code_verifier: Optional[str] = None) -> PixCredentials:
        payload = {
            "grant_type": "authorization_code",
            "client_id": self._app_id,
            "client_secret": self._client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier
        return self._to_credentials(await self._post_token(payload))

    async def refresh_credentials(self, *, refresh_token: str) -> PixCredentials:
        payload = {
            "grant_type": "refresh_token",
            "client_id": self._app_id,
            "client_secret": self._client_secret,
            "refresh_token": refresh_token,
        }
        return self._to_credentials(await self._post_token(payload))
