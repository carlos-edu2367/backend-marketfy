"""
FakeFiscalProvider — provider simulado para testes e staging.

Comportamento configurável por cenário:
- AUTHORIZE: sempre autoriza (padrão)
- REJECT: sempre rejeita
- TIMEOUT: simula timeout
- PROVIDER_ERROR: simula erro de provider
- DUPLICATE_REF: simula ref duplicada

Latência configurável para testar SLOs.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, Optional

from infra.providers.fiscal.base import (
    EmitResultStatus,
    FiscalProviderGateway,
    ProviderCancelResult,
    ProviderConsultResult,
    ProviderEmitResult,
    ProviderInutilizacaoResult,
    ProviderResponse,
)


class FakeFiscalProvider(FiscalProviderGateway):
    """
    Provider fake para testes, CI e staging.

    Nunca deve ser usado em produção — a factory verifica isso.
    """

    SCENARIO_AUTHORIZE = "authorize"
    SCENARIO_REJECT = "reject"
    SCENARIO_TIMEOUT = "timeout"
    SCENARIO_PROVIDER_ERROR = "provider_error"
    SCENARIO_DUPLICATE_REF = "duplicate_ref"
    SCENARIO_SEFAZ_UNAVAILABLE = "sefaz_unavailable"

    def __init__(
        self,
        scenario: str = SCENARIO_AUTHORIZE,
        latency_ms: float = 0,
        reject_code: str = "225",
        reject_message: str = "Rejeição simulada — NCM inválido",
    ):
        self.scenario = scenario
        self.latency_ms = latency_ms
        self.reject_code = reject_code
        self.reject_message = reject_message
        self._authorized_refs: set[str] = set()

    async def _simulate_latency(self):
        if self.latency_ms > 0:
            await asyncio.sleep(self.latency_ms / 1000)

    def _fake_access_key(self, provider_ref: str) -> str:
        seed = provider_ref.replace("-", "")[:16].ljust(16, "0")
        return f"35{seed}{'0' * 26}"[:44]

    async def emit_nfce(
        self,
        provider_ref: str,
        payload: Dict[str, Any],
        api_token: str,
        environment: str,
    ) -> ProviderEmitResult:
        await self._simulate_latency()

        if self.scenario == self.SCENARIO_TIMEOUT:
            raise asyncio.TimeoutError("Fake timeout")

        if self.scenario == self.SCENARIO_PROVIDER_ERROR:
            return ProviderEmitResult(
                status=EmitResultStatus.PROVIDER_ERROR,
                provider_ref=provider_ref,
                error_message="Fake provider error",
                http_status=503,
                is_recoverable=True,
            )

        if self.scenario == self.SCENARIO_SEFAZ_UNAVAILABLE:
            return ProviderEmitResult(
                status=EmitResultStatus.SEFAZ_UNAVAILABLE,
                provider_ref=provider_ref,
                error_message="SEFAZ indisponível (simulado)",
                http_status=503,
                is_recoverable=True,
            )

        if self.scenario == self.SCENARIO_DUPLICATE_REF:
            return ProviderEmitResult(
                status=EmitResultStatus.DUPLICATE_REF,
                provider_ref=provider_ref,
                error_message="Referência já utilizada",
                http_status=422,
                is_recoverable=False,
            )

        if self.scenario == self.SCENARIO_REJECT:
            return ProviderEmitResult(
                status=EmitResultStatus.REJECTED,
                provider_ref=provider_ref,
                error_code=self.reject_code,
                error_message=self.reject_message,
                sefaz_status_code=self.reject_code,
                sefaz_message=self.reject_message,
                http_status=422,
                is_recoverable=False,
            )

        # Default: AUTHORIZE
        access_key = self._fake_access_key(provider_ref)
        self._authorized_refs.add(provider_ref)
        return ProviderEmitResult(
            status=EmitResultStatus.AUTHORIZED,
            provider_ref=provider_ref,
            access_key=access_key,
            protocol=f"135{uuid.uuid4().hex[:10]}",
            number=int(uuid.uuid4().int % 999999) + 1,
            series=1,
            sefaz_status_code="100",
            sefaz_message="Autorizado o uso da NF-e",
            provider_status="autorizado",
            http_status=200,
            is_recoverable=False,
        )

    async def consult_nfce(
        self,
        provider_ref: str,
        api_token: str,
        environment: str,
    ) -> ProviderConsultResult:
        await self._simulate_latency()
        if provider_ref in self._authorized_refs:
            access_key = self._fake_access_key(provider_ref)
            return ProviderConsultResult(
                found=True,
                provider_ref=provider_ref,
                status=EmitResultStatus.AUTHORIZED,
                access_key=access_key,
                protocol=f"135{uuid.uuid4().hex[:10]}",
                number=1,
                series=1,
                sefaz_status_code="100",
                sefaz_message="Autorizado o uso da NF-e",
            )
        return ProviderConsultResult(found=False, provider_ref=provider_ref)

    async def cancel_nfce(
        self,
        provider_ref: str,
        access_key: str,
        justification: str,
        api_token: str,
        environment: str,
    ) -> ProviderCancelResult:
        await self._simulate_latency()
        if provider_ref in self._authorized_refs:
            self._authorized_refs.discard(provider_ref)
            return ProviderCancelResult(success=True, protocol=f"135{uuid.uuid4().hex[:10]}")
        return ProviderCancelResult(success=False, error_message="Documento não encontrado para cancelamento")

    async def download_xml(self, provider_ref: str, api_token: str, environment: str) -> Optional[bytes]:
        if provider_ref in self._authorized_refs:
            return f'<?xml version="1.0"?><nfce><ref>{provider_ref}</ref></nfce>'.encode()
        return None

    async def download_pdf(self, provider_ref: str, api_token: str, environment: str) -> Optional[bytes]:
        if provider_ref in self._authorized_refs:
            return b"%PDF-1.4 fake danfe"
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
        await self._simulate_latency()
        if self.scenario == self.SCENARIO_PROVIDER_ERROR:
            return ProviderInutilizacaoResult(
                success=False, error_message="Fake provider error inutilizacao", is_recoverable=True
            )
        return ProviderInutilizacaoResult(
            success=True,
            protocol=f"135{uuid.uuid4().hex[:10]}",
            message=f"Inutilização série {series} nros {start_number}-{end_number} aprovada (fake)",
        )

    async def register_company(
        self,
        cnpj: str,
        company_data: Dict[str, Any],
        api_token: str,
        environment: str,
    ) -> ProviderResponse:
        return ProviderResponse(success=True, data={"cnpj": cnpj, "status": "registered"})
