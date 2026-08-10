import hashlib
import uuid
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, FrozenSet, Optional, List
from datetime import date, datetime
from decimal import Decimal
from domain.shared import Entity, BusinessRuleException


def _deep_freeze_json(value: Any) -> Any:
    """Detach and recursively freeze a JSON-compatible value."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _deep_freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze_json(item) for item in value)
    return deepcopy(value)


def _deep_mutable_json(value: Any) -> Any:
    """Create an independent mutable JSON-compatible value."""
    if isinstance(value, Mapping):
        return {key: _deep_mutable_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_deep_mutable_json(item) for item in value]
    return deepcopy(value)

# =============================================================================
# ENUMS
# =============================================================================

class FiscalEnvironment(Enum):
    HOMOLOGATION = "homologacao"
    PRODUCTION = "producao"


class FiscalRuleEnforcement(str, Enum):
    """Per-market rollout gate for accountant-approved product tax rules."""

    OFF = "off"
    WARN = "warn"
    BLOCK = "block"


EnforcementMode = FiscalRuleEnforcement


class IcmsMode(str, Enum):
    NON_TAXED = "non_taxed"
    RETAINED_ST = "retained_st"


class FiscalDocumentStatus(Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    AUTHORIZED = "authorized"
    REJECTED = "rejected"
    PROVIDER_ERROR = "provider_error"
    SEFAZ_UNAVAILABLE = "sefaz_unavailable"
    OFFLINE_RECEIPT_ISSUED = "offline_receipt_issued"
    CONTINGENCY_REQUIRED = "contingency_required"
    MANUAL_ACTION_REQUIRED = "manual_action_required"
    CANCELED = "canceled"
    NOT_REQUESTED = "not_requested"


class FiscalAttemptStatus(Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class FiscalAttemptOperation(Enum):
    EMIT = "emit"
    CONSULT = "consult"
    CANCEL = "cancel"
    INUTILIZE = "inutilize"
    DOWNLOAD_XML = "download_xml"
    DOWNLOAD_PDF = "download_pdf"


class FiscalArtifactType(Enum):
    XML_AUTHORIZED = "xml_authorized"
    XML_CANCEL = "xml_cancel"
    DANFE_PDF = "danfe_pdf"


class FiscalEventSource(Enum):
    MARKETFY = "marketfy"
    PROVIDER = "provider"
    WEBHOOK = "webhook"
    WORKER = "worker"
    ADMIN = "admin"


class TaxRegime(Enum):
    SIMPLES_NACIONAL = "simples_nacional"
    LUCRO_PRESUMIDO = "lucro_presumido"
    LUCRO_REAL = "lucro_real"
    MEI = "mei"


class ProductTaxRuleStatus(str, Enum):
    """Lifecycle of an accountant-reviewed product tax rule."""

    DRAFT = "draft"
    HOMOLOGATED = "homologated"
    PUBLISHED = "published"
    RETIRED = "retired"


TaxRuleStatus = ProductTaxRuleStatus


class FiscalRuleError(BusinessRuleException):
    """Structured, transport-neutral fiscal rule validation error."""

    def __init__(
        self,
        code: str,
        message: str,
        items: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.items = items or []

    def details(self) -> dict[str, Any]:
        return {"code": self.code, "items": self.items}


@dataclass(frozen=True)
class TaxRuleApproval:
    """Immutable accountant evidence attached to one published tax rule."""

    rule_id: uuid.UUID
    accountant_user_id: uuid.UUID
    homologation_xml_storage_key: str
    homologation_xml_sha256: str
    approved_at: datetime

    @classmethod
    def from_verified_artifact(
        cls,
        *,
        rule_id: uuid.UUID,
        accountant_user_id: uuid.UUID,
        homologation_xml_storage_key: str,
        canonical_xml: bytes,
    ) -> "TaxRuleApproval":
        storage_key = homologation_xml_storage_key.strip()
        if not storage_key or not canonical_xml:
            raise BusinessRuleException("O artefato XML homologado é obrigatório.")
        return cls(
            rule_id=rule_id,
            accountant_user_id=accountant_user_id,
            homologation_xml_storage_key=storage_key,
            homologation_xml_sha256=hashlib.sha256(canonical_xml).hexdigest(),
            approved_at=datetime.utcnow(),
        )


@dataclass(frozen=True)
class TaxRuleSefazAuthorization:
    """Immutable proof that SEFAZ authorized a homologation XML for one rule."""

    rule_id: uuid.UUID
    accountant_user_id: uuid.UUID
    authorized_xml_storage_key: str
    xml_sha256: str
    access_key: str
    protocol: str
    authorized_at: datetime
    recorded_at: datetime


class NumberingMode(Enum):
    PROVIDER_AUTO = "provider_auto"
    MARKETFY_CONTROLLED = "marketfy_controlled"


class ValidationStatus(Enum):
    NOT_VALIDATED = "not_validated"
    VALIDATED = "validated"
    NEEDS_VALIDATION = "needs_validation"
    FAILED = "failed"


class UsageLedgerEventType(Enum):
    RESERVED = "reserved"
    CONSUMED = "consumed"
    RELEASED = "released"
    ADDON_PURCHASED = "addon_purchased"
    ADJUSTED = "adjusted"


PACKAGE_TYPE_ADMIN_GRANT = "nfce_admin_grant"


class GrantReasonCode(Enum):
    """Categorias de concessão administrativa de créditos NFC-e."""
    COURTESY = "courtesy"
    COMPENSATION = "compensation"
    BONUS = "bonus"
    MIGRATION = "migration"


# Texto exibido ao usuário final no histórico de créditos e na notificação.
# A nota interna do admin (grant_note) nunca aparece aqui.
GRANT_REASON_LABELS: dict[str, str] = {
    GrantReasonCode.COURTESY.value: "Cortesia da equipe Marketfy",
    GrantReasonCode.COMPENSATION.value: "Compensação por indisponibilidade",
    GrantReasonCode.BONUS.value: "Bônus promocional",
    GrantReasonCode.MIGRATION.value: "Créditos de migração de plano",
}


class NotificationSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class NotificationType(Enum):
    FISCAL_LIMIT_80 = "fiscal_limit_80"
    FISCAL_LIMIT_90 = "fiscal_limit_90"
    FISCAL_LIMIT_100 = "fiscal_limit_100"
    FISCAL_LIMIT_BLOCKED = "fiscal_limit_blocked"
    ADDON_PURCHASED = "addon_purchased"
    ADDON_RUNNING_LOW = "addon_running_low"
    CERTIFICATE_EXPIRING = "certificate_expiring"
    CERTIFICATE_EXPIRED = "certificate_expired"
    INVOICE_REJECTED = "invoice_rejected"
    QUEUE_DELAYED = "queue_delayed"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PENDING_DOCUMENTS = "pending_documents"


# Backward-compat alias
class InvoiceStatus(Enum):
    PENDING = "pendente"
    PROCESSING = "processando"
    AUTHORIZED = "autorizada"
    REJECTED = "rejeitada"
    CANCELED = "cancelada"
    ERROR = "erro"


# =============================================================================
# BACKWARD-COMPAT ENTITIES (mantidas para não quebrar testes existentes)
# =============================================================================

@dataclass
class FiscalConfig(Entity):
    """Config legacy — mantida para compatibilidade com repositório antigo."""
    market_id: uuid.UUID
    certificate_path: str
    certificate_password: str
    csc_token: str
    csc_id: str
    environment: FiscalEnvironment = FiscalEnvironment.HOMOLOGATION
    default_ncm: Optional[str] = None
    default_cfop: str = "5102"

    def validate_for_emission(self):
        if not self.certificate_path or not self.certificate_password:
            raise BusinessRuleException("Certificado Digital não configurado.")
        if not self.csc_token or not self.csc_id:
            raise BusinessRuleException("CSC (Token SEFAZ) não configurado.")


@dataclass
class Invoice(Entity):
    """Invoice legacy — mantida para compatibilidade."""
    market_id: uuid.UUID
    sale_id: uuid.UUID
    status: InvoiceStatus = InvoiceStatus.PENDING
    access_key: Optional[str] = None
    protocol: Optional[str] = None
    xml_url: Optional[str] = None
    pdf_url: Optional[str] = None
    error_message: Optional[str] = None
    series: int = 1
    number: Optional[int] = None
    emitted_at: Optional[datetime] = None

    def set_authorized(self, key: str, protocol: str, number: int, xml: str, pdf: str):
        self.status = InvoiceStatus.AUTHORIZED
        self.access_key = key
        self.protocol = protocol
        self.number = number
        self.xml_url = xml
        self.pdf_url = pdf
        self.emitted_at = datetime.utcnow()
        self.update_timestamp()

    def set_error(self, message: str):
        self.status = InvoiceStatus.REJECTED
        self.error_message = message
        self.update_timestamp()


# =============================================================================
# NOVAS ENTIDADES — PR1 em diante
# =============================================================================

@dataclass
class FiscalTenantConfig(Entity):
    """Configuração fiscal completa por mercado, pronta para produção NFC-e."""
    market_id: uuid.UUID
    provider: str = "focus_nfe"
    environment: FiscalEnvironment = FiscalEnvironment.HOMOLOGATION
    enabled: bool = False
    fiscal_rule_enforcement: FiscalRuleEnforcement = FiscalRuleEnforcement.OFF

    # Dados fiscais do estabelecimento
    legal_name: Optional[str] = None
    trade_name: Optional[str] = None
    cnpj: Optional[str] = None
    state_registration: Optional[str] = None
    municipal_registration: Optional[str] = None
    tax_regime: Optional[TaxRegime] = None
    crt: Optional[str] = None        # Código de Regime Tributário SEFAZ
    cnae_primary: Optional[str] = None

    # Endereço
    address_json: Optional[dict] = None

    # Certificado A1
    certificate_storage_key: Optional[str] = None  # Chave no storage privado
    certificate_password_ciphertext: Optional[str] = None
    certificate_fingerprint: Optional[str] = None
    certificate_valid_until: Optional[datetime] = None

    # CSC (Código de Segurança do Contribuinte para NFC-e)
    csc_id_ciphertext: Optional[str] = None
    csc_token_ciphertext: Optional[str] = None

    # Numeração NFC-e
    nfce_series: int = 1
    nfce_next_number: int = 1
    numbering_mode: NumberingMode = NumberingMode.PROVIDER_AUTO
    contingency_series: int = 900
    contingency_next_number: int = 1

    # Tributação padrão
    default_cfop: str = "5102"
    default_ncm: Optional[str] = None
    default_csosn: Optional[str] = "102"  # Simples Nacional padrão
    default_cst: Optional[str] = None

    # Responsável técnico (obrigatório em produção)
    responsavel_tecnico: Optional[dict] = None

    # Status de validação
    validation_status: ValidationStatus = ValidationStatus.NOT_VALIDATED
    last_validated_at: Optional[datetime] = None

    def validate_for_production(self) -> List[str]:
        """Retorna lista de erros. Lista vazia = config válida para produção."""
        errors = []
        if not self.cnpj:
            errors.append("CNPJ obrigatório.")
        if not self.legal_name:
            errors.append("Razão social obrigatória.")
        if not self.certificate_storage_key:
            errors.append("Certificado digital não configurado.")
        if not self.certificate_password_ciphertext:
            errors.append("Senha do certificado não configurada.")
        if self.certificate_valid_until and self.certificate_valid_until < datetime.utcnow():
            errors.append("Certificado digital vencido.")
        if not self.csc_id_ciphertext or not self.csc_token_ciphertext:
            errors.append("CSC não configurado (obrigatório para NFC-e).")
        if not self.tax_regime:
            errors.append("Regime tributário não definido.")
        if not self.address_json:
            errors.append("Endereço completo obrigatório.")
        if not self.nfce_series:
            errors.append("Série NFC-e não definida.")

        # Validações específicas para o Neectify Fiscal
        if self.provider == "neectify_fiscal":
            if self.cnpj:
                from domain.validators import validate_cnpj
                cnpj_digits = self.cnpj.replace(".", "").replace("/", "").replace("-", "")
                if not validate_cnpj(cnpj_digits):
                    errors.append("CNPJ inválido matematicamente.")
            
            if self.address_json:
                import json
                try:
                    address_data = json.loads(self.address_json) if isinstance(self.address_json, str) else self.address_json
                except (ValueError, TypeError):
                    address_data = {}
                
                if address_data:
                    uf = address_data.get("uf") or ""
                    city_code = address_data.get("city_code") or address_data.get("municipality_code") or ""
                    
                    if uf != "GO":
                        errors.append("Neectify Fiscal suporta apenas o estado de Goiás (UF: 'GO').")
                    
                    allowed_municipalities = {"5208707", "5201405", "5209705", "5218003"}
                    if city_code not in allowed_municipalities:
                        errors.append("Município não suportado no MVP SEFAZ-GO para Neectify Fiscal.")

        return errors

    def is_ready_for_emission(self) -> bool:
        return (
            self.enabled
            and bool(self.certificate_storage_key)
            and bool(self.csc_id_ciphertext)
            and bool(self.csc_token_ciphertext)
            and (self.certificate_valid_until is None or self.certificate_valid_until > datetime.utcnow())
        )

    def days_until_cert_expiry(self) -> Optional[int]:
        if not self.certificate_valid_until:
            return None
        delta = self.certificate_valid_until - datetime.utcnow()
        return delta.days


@dataclass
class ProductTaxProfile(Entity):
    """Perfil fiscal de produto — tributação reutilizável por mercado."""
    market_id: uuid.UUID
    name: str

    ncm: Optional[str] = None
    cest: Optional[str] = None
    cfop: str = "5102"
    origin: str = "0"         # 0=Nacional

    # ICMS
    icms_cst: Optional[str] = None    # Regime Normal
    icms_csosn: Optional[str] = "102" # Simples Nacional

    # PIS/COFINS
    pis_cst: str = "07"
    cofins_cst: str = "07"

    # Alíquotas customizadas (JSON)
    aliquotas_json: Optional[dict] = None

    effective_from: Optional[datetime] = None
    active: bool = True


@dataclass
class ProductTaxRule(Entity):
    """Versioned fiscal classification approved for a Marketfy product.

    This entity deliberately does not derive any classification from product
    text, barcode or NCM. A published version is audit evidence and therefore
    cannot be changed in place.
    """

    market_id: uuid.UUID
    name: str
    status: ProductTaxRuleStatus = ProductTaxRuleStatus.DRAFT
    rule_family_id: Optional[uuid.UUID] = None
    supersedes_rule_id: Optional[uuid.UUID] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None

    issuer_regime: Optional[TaxRegime] = None
    destination_uf: Optional[str] = None
    document_model: Optional[str] = None

    ncm: Optional[str] = None
    cest: Optional[str] = None
    origin: Optional[str] = None
    cfop: Optional[str] = None
    cbenef: Optional[str] = None

    icms_group: Optional[str] = None
    icms_cst: Optional[str] = None
    icms_csosn: Optional[str] = None
    icms_mod_bc: Optional[str] = None
    icms_rate: Optional[Decimal] = None
    icms_reduction_rate: Optional[Decimal] = None
    icms_st_mod_bc: Optional[str] = None
    icms_st_mva_rate: Optional[Decimal] = None
    icms_st_rate: Optional[Decimal] = None
    fcp_rate: Optional[Decimal] = None

    pis_cst: Optional[str] = None
    pis_rate: Optional[Decimal] = None
    cofins_cst: Optional[str] = None
    cofins_rate: Optional[Decimal] = None

    tax_parameters: Optional[Mapping[str, Any]] = None
    approval: Optional[Mapping[str, Any]] = None

    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None

    _IMMUTABLE_BUSINESS_FIELDS: ClassVar[FrozenSet[str]] = frozenset({
        "market_id",
        "name",
        "status",
        "rule_family_id",
        "supersedes_rule_id",
        "version",
        "effective_from",
        "effective_to",
        "issuer_regime",
        "destination_uf",
        "document_model",
        "ncm",
        "cest",
        "origin",
        "cfop",
        "cbenef",
        "icms_group",
        "icms_cst",
        "icms_csosn",
        "icms_mod_bc",
        "icms_rate",
        "icms_reduction_rate",
        "icms_st_mod_bc",
        "icms_st_mva_rate",
        "icms_st_rate",
        "fcp_rate",
        "pis_cst",
        "pis_rate",
        "cofins_cst",
        "cofins_rate",
        "tax_parameters",
        "approval",
        "approved_by",
        "approved_at",
    })
    _DEEPLY_IMMUTABLE_FIELDS: ClassVar[FrozenSet[str]] = frozenset({
        "tax_parameters",
        "approval",
    })

    def __setattr__(self, name: str, value) -> None:
        published = self.__dict__.get("_published_version", False)
        if (
            published
            and name in self._IMMUTABLE_BUSINESS_FIELDS
            and name in self.__dict__
            and value != self.__dict__[name]
        ):
            raise BusinessRuleException(
                "Uma regra fiscal publicada é imutável; crie uma nova versão para corrigi-la."
            )

        if published and name in self._DEEPLY_IMMUTABLE_FIELDS:
            value = _deep_freeze_json(value)

        object.__setattr__(self, name, value)
        if name == "status" and self._is_published_status(value):
            for field_name in self._DEEPLY_IMMUTABLE_FIELDS:
                if field_name in self.__dict__:
                    object.__setattr__(
                        self,
                        field_name,
                        _deep_freeze_json(self.__dict__[field_name]),
                    )
            object.__setattr__(self, "_published_version", True)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            object.__setattr__(self, "status", ProductTaxRuleStatus(self.status))
        if isinstance(self.issuer_regime, str):
            object.__setattr__(self, "issuer_regime", TaxRegime(self.issuer_regime))
        if self.version < 1:
            raise BusinessRuleException("A versão da regra fiscal deve ser positiva.")
        if self.rule_family_id is None:
            object.__setattr__(self, "rule_family_id", self.id)
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise BusinessRuleException("O fim da vigência não pode anteceder o início.")

    def rename(self, name: str) -> None:
        self.assert_mutable()
        self.name = name
        self.update_timestamp()

    def create_successor(self, *, effective_from: date) -> "ProductTaxRule":
        """Create an editable next version without modifying approved evidence."""
        if self.status is not ProductTaxRuleStatus.PUBLISHED:
            raise BusinessRuleException("Somente uma regra publicada pode gerar sucessora.")
        return ProductTaxRule(
            market_id=self.market_id,
            name=self.name,
            status=ProductTaxRuleStatus.DRAFT,
            rule_family_id=self.rule_family_id,
            supersedes_rule_id=self.id,
            version=self.version + 1,
            effective_from=effective_from,
            effective_to=self.effective_to,
            issuer_regime=self.issuer_regime,
            destination_uf=self.destination_uf,
            document_model=self.document_model,
            ncm=self.ncm,
            cest=self.cest,
            origin=self.origin,
            cfop=self.cfop,
            cbenef=self.cbenef,
            icms_group=self.icms_group,
            icms_cst=self.icms_cst,
            icms_csosn=self.icms_csosn,
            icms_mod_bc=self.icms_mod_bc,
            icms_rate=self.icms_rate,
            icms_reduction_rate=self.icms_reduction_rate,
            icms_st_mod_bc=self.icms_st_mod_bc,
            icms_st_mva_rate=self.icms_st_mva_rate,
            icms_st_rate=self.icms_st_rate,
            fcp_rate=self.fcp_rate,
            pis_cst=self.pis_cst,
            pis_rate=self.pis_rate,
            cofins_cst=self.cofins_cst,
            cofins_rate=self.cofins_rate,
            tax_parameters=_deep_mutable_json(self.tax_parameters),
            approval=_deep_mutable_json(self.approval),
        )

    def is_effective_on(self, when: date) -> bool:
        """Return whether a dated rule is valid on ``when``, inclusively."""
        if self.effective_from is None or when < self.effective_from:
            return False
        return self.effective_to is None or when <= self.effective_to

    def matches_context(self, *, destination_uf: str, document_model: str) -> bool:
        """Match only a fully explicit v2 context; nullable legacy rows fail closed."""
        if (
            self.issuer_regime is None
            or self.destination_uf is None
            or self.document_model is None
        ):
            return False
        return (
            self.destination_uf.upper() == destination_uf.upper()
            and self.document_model == document_model
        )

    def assert_mutable(self) -> None:
        if self.__dict__.get("_published_version", False):
            raise BusinessRuleException(
                "Uma regra fiscal publicada é imutável; crie uma nova versão para corrigi-la."
            )

    def _ensure_mutable(self) -> None:
        self.assert_mutable()

    @staticmethod
    def _is_published_status(status) -> bool:
        return status is ProductTaxRuleStatus.PUBLISHED or status == ProductTaxRuleStatus.PUBLISHED.value


@dataclass
class FiscalDocument(Entity):
    """Documento fiscal de uma venda — o 'estado fiscal' de um sale_id."""
    market_id: uuid.UUID
    sale_id: uuid.UUID
    document_type: str = "nfce"

    provider: str = "focus_nfe"
    provider_ref: Optional[str] = None     # ref determinístico: marketfy-{market_id}-{sale_id}
    environment: FiscalEnvironment = FiscalEnvironment.HOMOLOGATION

    status: FiscalDocumentStatus = FiscalDocumentStatus.QUEUED
    idempotency_key: Optional[str] = None

    # Dados da nota autorizada
    series: Optional[int] = None
    number: Optional[int] = None
    access_key: Optional[str] = None
    protocol: Optional[str] = None
    sefaz_status_code: Optional[str] = None
    sefaz_message: Optional[str] = None
    provider_status: Optional[str] = None

    # Datas
    issued_at: Optional[datetime] = None
    authorized_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None

    # Contingência/fallback
    contingency_mode: bool = False
    offline_receipt_id: Optional[str] = None

    # Rastreio
    last_attempt_id: Optional[uuid.UUID] = None

    # Evidência imutável do pedido enviada ao provider. É criada antes da fila
    # para que retries nunca reconstruam tributação a partir de dados mutáveis.
    request_contract_version: Optional[str] = None
    request_payload_json: Optional[dict] = None
    request_payload_sha256: Optional[str] = None

    def set_authorized(self, access_key: str, protocol: str, number: int,
                       series: int, sefaz_code: str, sefaz_msg: str):
        self.status = FiscalDocumentStatus.AUTHORIZED
        self.access_key = access_key
        self.protocol = protocol
        self.number = number
        self.series = series
        self.sefaz_status_code = sefaz_code
        self.sefaz_message = sefaz_msg
        self.authorized_at = datetime.utcnow()
        self.update_timestamp()

    def set_rejected(self, sefaz_code: str, sefaz_msg: str):
        self.status = FiscalDocumentStatus.REJECTED
        self.sefaz_status_code = sefaz_code
        self.sefaz_message = sefaz_msg
        self.update_timestamp()

    def set_provider_error(self, message: str):
        self.status = FiscalDocumentStatus.PROVIDER_ERROR
        self.sefaz_message = message
        self.update_timestamp()

    def set_manual_action_required(self, reason: str):
        self.status = FiscalDocumentStatus.MANUAL_ACTION_REQUIRED
        self.sefaz_message = reason
        self.update_timestamp()

    def can_retry(self) -> bool:
        return self.status in (
            FiscalDocumentStatus.QUEUED,
            FiscalDocumentStatus.PROVIDER_ERROR,
            FiscalDocumentStatus.SEFAZ_UNAVAILABLE,
        )

    def is_terminal(self) -> bool:
        return self.status in (
            FiscalDocumentStatus.AUTHORIZED,
            FiscalDocumentStatus.REJECTED,
            FiscalDocumentStatus.CANCELED,
            FiscalDocumentStatus.MANUAL_ACTION_REQUIRED,
            FiscalDocumentStatus.NOT_REQUESTED,
        )


@dataclass
class FiscalAttempt(Entity):
    """Registro de uma chamada ao provider — cada tentativa de emissão."""
    fiscal_document_id: uuid.UUID
    market_id: uuid.UUID
    provider: str
    operation: FiscalAttemptOperation = FiscalAttemptOperation.EMIT
    attempt_number: int = 1

    status: FiscalAttemptStatus = FiscalAttemptStatus.PENDING
    request_hash: Optional[str] = None        # Hash do payload (sem segredos)
    provider_request_id: Optional[str] = None
    http_status: Optional[int] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    next_retry_at: Optional[datetime] = None

    def finish(self, status: FiscalAttemptStatus, http_status: int = None,
               error_code: str = None, error_message: str = None,
               provider_request_id: str = None):
        self.status = status
        self.finished_at = datetime.utcnow()
        if self.started_at:
            self.duration_ms = int((self.finished_at - self.started_at).total_seconds() * 1000)
        self.http_status = http_status
        self.error_code = error_code
        self.error_message = error_message
        self.provider_request_id = provider_request_id
        self.update_timestamp()


@dataclass
class FiscalArtifact(Entity):
    """XML, PDF ou DANFE de um documento fiscal armazenado no storage privado."""
    fiscal_document_id: uuid.UUID
    artifact_type: FiscalArtifactType
    storage_key: str
    sha256: Optional[str] = None
    content_type: str = "application/xml"
    size_bytes: Optional[int] = None


@dataclass
class FiscalEvent(Entity):
    """Trilha de auditoria fiscal detalhada por documento."""
    fiscal_document_id: uuid.UUID
    market_id: uuid.UUID
    event_type: str
    source: FiscalEventSource = FiscalEventSource.MARKETFY
    message: Optional[str] = None
    metadata_json_sanitized: Optional[dict] = None


@dataclass
class FiscalUsageCounter(Entity):
    """Contador mensal de emissões fiscais por owner (owner-scoped)."""
    owner_id: uuid.UUID
    period_yyyymm: str           # Ex: "202506"

    included_limit: int = 0
    addon_limit: int = 0
    reserved_count: int = 0
    used_count: int = 0
    failed_billable_count: int = 0
    released_count: int = 0
    blocked_at: Optional[datetime] = None
    market_id: Optional[uuid.UUID] = None  # NULL = counter owner-level

    def available_quota(self) -> int:
        available_included = max(0, self.included_limit - self.used_count - self.reserved_count)
        return available_included + self.addon_limit

    def usage_percentage(self) -> float:
        total = self.included_limit + self.addon_limit
        if total == 0:
            return 100.0
        consumed = self.used_count + self.reserved_count
        return (consumed / total) * 100

    def is_blocked(self) -> bool:
        return self.blocked_at is not None


@dataclass
class FiscalUsageLedger(Entity):
    """Ledger detalhado de eventos de consumo/reserva."""
    owner_id: uuid.UUID
    period_yyyymm: str
    event_type: UsageLedgerEventType
    market_id: Optional[uuid.UUID] = None   # preservado para rastreio; None em eventos owner-level
    sale_id: Optional[uuid.UUID] = None
    fiscal_document_id: Optional[uuid.UUID] = None
    quantity: int = 1
    reason: Optional[str] = None
    idempotency_key: Optional[str] = None


@dataclass
class FiscalEmissionPackage(Entity):
    """Pacote adicional de emissões comprado pelo owner."""
    owner_id: uuid.UUID
    package_type: str = "nfce_addon"
    quantity: int = 0
    remaining: int = 0
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    billing_subscription_id: Optional[str] = None
    payment_status: str = "pending"
    market_id: Optional[uuid.UUID] = None  # rastreio de onde foi comprado
    package_slug: Optional[str] = None
    bc_job_id: Optional[str] = None
    bc_payment_id: Optional[str] = None
    bc_idempotency_key: Optional[str] = None
    price_gross: Optional[Decimal] = None
    price_net_target: Optional[Decimal] = None
    purchased_at_market_id: Optional[uuid.UUID] = None
    grant_reason_code: Optional[str] = None
    grant_note: Optional[str] = None
    granted_by_id: Optional[uuid.UUID] = None

    def is_valid(self) -> bool:
        now = datetime.utcnow()
        if self.valid_from and now < self.valid_from:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        return self.remaining > 0 and self.payment_status == "paid"


@dataclass
class FiscalNumberInutilizacao(Entity):
    """
    Inutilização de numeração NFC-e/NF-e junto à SEFAZ.

    Necessária quando há gaps de série que não serão preenchidos.
    Cada instância representa um pedido de inutilização de um intervalo.
    """
    market_id: uuid.UUID
    provider: str
    environment: FiscalEnvironment
    cnpj: str
    document_type: str  # "nfce" ou "nfe"
    series: int
    start_number: int
    end_number: int
    justification: str
    requested_by_id: uuid.UUID

    status: str = "pending"          # pending | authorized | failed
    protocol: Optional[str] = None
    provider_message: Optional[str] = None
    error_message: Optional[str] = None
    authorized_at: Optional[datetime] = None
    idempotency_key: Optional[str] = None  # "{cnpj}:{series}:{start}:{end}"

    def mark_authorized(self, protocol: str, message: str = ""):
        self.status = "authorized"
        self.protocol = protocol
        self.provider_message = message
        self.authorized_at = datetime.utcnow()

    def mark_failed(self, error: str):
        self.status = "failed"
        self.error_message = error


@dataclass
class ProviderWebhookEvent(Entity):
    """Idempotência de webhooks de provider fiscal."""
    provider: str
    event_id: str
    provider_ref: Optional[str] = None
    raw_payload_hash: Optional[str] = None
    processing_status: str = "pending"
    processed_at: Optional[datetime] = None


@dataclass(frozen=True)
class EmissionCreditPackage:
    slug: str
    emission_count: int
    price_gross: Decimal
    price_net_target: Decimal


EMISSION_PACKAGES: dict[str, EmissionCreditPackage] = {
    "pack_100": EmissionCreditPackage("pack_100", 100, Decimal("41.99"), Decimal("39.90")),
    "pack_250": EmissionCreditPackage("pack_250", 250, Decimal("73.57"), Decimal("69.90")),
    "pack_500": EmissionCreditPackage("pack_500", 500, Decimal("126.20"), Decimal("119.90")),
}


@dataclass(frozen=True)
class PurchaseInitResult:
    package_id: uuid.UUID
    init_point: str
    package: EmissionCreditPackage
    job_id: Optional[str] = None



@dataclass(frozen=True)
class PackageHistoryItem:
    package_id: uuid.UUID
    package_slug: Optional[str]
    quantity: int
    remaining: int
    payment_status: str
    price_gross: Optional[Decimal]
    price_net_target: Optional[Decimal]
    valid_from: Optional[datetime]
    valid_until: Optional[datetime]
    created_at: Optional[datetime]
    package_type: str = "nfce_addon"
    grant_reason_code: Optional[str] = None

    @classmethod
    def from_package(cls, package: FiscalEmissionPackage) -> "PackageHistoryItem":
        return cls(
            package_id=package.id,
            package_slug=package.package_slug,
            quantity=package.quantity,
            remaining=package.remaining,
            payment_status=package.payment_status,
            price_gross=package.price_gross,
            price_net_target=package.price_net_target,
            valid_from=package.valid_from,
            valid_until=package.valid_until,
            created_at=package.created_at,
            package_type=package.package_type,
            grant_reason_code=package.grant_reason_code,
        )


@dataclass(frozen=True)
class GrantResult:
    """Resultado de uma concessão administrativa.

    created=False indica replay idempotente — o pacote já existia e nenhum
    crédito novo foi emitido.
    """
    package: FiscalEmissionPackage
    created: bool


@dataclass(frozen=True)
class CreditsBalance:
    period: str
    included_limit: int
    addon_limit: int
    used_count: int
    reserved_count: int
    remaining: int
    percentage_used: float
    addon_total: int = 0


@dataclass
class QuotaReserveResult:
    """Resultado de uma reserva de cota bem-sucedida."""
    consuming_addon: bool


@dataclass
class QuotaStatus:
    """Status da cota de emissão do período atual."""
    period: str
    included_limit: int
    addon_limit: int
    used_count: int
    reserved_count: int
    remaining: int
    percentage_used: float
    addon_total: int = 0


class NeectifyOnboardingStep(Enum):
    PENDING = "pending"
    ISSUER_CREATED = "issuer_created"
    CONFIG_SET = "config_set"
    CERT_UPLOADED = "cert_uploaded"
    CERT_ACTIVE = "cert_active"
    READY = "ready"


class FiscalAuthError(Exception):
    """Provider retornou 401/403 — credencial inválida ou sem permissão."""


class FiscalValidationError(Exception):
    """Provider retornou 422 — payload inválido ou regra de negócio violada."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.details = details or {}


class FiscalQuotaExceededError(Exception):
    """Limite mensal de emissões atingido — sem included nem addon disponível."""

    def __init__(self, used: int, included_limit: int, addon_limit: int):
        super().__init__("Limite mensal de emissões atingido.")
        self.used = used
        self.included_limit = included_limit
        self.addon_limit = addon_limit


@dataclass
class FiscalNotification(Entity):
    """Notificação fiscal para owner/manager."""
    owner_id: uuid.UUID
    market_id: Optional[uuid.UUID]
    notification_type: NotificationType
    severity: NotificationSeverity = NotificationSeverity.INFO
    title: str = ""
    message: str = ""
    dedupe_key: Optional[str] = None
    read_at: Optional[datetime] = None
    sent_email_at: Optional[datetime] = None

    def mark_read(self):
        self.read_at = datetime.utcnow()
