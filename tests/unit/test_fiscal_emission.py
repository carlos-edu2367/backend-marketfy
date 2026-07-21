"""
PR5 / PR7 — Testes do FiscalEmissionService.

Cobre:
- Fiscal desabilitado retorna NOT_REQUESTED
- Config incompleta retorna MANUAL_ACTION_REQUIRED
- Venda não encontrada lança BusinessRuleException
- Multi-tenant: venda de outro mercado rejeita
- Idempotência: chamada duplicada retorna doc existente
- Pré-validação falhando cria documento REJECTED (não chama queue)
- Cota esgotada retorna OFFLINE_RECEIPT_ISSUED
- Emissão bem sucedida enfileira job e retorna QUEUED
- ARQ indisponível: emissão prossegue sem enfileiramento (caixa não trava)
"""
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.fiscal.fiscal_emission_service import FiscalEmissionService
from application.services.fiscal.fiscal_pre_validator import FiscalPreValidator
from domain.fiscal import (
    FiscalDocument,
    FiscalDocumentStatus,
    FiscalEnvironment,
    FiscalTenantConfig,
    TaxRegime,
    ValidationStatus,
)
from domain.shared import BusinessRuleException


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------

def _tax_snapshot(amount: Decimal = Decimal("10.00")) -> dict:
    return {
        "rule_id": str(uuid.uuid4()), "rule_version": 1,
        "ncm": "22021000", "cest": None, "cfop": "5102", "origin": "0",
        "icms": {"group": "ICMSSN102", "cst": None, "csosn": "102", "own_base": amount, "reduction_rate": Decimal("0"), "own_rate": Decimal("0"), "own_amount": Decimal("0"), "st_base": Decimal("0"), "st_mva_rate": Decimal("0"), "st_rate": Decimal("0"), "st_amount": Decimal("0"), "fcp_rate": Decimal("0"), "fcp_amount": Decimal("0")},
        "pis": {"group": "PIS07", "cst": "07", "base": amount, "rate": Decimal("0"), "amount": Decimal("0")},
        "cofins": {"group": "COFINS07", "cst": "07", "base": amount, "rate": Decimal("0"), "amount": Decimal("0")},
    }

@dataclass
class FakeItem:
    product_id: uuid.UUID = field(default_factory=uuid.uuid4)
    product_name: str = "Produto"
    ncm_snapshot: str = "22021000"
    unit_price: Decimal = Decimal("10.00")
    quantity: Decimal = Decimal("1")
    total: Decimal = Decimal("10.00")
    tax_rule_version_snapshot: int = 1
    fiscal_tax_snapshot: dict = field(default_factory=_tax_snapshot)


@dataclass
class FakePayment:
    method: str = "pix"
    amount: Decimal = Decimal("10.00")


@dataclass
class FakeSale:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    market_id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: str = "completed"
    items: List[FakeItem] = field(default_factory=lambda: [FakeItem()])
    payments: List[FakePayment] = field(default_factory=lambda: [FakePayment()])
    customer_cpf: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)


def _make_config(enabled=True, ready=True) -> FiscalTenantConfig:
    future = datetime(2030, 1, 1)
    return FiscalTenantConfig(
        market_id=uuid.uuid4(),
        provider="focus_nfe",
        enabled=enabled,
        environment=FiscalEnvironment.HOMOLOGATION,
        certificate_storage_key="fiscal/cert" if ready else None,
        certificate_password_ciphertext="cipher" if ready else None,
        certificate_valid_until=future if ready else None,
        csc_id_ciphertext="cipher_cscid" if ready else None,
        csc_token_ciphertext="cipher_csctoken" if ready else None,
        cnpj="12345678000195",
        legal_name="Empresa",
        tax_regime=TaxRegime.SIMPLES_NACIONAL,
        address_json={},
        nfce_series=1,
        validation_status=ValidationStatus.VALIDATED,
    )


