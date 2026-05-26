"""
FiscalProviderGateway — contrato interno do Marketfy para emissão fiscal.
Isola qualquer provider externo (Focus NFe, PlugNotas, etc.) atrás de um
contrato próprio. O sistema nunca deve "virar Focus"; o Focus é apenas um
adaptador desta interface.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any


class EmitResultStatus(Enum):
    AUTHORIZED = "authorized"
    REJECTED = "rejected"           # Rejeição SEFAZ (sem retry útil sem correção)
    PROVIDER_ERROR = "provider_error"    # Erro recuperável no provider
    SEFAZ_UNAVAILABLE = "sefaz_unavailable"
    TIMEOUT = "timeout"
    DUPLICATE_REF = "duplicate_ref"     # Ref já usada — precisa reconciliar
    UNKNOWN = "unknown"


@dataclass
class ProviderEmitResult:
    """Resultado normalizado de uma emissão fiscal."""
    status: EmitResultStatus
    provider_ref: str

    # Dados de sucesso
    access_key: Optional[str] = None
    protocol: Optional[str] = None
    number: Optional[int] = None
    series: Optional[int] = None
    sefaz_status_code: Optional[str] = None
    sefaz_message: Optional[str] = None
    provider_status: Optional[str] = None

    # Dados de erro
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    http_status: Optional[int] = None
    is_recoverable: bool = True

    # Metadados do provider
    provider_request_id: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = field(default=None, repr=False)

    @property
    def is_authorized(self) -> bool:
        return self.status == EmitResultStatus.AUTHORIZED

    @property
    def is_rejected(self) -> bool:
        return self.status == EmitResultStatus.REJECTED

    @property
    def needs_reconciliation(self) -> bool:
        return self.status in (EmitResultStatus.TIMEOUT, EmitResultStatus.UNKNOWN,
                               EmitResultStatus.DUPLICATE_REF)


@dataclass
class ProviderConsultResult:
    """Resultado de consulta de status de documento pelo provider_ref."""
    found: bool
    provider_ref: str
    status: Optional[EmitResultStatus] = None
    access_key: Optional[str] = None
    protocol: Optional[str] = None
    number: Optional[int] = None
    series: Optional[int] = None
    sefaz_status_code: Optional[str] = None
    sefaz_message: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class ProviderCancelResult:
    """Resultado de cancelamento de NFC-e."""
    success: bool
    protocol: Optional[str] = None
    error_message: Optional[str] = None
    is_recoverable: bool = True


@dataclass
class ProviderInutilizacaoResult:
    """Resultado de inutilização de intervalo de numeração."""
    success: bool
    protocol: Optional[str] = None
    message: Optional[str] = None
    error_message: Optional[str] = None
    is_recoverable: bool = True


@dataclass
class ProviderResponse:
    """Envelope genérico de resposta do provider."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


def build_provider_ref(market_id: uuid.UUID, sale_id: uuid.UUID) -> str:
    """Ref determinístico: marketfy-{market_id}-{sale_id}."""
    return f"marketfy-{market_id}-{sale_id}"


class FiscalProviderGateway(ABC):
    """
    Contrato interno para qualquer provider de emissão fiscal.

    Toda a lógica de negócio fiscal do Marketfy deve depender desta interface,
    nunca diretamente de um provider externo.
    """

    @abstractmethod
    async def emit_nfce(
        self,
        provider_ref: str,
        payload: Dict[str, Any],
        api_token: str,
        environment: str,
    ) -> ProviderEmitResult:
        """
        Emite uma NFC-e.

        Args:
            provider_ref: Referência única determinística (marketfy-{market_id}-{sale_id}).
            payload: Dados fiscais da venda no formato do Marketfy.
            api_token: Token de autenticação para este market/provider.
            environment: "homologacao" ou "producao".
        """
        pass

    @abstractmethod
    async def consult_nfce(
        self,
        provider_ref: str,
        api_token: str,
        environment: str,
    ) -> ProviderConsultResult:
        """Consulta status de uma NFC-e pelo provider_ref."""
        pass

    @abstractmethod
    async def cancel_nfce(
        self,
        provider_ref: str,
        access_key: str,
        justification: str,
        api_token: str,
        environment: str,
    ) -> ProviderCancelResult:
        """Cancela uma NFC-e autorizada dentro do prazo legal."""
        pass

    @abstractmethod
    async def download_xml(
        self,
        provider_ref: str,
        api_token: str,
        environment: str,
    ) -> Optional[bytes]:
        """Baixa o XML autorizado da NFC-e."""
        pass

    @abstractmethod
    async def download_pdf(
        self,
        provider_ref: str,
        api_token: str,
        environment: str,
    ) -> Optional[bytes]:
        """Baixa o DANFE PDF da NFC-e."""
        pass

    @abstractmethod
    async def inutilize_numbering(
        self,
        cnpj: str,
        series: int,
        start_number: int,
        end_number: int,
        justification: str,
        api_token: str,
        environment: str,
    ) -> "ProviderInutilizacaoResult":
        """
        Inutiliza intervalo de numeração NFC-e/NF-e junto à SEFAZ.

        Usado quando há gaps de numeração que não serão preenchidos
        (ex: NFC-es que falharam e não podem ser reemitidas).

        Args:
            cnpj: CNPJ do emitente (somente dígitos).
            series: Série da NFC-e (ex: 1).
            start_number: Número inicial do intervalo.
            end_number: Número final (igual ao inicial para inutilizar 1 número).
            justification: Motivo da inutilização (mínimo 15 chars).
            api_token: Token de autenticação.
            environment: "homologacao" ou "producao".
        """
        pass

    @abstractmethod
    async def register_company(
        self,
        cnpj: str,
        company_data: Dict[str, Any],
        api_token: str,
        environment: str,
    ) -> ProviderResponse:
        """Cadastra ou atualiza empresa no provider (necessário antes da 1ª emissão)."""
        pass

    def build_provider_ref(self, market_id: uuid.UUID, sale_id: uuid.UUID) -> str:
        """Ref determinístico: marketfy-{market_id}-{sale_id}."""
        return f"marketfy-{market_id}-{sale_id}"
