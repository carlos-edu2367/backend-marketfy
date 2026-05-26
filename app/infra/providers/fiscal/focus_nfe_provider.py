"""
FocusNFeProviderV2 — Adaptador para a API V2 da Focus NFe.

Documentação: https://focusnfe.com.br/doc/
Endpoint NFC-e: POST https://api.focusnfe.com.br/v2/nfce?ref={ref}

Notas importantes:
- A Focus usa Basic Auth com token no username e senha vazia.
- O campo `ref` é obrigatório e único por NFC-e (usado para idempotência).
- Emissão é síncrona: autoriza ou rejeita na mesma chamada (normalmente).
- `forma_emissao=offline` ativa contingência offline.
- Nunca logar o api_token nem o payload completo com dados pessoais.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

import httpx

from infra.config.logger import get_logger
from infra.providers.fiscal.base import (
    EmitResultStatus,
    FiscalProviderGateway,
    ProviderCancelResult,
    ProviderConsultResult,
    ProviderEmitResult,
    ProviderInutilizacaoResult,
    ProviderResponse,
)

logger = get_logger("focus_nfe_v2")

_BASE_HOMOLOG = "https://homologacao.focusnfe.com.br/v2"
_BASE_PROD = "https://api.focusnfe.com.br/v2"

# Timeout agressivo para o caminho do caixa; worker pode usar valor maior
_EMIT_TIMEOUT = 8.0
_CONSULT_TIMEOUT = 10.0


def _base_url(environment: str) -> str:
    return _BASE_PROD if environment == "producao" else _BASE_HOMOLOG


def _auth(api_token: str):
    return (api_token, "")


def _sanitize_ref(provider_ref: str) -> str:
    """Remove chars inválidos do ref Focus (apenas alfanumérico, - e _)."""
    return provider_ref.replace(" ", "-")[:50]


class FocusNFeProviderV2(FiscalProviderGateway):
    """Adaptador Focus NFe V2 para emissão NFC-e."""

    # Client pool compartilhado por instance — aquece conexão HTTP
    _client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=3.0, read=_EMIT_TIMEOUT, write=5.0, pool=5.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def emit_nfce(
        self,
        provider_ref: str,
        payload: Dict[str, Any],
        api_token: str,
        environment: str,
    ) -> ProviderEmitResult:
        ref = _sanitize_ref(provider_ref)
        url = f"{_base_url(environment)}/nfce"
        req_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]

        logger.info(
            "focus_emit_start",
            extra={"extra_data": {"provider_ref": ref, "environment": environment, "req_hash": req_hash}},
        )

        client = await self._get_client()
        try:
            resp = await client.post(
                url,
                params={"ref": ref},
                json=payload,
                auth=_auth(api_token),
            )
        except httpx.TimeoutException:
            logger.warning("focus_emit_timeout", extra={"extra_data": {"provider_ref": ref}})
            return ProviderEmitResult(
                status=EmitResultStatus.TIMEOUT,
                provider_ref=provider_ref,
                error_message="Timeout na comunicação com Focus NFe",
                is_recoverable=True,
            )
        except httpx.RequestError as exc:
            logger.error("focus_emit_connection_error", extra={"extra_data": {"error": str(exc), "provider_ref": ref}})
            return ProviderEmitResult(
                status=EmitResultStatus.PROVIDER_ERROR,
                provider_ref=provider_ref,
                error_message=f"Erro de conexão: {type(exc).__name__}",
                is_recoverable=True,
            )

        return self._parse_emit_response(resp, provider_ref)

    def _parse_emit_response(self, resp: httpx.Response, provider_ref: str) -> ProviderEmitResult:
        http_status = resp.status_code

        try:
            data = resp.json()
        except Exception:
            data = {}

        # Sucesso: 200/201 (autorizada) ou 202 (processando, raro)
        if http_status in (200, 201, 202):
            sefaz_status = data.get("status", "")
            chave = data.get("chave_nfe") or data.get("chave")
            protocolo = data.get("protocolo")
            numero = data.get("numero")
            serie = data.get("serie")
            mensagem = data.get("mensagem_sefaz") or data.get("mensagem") or ""

            # Focus: status "autorizado" = nota aprovada; outros = processando/pendente
            if sefaz_status in ("autorizado", "autorizada"):
                return ProviderEmitResult(
                    status=EmitResultStatus.AUTHORIZED,
                    provider_ref=provider_ref,
                    access_key=chave,
                    protocol=protocolo,
                    number=int(numero) if numero else None,
                    series=int(serie) if serie else None,
                    sefaz_status_code=sefaz_status,
                    sefaz_message=mensagem,
                    provider_status=sefaz_status,
                    http_status=http_status,
                )
            # 202 ou status não conclusivo — tratar como desconhecido para reconciliar
            return ProviderEmitResult(
                status=EmitResultStatus.UNKNOWN,
                provider_ref=provider_ref,
                access_key=chave,
                protocol=protocolo,
                number=int(numero) if numero else None,
                sefaz_status_code=sefaz_status,
                sefaz_message=mensagem,
                provider_status=sefaz_status,
                http_status=http_status,
                is_recoverable=True,
            )

        # Rejeição SEFAZ (400/422 com mensagem de rejeição)
        if http_status in (400, 422):
            erros = data.get("erros") or []
            if erros and isinstance(erros, list):
                msg = erros[0].get("mensagem", "Erro de validação")
                code = erros[0].get("codigo", "")
            else:
                msg = data.get("mensagem") or f"HTTP {http_status}"
                code = data.get("codigo", "")

            # Referência já utilizada — precisa consultar antes de reenviar
            if "referencia" in msg.lower() and "utilizada" in msg.lower():
                return ProviderEmitResult(
                    status=EmitResultStatus.DUPLICATE_REF,
                    provider_ref=provider_ref,
                    error_code=code,
                    error_message=msg,
                    http_status=http_status,
                    is_recoverable=False,
                )

            return ProviderEmitResult(
                status=EmitResultStatus.REJECTED,
                provider_ref=provider_ref,
                error_code=code,
                error_message=msg,
                http_status=http_status,
                is_recoverable=False,
            )

        # Erros de servidor (5xx) ou SEFAZ indisponível
        if http_status >= 500:
            return ProviderEmitResult(
                status=EmitResultStatus.SEFAZ_UNAVAILABLE,
                provider_ref=provider_ref,
                error_message=f"Provider error HTTP {http_status}",
                http_status=http_status,
                is_recoverable=True,
            )

        # Qualquer outro status — tratar como erro recuperável
        return ProviderEmitResult(
            status=EmitResultStatus.PROVIDER_ERROR,
            provider_ref=provider_ref,
            error_message=f"HTTP {http_status} inesperado",
            http_status=http_status,
            is_recoverable=True,
        )

    async def consult_nfce(
        self,
        provider_ref: str,
        api_token: str,
        environment: str,
    ) -> ProviderConsultResult:
        ref = _sanitize_ref(provider_ref)
        url = f"{_base_url(environment)}/nfce/{ref}"

        client = await self._get_client()
        try:
            resp = await client.get(url, auth=_auth(api_token),
                                    timeout=_CONSULT_TIMEOUT)
        except Exception as exc:
            logger.warning("focus_consult_error", extra={"extra_data": {"provider_ref": ref, "error": str(exc)}})
            return ProviderConsultResult(found=False, provider_ref=provider_ref,
                                         error_message=str(exc))

        if resp.status_code == 404:
            return ProviderConsultResult(found=False, provider_ref=provider_ref)

        if resp.status_code in (200, 201):
            data = resp.json()
            sefaz_status = data.get("status", "")
            chave = data.get("chave_nfe") or data.get("chave")
            protocol = data.get("protocolo")
            numero = data.get("numero")
            serie = data.get("serie")
            mensagem = data.get("mensagem_sefaz") or data.get("mensagem") or ""

            if sefaz_status in ("autorizado", "autorizada"):
                emit_status = EmitResultStatus.AUTHORIZED
            elif sefaz_status in ("cancelado", "cancelada"):
                emit_status = EmitResultStatus.UNKNOWN  # Tratado como cancelado upstream
            elif sefaz_status in ("rejeitado", "rejeitada", "erro"):
                emit_status = EmitResultStatus.REJECTED
            else:
                emit_status = EmitResultStatus.UNKNOWN

            return ProviderConsultResult(
                found=True,
                provider_ref=provider_ref,
                status=emit_status,
                access_key=chave,
                protocol=protocol,
                number=int(numero) if numero else None,
                series=int(serie) if serie else None,
                sefaz_status_code=sefaz_status,
                sefaz_message=mensagem,
            )

        return ProviderConsultResult(found=False, provider_ref=provider_ref,
                                      error_message=f"HTTP {resp.status_code}")

    async def cancel_nfce(
        self,
        provider_ref: str,
        access_key: str,
        justification: str,
        api_token: str,
        environment: str,
    ) -> ProviderCancelResult:
        ref = _sanitize_ref(provider_ref)
        url = f"{_base_url(environment)}/nfce/{ref}"

        client = await self._get_client()
        try:
            resp = await client.delete(
                url,
                json={"justificativa": justification},
                auth=_auth(api_token),
                timeout=15.0,
            )
        except Exception as exc:
            return ProviderCancelResult(success=False, error_message=str(exc))

        if resp.status_code in (200, 201):
            data = resp.json()
            return ProviderCancelResult(
                success=True,
                protocol=data.get("protocolo"),
            )
        return ProviderCancelResult(
            success=False,
            error_message=f"HTTP {resp.status_code}: {resp.text[:200]}",
            is_recoverable=resp.status_code >= 500,
        )

    async def download_xml(self, provider_ref: str, api_token: str, environment: str) -> Optional[bytes]:
        ref = _sanitize_ref(provider_ref)
        url = f"{_base_url(environment)}/nfce/{ref}/xml"
        client = await self._get_client()
        try:
            resp = await client.get(url, auth=_auth(api_token), timeout=15.0)
            if resp.status_code == 200:
                return resp.content
        except Exception as exc:
            logger.warning("focus_xml_download_error", extra={"extra_data": {"provider_ref": ref, "error": str(exc)}})
        return None

    async def download_pdf(self, provider_ref: str, api_token: str, environment: str) -> Optional[bytes]:
        ref = _sanitize_ref(provider_ref)
        url = f"{_base_url(environment)}/nfce/{ref}/danfe"
        client = await self._get_client()
        try:
            resp = await client.get(url, auth=_auth(api_token), timeout=15.0)
            if resp.status_code == 200:
                return resp.content
        except Exception as exc:
            logger.warning("focus_pdf_download_error", extra={"extra_data": {"provider_ref": ref, "error": str(exc)}})
        return None

    async def inutilize_numbering(
        self,
        cnpj: str,
        series: int,
        start_number: int,
        end_number: int,
        justification: str,
        api_token: str,
        environment: str,
    ) -> ProviderInutilizacaoResult:
        """
        Inutiliza intervalo de numeração junto à SEFAZ via Focus NFe.

        Focus NFe endpoint: POST /v2/nfce/inutilizacoes
        Ref: https://focusnfe.com.br/doc/#inutilizacao-de-nfe-ou-nfce
        """
        cnpj_digits = cnpj.replace(".", "").replace("/", "").replace("-", "")
        url = f"{_base_url(environment)}/nfce/inutilizacoes"
        payload = {
            "cnpj": cnpj_digits,
            "serie": series,
            "numero_inicial": start_number,
            "numero_final": end_number,
            "justificativa": justification,
        }
        client = await self._get_client()
        try:
            resp = await client.post(url, json=payload, auth=_auth(api_token), timeout=30.0)
        except Exception as exc:
            return ProviderInutilizacaoResult(
                success=False,
                error_message=str(exc),
                is_recoverable=True,
            )

        if resp.status_code in (200, 201):
            data = resp.json()
            return ProviderInutilizacaoResult(
                success=True,
                protocol=data.get("protocolo"),
                message=data.get("mensagem") or "Inutilização concluída",
            )
        return ProviderInutilizacaoResult(
            success=False,
            error_message=f"HTTP {resp.status_code}: {resp.text[:300]}",
            is_recoverable=resp.status_code >= 500,
        )

    async def register_company(
        self,
        cnpj: str,
        company_data: Dict[str, Any],
        api_token: str,
        environment: str,
    ) -> ProviderResponse:
        url = f"{_base_url(environment)}/empresas/{cnpj.replace('.', '').replace('/', '').replace('-', '')}"
        client = await self._get_client()
        try:
            resp = await client.post(url, json=company_data, auth=_auth(api_token), timeout=15.0)
            if resp.status_code in (200, 201):
                return ProviderResponse(success=True, data=resp.json())
            return ProviderResponse(success=False, error=f"HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            return ProviderResponse(success=False, error=str(exc))

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
