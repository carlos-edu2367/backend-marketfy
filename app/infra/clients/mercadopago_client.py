"""Cliente HTTP para a API do Mercado Pago (OAuth + Orders)."""

from __future__ import annotations
import asyncio
from decimal import Decimal
from typing import Optional

import httpx

from domain.pix import PixCredentials, PixOrderResult, map_order_status
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

    async def _request_order(self, method: str, path: str, *, access_token: str,
                             json_body: dict | None = None, idempotency_key: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json",
                   "Content-Type": "application/json"}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.request(method, f"{self._api_base}{path}",
                                                json=json_body, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RequestError) as exc:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise MercadoPagoUnavailableError("Falha de rede com Mercado Pago (orders).") from exc
            if resp.status_code in (200, 201):
                return resp.json()
            if resp.status_code in (401, 403):
                raise MercadoPagoAuthError("Token Mercado Pago inválido/expirado.", status_code=resp.status_code)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            # inclui 400 idempotency_key_already_used e validações
            raise MercadoPagoError(f"Mercado Pago orders retornou {resp.status_code}.", status_code=resp.status_code)
        raise MercadoPagoUnavailableError("Mercado Pago indisponível (orders).")

    @staticmethod
    def _dec(value) -> Decimal:
        return Decimal(str(value)) if value is not None else Decimal("0.00")

    @staticmethod
    def _extract_qr_data(data: dict) -> Optional[str]:
        # Robustez: procurar em caminhos conhecidos (confirmar no spike).
        for path in (("type_response", "qr_data"), ("qr_data",),
                     ("config", "qr", "qr_data"), ("point_of_interaction", "transaction_data", "qr_code")):
            node = data
            ok = True
            for key in path:
                if isinstance(node, dict) and key in node:
                    node = node[key]
                else:
                    ok = False
                    break
            if ok and isinstance(node, str):
                return node
        return None

    @classmethod
    def _parse_order(cls, data: dict) -> PixOrderResult:
        status = str(data.get("status", ""))
        detail = data.get("status_detail")
        return PixOrderResult(
            order_id=str(data.get("id", "")),
            external_status=status,
            status_detail=detail,
            mapped_status=map_order_status(status, detail),
            total_amount=cls._dec(data.get("total_amount")),
            currency=str(data.get("currency_id", "BRL")),
            qr_data=cls._extract_qr_data(data),
            receiver_account_id=str(data.get("user_id")) if data.get("user_id") is not None else None,
        )

    async def create_qr_order(self, *, access_token, amount: Decimal, external_reference,
                              external_pos_id, description, items, expiration, idempotency_key) -> PixOrderResult:
        body = {
            "type": "qr",
            "total_amount": f"{amount:.2f}",
            "external_reference": external_reference,
            "description": description[:150],
            "expiration_time": expiration,
            "config": {"qr": {"external_pos_id": external_pos_id, "mode": "dynamic"}},
            "transactions": {"payments": [{"amount": f"{amount:.2f}"}]},
            "items": [
                {"title": it.title[:150], "unit_price": f"{it.unit_price:.2f}",
                 "quantity": int(it.quantity), "unit_measure": it.unit_measure,
                 **({"external_code": it.external_code} if it.external_code else {})}
                for it in items
            ],
        }
        data = await self._request_order("POST", "/v1/orders", access_token=access_token,
                                         json_body=body, idempotency_key=idempotency_key)
        return self._parse_order(data)

    async def get_order(self, *, access_token, order_id) -> PixOrderResult:
        data = await self._request_order("GET", f"/v1/orders/{order_id}", access_token=access_token)
        return self._parse_order(data)

    async def cancel_order(self, *, access_token, order_id, idempotency_key) -> PixOrderResult:
        data = await self._request_order("POST", f"/v1/orders/{order_id}/cancel",
                                         access_token=access_token, idempotency_key=idempotency_key)
        return self._parse_order(data)

    async def create_store(self, *, access_token: str, user_id: str, name: str,
                           external_id: str, location: dict) -> dict:
        return await self._request_order(
            "POST", f"/users/{user_id}/stores", access_token=access_token,
            json_body={"name": name, "external_id": external_id, "location": location},
        )

    async def create_pos(self, *, access_token: str, name: str, store_id,
                         external_store_id: str, external_id: str) -> dict:
        return await self._request_order(
            "POST", "/pos", access_token=access_token,
            json_body={"name": name, "fixed_amount": True, "store_id": store_id,
                      "external_store_id": external_store_id, "external_id": external_id},
        )