def _make_service(
    cfg=None,
    sale=None,
    existing_doc=None,
    quota_ok=True,
    arq_pool=None,
) -> FiscalEmissionService:
    from domain.fiscal import FiscalQuotaExceededError, QuotaReserveResult

    config_repo = AsyncMock()
    config_repo.get_by_market.return_value = cfg

    doc_repo = AsyncMock()
    doc_repo.get_by_sale.return_value = existing_doc
    doc_repo.get_by_id.return_value = existing_doc
    saved_docs = []

    async def save_doc(doc):
        saved_docs.append(doc)
        return doc
    doc_repo.save.side_effect = save_doc

    event_repo = AsyncMock()
    event_repo.append.return_value = None

    quota_svc = AsyncMock()
    if quota_ok:
        quota_svc.check_and_reserve.return_value = QuotaReserveResult(consuming_addon=False)
    else:
        quota_svc.check_and_reserve.side_effect = FiscalQuotaExceededError(
            used=200, included_limit=200, addon_limit=0
        )
    quota_svc.release.return_value = None

    plan_access_svc = AsyncMock()
    plan_access_svc.get_fiscal_monthly_limit.return_value = 200

    sale_repo = AsyncMock()
    sale_repo.get_by_id.return_value = sale

    market_repo = AsyncMock()

    return FiscalEmissionService(
        config_repo=config_repo,
        doc_repo=doc_repo,
        event_repo=event_repo,
        quota_service=quota_svc,
        pre_validator=FiscalPreValidator(),
        sale_repo=sale_repo,
        market_repo=market_repo,
        arq_pool=arq_pool,
        plan_access_service=plan_access_svc,
    )


