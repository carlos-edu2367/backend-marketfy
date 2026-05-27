"""Cliente HTTP de baixo nível para a API Neectify Fiscal.

Responsabilidade: gerenciar sessão httpx, headers padrão, timeouts,
retry em erros transitórios e deserialização de erros da API.
Sem lógica de negócio — apenas I/O.

Segurança:
- API key nunca aparece em logs.
- Erros HTTP com detalhes só são logados no servidor, nunca propagados
  diretamente ao frontend.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from domain.fiscal import FiscalAuthError, FiscalValidationError

logger = logging.getLogger("neectify_fiscal_client")

_RETRYABLE_STATUS = {429, 503, 504}
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0


class NeectifyFiscalClient:
    """
    Sessão HTTP reutilizável para a API Neectify Fiscal.

    Instanciar uma vez por processo e reutilizar — mantém connection pool.
    """

    def __init__(self, api_key: str, base_url: str, timeout_seconds: int = 30, internal_client: str = "marketfy"):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._internal_client = internal_client
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "X-API-Key": self._api_key,
                    "X-Internal-Client": self._internal_client,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=float(self._timeout),
                    write=10.0,
                    pool=5.0,
                ),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        content: Optional[bytes] = None,
        files: Optional[dict] = None,
        headers: Optional[Dict[str, str]] = None,
        extra_timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Executa requisição com retry automático para erros transitórios.

        Raises:
            FiscalAuthError: HTTP 401/403.
            FiscalValidationError: HTTP 422 (payload inválido).
            httpx.TimeoutException: timeout esgotado após retries.
            httpx.HTTPStatusError: outros 4xx/5xx não recuperáveis.
        """
        client = self._get_client()
        request_headers = dict(headers or {})
        # Não logar o header X-API-Key
        log_headers = {k: v for k, v in request_headers.items() if k.lower() != "x-api-key"}

        timeout = httpx.Timeout(float(extra_timeout or self._timeout))

        for attempt in range(_MAX_RETRIES):
            try:
                if files is not None:
                    # multipart/form-data — não inclui Content-Type (httpx define)
                    send_headers = {
                        k: v
                        for k, v in {**client.headers, **request_headers}.items()
                        if k.lower() != "content-type"
                    }
                    resp = await client.request(
                        method,
                        path,
                        files=files,
                        headers=send_headers,
                        timeout=timeout,
                    )
                elif content is not None:
                    resp = await client.request(
                        method,
                        path,
                        content=content,
                        headers=request_headers,
                        timeout=timeout,
                    )
                else:
                    resp = await client.request(
                        method,
                        path,
                        json=json,
                        headers=request_headers,
                        timeout=timeout,
                    )
            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_BACKOFF_BASE ** attempt)
                    continue
                logger.warning(
                    "neectify_timeout",
                    extra={"extra_data": {"method": method, "path": path, "attempt": attempt + 1}},
                )
                raise

            if resp.status_code in (401, 403):
                logger.error(
                    "neectify_auth_error",
                    extra={"extra_data": {"method": method, "path": path, "status": resp.status_code}},
                )
                raise FiscalAuthError(
                    f"Neectify Fiscal retornou HTTP {resp.status_code}: credencial inválida ou sem permissão."
                )

            if resp.status_code == 422:
                try:
                    details = resp.json()
                except Exception:
                    details = {}
                raise FiscalValidationError(
                    f"Payload inválido para {method} {path}",
                    details=details,
                )

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", _BACKOFF_BASE ** attempt))
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(retry_after)
                    continue
                resp.raise_for_status()

            if resp.status_code in (503, 504):
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_BACKOFF_BASE ** attempt)
                    continue
                resp.raise_for_status()

            resp.raise_for_status()

            if resp.status_code == 204 or len(resp.content) == 0:
                return {}
            return resp.json()

        # Nunca chega aqui — apenas para satisfazer o tipo
        raise RuntimeError("Retry loop encerrado inesperadamente")

    async def request_bytes(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> bytes:
        """Retorna resposta como bytes (usado para download de XML)."""
        client = self._get_client()
        request_headers = dict(headers or {})
        timeout = httpx.Timeout(float(self._timeout))

        resp = await client.request(
            method,
            path,
            headers=request_headers,
            timeout=timeout,
        )

        if resp.status_code in (401, 403):
            raise FiscalAuthError(
                f"Neectify Fiscal retornou HTTP {resp.status_code}"
            )

        resp.raise_for_status()
        return resp.content

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
