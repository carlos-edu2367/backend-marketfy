"""
NeectifyFiscalProvider — adaptador para a API Neectify Fiscal.

Implementa FiscalProviderGateway usando o NeectifyFiscalClient de baixo nível.
Encapsula todo o mapeamento entre a interface interna do Marketfy e o contrato
REST do Neectify Fiscal.

Autenticação: Bearer token em todos os requests (gerenciado pelo client).
Idempotency-Key: enviado como header nos endpoints de emissão, cancelamento e
inutilização.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from domain.fiscal import FiscalAuthError, FiscalValidationError
from infra.clients.neectify_fiscal_client import NeectifyFiscalClient
from infra.providers.fiscal.base import (
    EmitResultStatus,
    FiscalProviderGateway,
    ProviderCancelResult,
    ProviderConsultResult,
    ProviderEmitResult,
    ProviderInutilizacaoResult,
    ProviderResponse,
)

logger = logging.getLogger("neectify_fiscal_provider")

_NEECTIFY_STATUS_MAP = {
    "authorized": EmitResultStatus.AUTHORIZED,
    "rejected": EmitResultStatus.REJECTED,
    "cancelled": EmitResultStatus.AUTHORIZED,  # tratado como terminal upstream
    "failed_permanent": EmitResultStatus.REJECTED,
}
_PENDING_STATUSES = {
    "received", "queued", "xml_generated", "signed",
    "xsd_validated", "sent", "processing",
    "cancel_requested", "failed_transient",
}


class NeectifyFiscalProvider(FiscalProviderGateway):
    """
    Provider fiscal usando a API Neectify Fiscal.

    Métodos de onboarding (create_issuer, create_nfce_config,
    upload_certificate, activate_certificate) são chamados pelo
    FiscalOnboardingService durante o setup inicial de cada emitente.
    """

    def __init__(self, client: NeectifyFiscalClient):
        self._client = client

    # =========================================================================
    # ONBOARDING
    # =========================================================================

    async def create_issuer(self, payload: dict) -> dict:
        """POST /v1/issuers"""
        return await self._client.request("POST", "/v1/issuers", json=payload)

    async def create_nfce_config(self, issuer_id: str, payload: dict) -> dict:
        """POST /v1/issuers/{issuer_id}/nfce-configs"""
        return await self._client.request(
            "POST",
            f"/v1/issuers/{issuer_id}/nfce-configs",
            json=payload,
        )

    async def upload_certificate(
        self,
        issuer_id: str,
        pfx_bytes: bytes,
        password: str,
        environment: str,
    ) -> dict:
        """POST /v1/issuers/{issuer_id}/certificates  (multipart/form-data)"""
        files = {
            "file": ("certificate.pfx", pfx_bytes, "application/octet-stream"),
            "password": (None, password),
            "environment": (None, environment),
        }
        return await self._client.request(
            "POST",
            f"/v1/issuers/{issuer_id}/certificates",
            files=files,
        )

    async def activate_certificate(self, certificate_id: str) -> dict:
        """POST /v1/certificates/{certificate_id}/activate"""
        return await self._client.request(
            "POST",
            f"/v1/certificates/{certificate_id}/activate",
        )

    # =========================================================================
    # EMISSÃO NFC-e
    # =========================================================================

    async def emit_nfce(
        self,
        provider_ref: str,
        payload: Dict[str, Any],
        api_token: str,
        environment: str,
    ) -> ProviderEmitResult:
        """
        POST /v1/nfce com Idempotency-Key.

        Retorna 202 Accepted com status "queued".
        """
        idempotency_key = provider_ref
        try:
            data = await self._client.request(
                "POST",
                "/v1/nfce",
                json=payload,
                headers={"Idempotency-Key": idempotency_key},
            )
        except FiscalAuthError as exc:
            logger.error(
                "neectify_emit_auth_error",
                extra={"extra_data": {"provider_ref": provider_ref}},
            )
            return ProviderEmitResult(
                status=EmitResultStatus.PROVIDER_ERROR,
                provider_ref=provider_ref,
                error_message=str(exc),
                http_status=401,
                is_recoverable=False,
            )
        except FiscalValidationError as exc:
            return ProviderEmitResult(
                status=EmitResultStatus.REJECTED,
                provider_ref=provider_ref,
                error_message=str(exc),
                http_status=422,
                is_recoverable=False,
                raw_response=exc.details,
            )
        except Exception as exc:
            logger.warning(
                "neectify_emit_error",
                extra={"extra_data": {"provider_ref": provider_ref, "error": type(exc).__name__}},
            )
            return ProviderEmitResult(
                status=EmitResultStatus.PROVIDER_ERROR,
                provider_ref=provider_ref,
                error_message=type(exc).__name__,
                is_recoverable=True,
            )

        neectify_id = data.get("id", "")
        status = data.get("status", "")

        logger.info(
            "neectify_emit_queued",
            extra={"extra_data": {"provider_ref": provider_ref, "neectify_id": neectify_id}},
        )

        return ProviderEmitResult(
            status=EmitResultStatus.UNKNOWN,
            provider_ref=neectify_id or provider_ref,
            provider_status=status,
            http_status=202,
            is_recoverable=True,
        )

    async def get_nfce_status(self, nfce_id: str) -> dict:
        """GET /v1/nfce/{nfce_id}"""
        return await self._client.request("GET", f"/v1/nfce/{nfce_id}")

    async def consult_nfce(
        self,
        provider_ref: str,
        api_token: str,
        environment: str,
    ) -> ProviderConsultResult:
        """GET /v1/nfce/{provider_ref} para polling de status."""
        try:
            data = await self._client.request("GET", f"/v1/nfce/{provider_ref}")
        except FiscalAuthError as exc:
            return ProviderConsultResult(
                found=False,
                provider_ref=provider_ref,
                error_message=str(exc),
            )
        except Exception as exc:
            logger.warning(
                "neectify_consult_error",
                extra={"extra_data": {"provider_ref": provider_ref, "error": type(exc).__name__}},
            )
            return ProviderConsultResult(
                found=False,
                provider_ref=provider_ref,
                error_message=type(exc).__name__,
            )

        neectify_status = data.get("status", "")
        access_key = data.get("access_key")
        series = data.get("series")
        number = data.get("number")

        if neectify_status == "authorized":
            emit_status = EmitResultStatus.AUTHORIZED
        elif neectify_status in ("rejected", "failed_permanent"):
            emit_status = EmitResultStatus.REJECTED
        elif neectify_status in _PENDING_STATUSES:
            emit_status = EmitResultStatus.UNKNOWN
        else:
            emit_status = EmitResultStatus.UNKNOWN

        return ProviderConsultResult(
            found=True,
            provider_ref=provider_ref,
            status=emit_status,
            access_key=access_key,
            protocol=data.get("protocol"),
            number=int(number) if number else None,
            series=int(series) if series else None,
            sefaz_status_code=neectify_status,
            sefaz_message=data.get("sefaz_message"),
        )

    async def cancel_nfce(
        self,
        provider_ref: str,
        access_key: str,
        justification: str,
        api_token: str,
        environment: str,
    ) -> ProviderCancelResult:
        """POST /v1/nfce/{nfce_id}/cancel com Idempotency-Key."""
        idempotency_key = provider_ref.replace(":nfce", ":cancel")
        try:
            data = await self._client.request(
                "POST",
                f"/v1/nfce/{provider_ref}/cancel",
                json={"justification": justification},
                headers={"Idempotency-Key": idempotency_key},
            )
        except FiscalAuthError as exc:
            return ProviderCancelResult(success=False, error_message=str(exc), is_recoverable=False)
        except FiscalValidationError as exc:
            return ProviderCancelResult(success=False, error_message=str(exc), is_recoverable=False)
        except Exception as exc:
            return ProviderCancelResult(success=False, error_message=type(exc).__name__, is_recoverable=True)

        status = data.get("status", "")
        return ProviderCancelResult(
            success=status in ("cancel_requested", "cancelled"),
            protocol=data.get("protocol"),
        )

    async def download_xml(
        self,
        provider_ref: str,
        api_token: str,
        environment: str,
    ) -> Optional[bytes]:
        """GET /v1/nfce/{nfce_id}/xml"""
        try:
            return await self._client.request_bytes("GET", f"/v1/nfce/{provider_ref}/xml")
        except Exception as exc:
            logger.warning(
                "neectify_xml_download_error",
                extra={"extra_data": {"provider_ref": provider_ref, "error": type(exc).__name__}},
            )
            return None

    async def download_pdf(
        self,
        provider_ref: str,
        api_token: str,
        environment: str,
    ) -> Optional[bytes]:
        """GET /v1/nfce/{nfce_id}/danfe — endpoint não especificado no INTEGRATION.md.
        Retorna None se não disponível."""
        try:
            return await self._client.request_bytes("GET", f"/v1/nfce/{provider_ref}/danfe")
        except Exception as exc:
            logger.warning(
                "neectify_pdf_download_error",
                extra={"extra_data": {"provider_ref": provider_ref, "error": type(exc).__name__}},
            )
            return None

    async def inutilize_range(
        self,
        payload: dict,
        idempotency_key: str,
    ) -> dict:
        """POST /v1/nfce/numbering/inutilize com Idempotency-Key."""
        return await self._client.request(
            "POST",
            "/v1/nfce/numbering/inutilize",
            json=payload,
            headers={"Idempotency-Key": idempotency_key},
        )

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
        """Implementa FiscalProviderGateway.inutilize_numbering."""
        cnpj_digits = cnpj.replace(".", "").replace("/", "").replace("-", "")
        idempotency_key = f"marketfy:inutilize:{cnpj_digits}:{series}:{start_number}:{end_number}"
        payload = {
            "cnpj": cnpj_digits,
            "environment": environment,
            "series": series,
            "number_start": start_number,
            "number_end": end_number,
            "justification": justification,
        }
        try:
            data = await self.inutilize_range(payload=payload, idempotency_key=idempotency_key)
        except FiscalAuthError as exc:
            return ProviderInutilizacaoResult(
                success=False, error_message=str(exc), is_recoverable=False
            )
        except FiscalValidationError as exc:
            return ProviderInutilizacaoResult(
                success=False, error_message=str(exc), is_recoverable=False
            )
        except Exception as exc:
            return ProviderInutilizacaoResult(
                success=False, error_message=type(exc).__name__, is_recoverable=True
            )

        status = data.get("status", "")
        return ProviderInutilizacaoResult(
            success=status in ("queued", "authorized"),
            protocol=data.get("protocol"),
            message=data.get("sefaz_reason"),
        )

    async def register_company(
        self,
        cnpj: str,
        company_data: Dict[str, Any],
        api_token: str,
        environment: str,
    ) -> ProviderResponse:
        """
        Cadastra empresa no Neectify criando um issuer.

        Mantido para compatibilidade com FiscalProviderGateway.
        Use create_issuer() para o fluxo de onboarding completo.
        """
        try:
            data = await self.create_issuer(company_data)
            return ProviderResponse(success=True, data=data)
        except FiscalValidationError as exc:
            return ProviderResponse(success=False, error=str(exc))
        except Exception as exc:
            return ProviderResponse(success=False, error=type(exc).__name__)

    async def close(self) -> None:
        await self._client.close()