# ---------------------------------------------------------------------------
# Fiscal desabilitado
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fiscal_disabled_returns_not_requested():
    svc = _make_service(cfg=None)
    result = await svc.request_emission(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert result["status"] == FiscalDocumentStatus.NOT_REQUESTED.value
    assert result["fiscal_document_id"] is None


@pytest.mark.asyncio
async def test_fiscal_config_disabled_returns_not_requested():
    cfg = _make_config(enabled=False)
    svc = _make_service(cfg=cfg)
    result = await svc.request_emission(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert result["status"] == FiscalDocumentStatus.NOT_REQUESTED.value


# ---------------------------------------------------------------------------
# Config incompleta
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_incomplete_config_returns_manual_action():
    cfg = _make_config(enabled=True, ready=False)
    svc = _make_service(cfg=cfg)
    result = await svc.request_emission(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert result["status"] == FiscalDocumentStatus.MANUAL_ACTION_REQUIRED.value


# ---------------------------------------------------------------------------
# Venda não encontrada
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sale_not_found_raises_business_rule():
    cfg = _make_config(enabled=True)
    svc = _make_service(cfg=cfg, sale=None)
    with pytest.raises(BusinessRuleException, match="não encontrada"):
        await svc.request_emission(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())


# ---------------------------------------------------------------------------
# Multi-tenant: venda de outro mercado
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sale_from_different_market_raises():
    market_id = uuid.uuid4()
    sale = FakeSale(market_id=uuid.uuid4())  # market_id diferente
    cfg = _make_config()
    svc = _make_service(cfg=cfg, sale=sale)
    with pytest.raises(BusinessRuleException):
        await svc.request_emission(market_id, sale.id, uuid.uuid4())


# ---------------------------------------------------------------------------
# Idempotência
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idempotency_returns_existing_terminal_doc():
    market_id = uuid.uuid4()
    sale_id = uuid.uuid4()
    existing_doc = FiscalDocument(
        market_id=market_id,
        sale_id=sale_id,
        status=FiscalDocumentStatus.AUTHORIZED,
        provider_ref=f"marketfy-{market_id}-{sale_id}",
        access_key="ACCESS_KEY",
        protocol="PROT",
        number=42,
        series=1,
        authorized_at=datetime.utcnow(),
    )
    cfg = _make_config()
    sale = FakeSale(market_id=market_id, id=sale_id)
    svc = _make_service(cfg=cfg, sale=sale, existing_doc=existing_doc)
    result = await svc.request_emission(market_id, sale_id, uuid.uuid4())
    assert result["status"] == FiscalDocumentStatus.AUTHORIZED.value
    assert result["fiscal_document_id"] == str(existing_doc.id)


@pytest.mark.asyncio
async def test_idempotency_returns_queued_doc_with_re_enqueueing():
    market_id = uuid.uuid4()
    sale_id = uuid.uuid4()
    existing_doc = FiscalDocument(
        market_id=market_id,
        sale_id=sale_id,
        status=FiscalDocumentStatus.QUEUED,
    )
    cfg = _make_config()
    sale = FakeSale(market_id=market_id, id=sale_id)
    arq_pool = AsyncMock()
    svc = _make_service(cfg=cfg, sale=sale, existing_doc=existing_doc, arq_pool=arq_pool)
    result = await svc.request_emission(market_id, sale_id, uuid.uuid4())
    assert result["status"] == FiscalDocumentStatus.QUEUED.value
    arq_pool.enqueue_job.assert_called_once()


@pytest.mark.asyncio
async def test_idempotency_requeues_processing_doc_to_recover_missing_worker_job():
    market_id = uuid.uuid4()
    sale_id = uuid.uuid4()
    existing_doc = FiscalDocument(
        market_id=market_id,
        sale_id=sale_id,
        status=FiscalDocumentStatus.PROCESSING,
    )
    cfg = _make_config()
    sale = FakeSale(market_id=market_id, id=sale_id)
    arq_pool = AsyncMock()
    svc = _make_service(cfg=cfg, sale=sale, existing_doc=existing_doc, arq_pool=arq_pool)
    result = await svc.request_emission(market_id, sale_id, uuid.uuid4())
    assert result["status"] == FiscalDocumentStatus.QUEUED.value
    arq_pool.enqueue_job.assert_called_once()


@pytest.mark.asyncio
async def test_enqueue_uses_deterministic_job_id_for_fiscal_document():
    market_id = uuid.uuid4()
    sale = FakeSale(market_id=market_id)
    cfg = _make_config()
    arq_pool = AsyncMock()
    svc = _make_service(cfg=cfg, sale=sale, quota_ok=True, arq_pool=arq_pool)
    result = await svc.request_emission(market_id, sale.id, uuid.uuid4())
    doc_id = result["fiscal_document_id"]
    arq_pool.enqueue_job.assert_called_once()
    assert arq_pool.enqueue_job.call_args.kwargs["_job_id"] == f"emit_nfce:{doc_id}"


@pytest.mark.asyncio
async def test_enqueue_logs_success_with_job_metadata(monkeypatch):
    from application.services.fiscal import fiscal_emission_service as emission_module

    logger_mock = MagicMock()
    monkeypatch.setattr(emission_module, "logger", logger_mock)
    arq_pool = AsyncMock()
    arq_pool.enqueue_job.return_value = type("Job", (), {"job_id": "job-123"})()
    svc = _make_service(arq_pool=arq_pool)
    doc_id = uuid.uuid4()
    market_id = uuid.uuid4()
    owner_id = uuid.uuid4()

    await svc._enqueue_emit(doc_id, market_id, owner_id)

    logger_mock.info.assert_any_call(
        "job enfileirado com sucesso",
        extra={"extra_data": {
            "doc_id": str(doc_id),
            "market_id": str(market_id),
            "owner_id": str(owner_id),
            "queue_name": "fiscal:high",
            "job_id": "job-123",
            "arq_job_id": f"emit_nfce:{doc_id}",
        }},
    )


@pytest.mark.asyncio
async def test_enqueue_logs_problem_when_job_is_not_created(monkeypatch):
    from application.services.fiscal import fiscal_emission_service as emission_module

    logger_mock = MagicMock()
    monkeypatch.setattr(emission_module, "logger", logger_mock)
    arq_pool = AsyncMock()
    arq_pool.enqueue_job.return_value = None
    svc = _make_service(arq_pool=arq_pool)
    doc_id = uuid.uuid4()
    market_id = uuid.uuid4()
    owner_id = uuid.uuid4()

    await svc._enqueue_emit(doc_id, market_id, owner_id)

    logger_mock.warning.assert_any_call(
        "problemas ao enfileirar job: enfileiramento nao confirmado",
        extra={"extra_data": {
            "doc_id": str(doc_id),
            "market_id": str(market_id),
            "owner_id": str(owner_id),
            "queue_name": "fiscal:high",
            "arq_job_id": f"emit_nfce:{doc_id}",
        }},
    )


# ---------------------------------------------------------------------------
# Pré-validação falha
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pre_validation_failure_creates_rejected_doc():
    market_id = uuid.uuid4()
    sale_id = uuid.uuid4()
    # Snapshot fiscal ausente deve bloquear a emissão antes da fila.

    @dataclass
    class SaleNoNcm:
        id: uuid.UUID = field(default_factory=uuid.uuid4)
        market_id: uuid.UUID = field(default_factory=uuid.uuid4)
        status: str = "completed"
        items: List = field(default_factory=lambda: [
            type("I", (), {
                "product_id": uuid.uuid4(), "product_name": "X",
                "ncm_snapshot": None, "unit_price": Decimal("10"), "quantity": Decimal("1"),
                "total": Decimal("10"), "tax_rule_version_snapshot": None,
                "fiscal_tax_snapshot": None,
            })()
        ])
        payments: List = field(default_factory=lambda: [
            type("P", (), {"method": "pix", "amount": Decimal("10")})()
        ])
        customer_cpf: Optional[str] = None
        created_at: datetime = field(default_factory=datetime.utcnow)

    sale = SaleNoNcm(market_id=market_id)
    sale.id = sale_id
    cfg = _make_config(enabled=True)
    cfg.default_ncm = None  # Sem NCM default

    doc_repo = AsyncMock()
    doc_repo.get_by_sale.return_value = None
    saved_docs = []
    async def save_doc(doc): saved_docs.append(doc); return doc
    doc_repo.save.side_effect = save_doc

    config_repo = AsyncMock()
    config_repo.get_by_market.return_value = cfg
    sale_repo = AsyncMock()
    sale_repo.get_by_id.return_value = sale

    svc = FiscalEmissionService(
        config_repo=config_repo,
        doc_repo=doc_repo,
        event_repo=AsyncMock(),
        quota_service=AsyncMock(),
        pre_validator=FiscalPreValidator(),
        sale_repo=sale_repo,
        market_repo=AsyncMock(),
        arq_pool=None,
    )

    result = await svc.request_emission(market_id, sale_id, uuid.uuid4())
    assert result["status"] == FiscalDocumentStatus.REJECTED.value
    assert saved_docs[-1].status == FiscalDocumentStatus.REJECTED


@pytest.mark.asyncio
async def test_off_rollout_uses_legacy_validation_without_tax_snapshot():
    """Existing markets in off keep emitting from legacy item/config data."""
    market_id = uuid.uuid4()
    sale = FakeSale(market_id=market_id)
    sale.items[0].fiscal_tax_snapshot = None
    sale.items[0].tax_rule_version_snapshot = None
    cfg = _make_config(enabled=True)
    cfg.default_ncm = "22021000"
    cfg.default_cfop = "5102"
    cfg.default_csosn = "102"

    arq_pool = AsyncMock()
    svc = _make_service(cfg=cfg, sale=sale, arq_pool=arq_pool)

    result = await svc.request_emission(market_id, sale.id, uuid.uuid4())

    assert result["status"] == FiscalDocumentStatus.QUEUED.value
    arq_pool.enqueue_job.assert_called_once()


# ---------------------------------------------------------------------------
# Cota esgotada
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quota_exceeded_creates_offline_receipt():
    market_id = uuid.uuid4()
    sale = FakeSale(market_id=market_id)
    cfg = _make_config()
    svc = _make_service(cfg=cfg, sale=sale, quota_ok=False)
    result = await svc.request_emission(market_id, sale.id, uuid.uuid4())
    assert result["status"] == FiscalDocumentStatus.OFFLINE_RECEIPT_ISSUED.value


# ---------------------------------------------------------------------------
# Emissão bem sucedida
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_successful_emission_enqueues_and_returns_queued():
    market_id = uuid.uuid4()
    sale = FakeSale(market_id=market_id)
    cfg = _make_config()
    arq_pool = AsyncMock()
    svc = _make_service(cfg=cfg, sale=sale, quota_ok=True, arq_pool=arq_pool)
    result = await svc.request_emission(market_id, sale.id, uuid.uuid4())
    assert result["status"] == FiscalDocumentStatus.QUEUED.value
    arq_pool.enqueue_job.assert_called_once()


@pytest.mark.asyncio
async def test_emission_result_contains_provider_ref():
    market_id = uuid.uuid4()
    sale = FakeSale(market_id=market_id)
    cfg = _make_config()
    svc = _make_service(cfg=cfg, sale=sale, quota_ok=True)
    result = await svc.request_emission(market_id, sale.id, uuid.uuid4())
    assert result["provider_ref"] is not None
    assert str(market_id) in result["provider_ref"]


@pytest.mark.asyncio
async def test_emission_without_arq_does_not_crash():
    """Caixa NÃO pode travar — emissão deve prosseguir sem ARQ."""
    market_id = uuid.uuid4()
    sale = FakeSale(market_id=market_id)
    cfg = _make_config()
    # arq_pool=None simula ARQ indisponível
    svc = _make_service(cfg=cfg, sale=sale, quota_ok=True, arq_pool=None)
    result = await svc.request_emission(market_id, sale.id, uuid.uuid4())
    # Deve retornar queued (ou algum status não-erro) mesmo sem ARQ
    assert result["status"] in (
        FiscalDocumentStatus.QUEUED.value,
        FiscalDocumentStatus.PROCESSING.value,
    )


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_status_returns_none_when_no_doc():
    svc = _make_service()
    result = await svc.get_status(uuid.uuid4(), uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_get_status_returns_doc_response():
    market_id = uuid.uuid4()
    sale_id = uuid.uuid4()
    doc = FiscalDocument(
        market_id=market_id, sale_id=sale_id,
        status=FiscalDocumentStatus.AUTHORIZED,
        access_key="KEY",
    )
    svc = _make_service(existing_doc=doc)
    result = await svc.get_status(market_id, sale_id)
    assert result is not None
    assert result["status"] == FiscalDocumentStatus.AUTHORIZED.value
    assert result["access_key"] == "KEY"
