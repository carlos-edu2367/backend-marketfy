"""Fiscal documents retain outbound request evidence before queueing."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from domain.fiscal import FiscalDocument, FiscalDocumentStatus


@pytest.mark.asyncio
async def test_block_document_without_persisted_payload_requires_manual_action(monkeypatch) -> None:
    """New block-mode documents must never be rebuilt from mutable defaults."""
    from application.jobs import fiscal_jobs
    from domain.fiscal import FiscalRuleEnforcement

    doc = FiscalDocument(market_id=uuid.uuid4(), sale_id=uuid.uuid4())
    doc.request_payload_json = None
    doc_repo = AsyncMock()
    doc_repo.get_by_id.return_value = doc
    doc_repo.save.side_effect = lambda saved: saved

    class Config:
        fiscal_rule_enforcement = FiscalRuleEnforcement.BLOCK

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None

    monkeypatch.setattr(fiscal_jobs, "_fiscal_job_dependencies", lambda: {
        "session_factory": lambda: Session(), "doc_repo": doc_repo, "config": Config(),
    }, raising=False)

    # The worker exposes a small dependency seam so this invariant is testable
    # without a database or provider.
    result = await fiscal_jobs._select_persisted_payload_for_emission(doc, Config(), AsyncMock())

    assert result == {"status": "payload_missing"}
    assert doc.status is FiscalDocumentStatus.MANUAL_ACTION_REQUIRED


@pytest.mark.asyncio
async def test_block_emission_persists_v2_evidence_before_quota_and_enqueue() -> None:
    from application.services.fiscal.fiscal_contract_v2 import canonical_contract_sha256
    from application.services.fiscal.fiscal_emission_service import FiscalEmissionService
    from domain.fiscal import FiscalEnvironment, FiscalRuleEnforcement, QuotaReserveResult

    market_id, sale_id, owner_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sale = SimpleNamespace(id=sale_id, market_id=market_id, items=[], payments=[])
    config = SimpleNamespace(
        enabled=True, provider="neectify_fiscal", environment=FiscalEnvironment.HOMOLOGATION,
        fiscal_rule_enforcement=FiscalRuleEnforcement.BLOCK, neectify_issuer_id="iss_1",
        is_ready_for_emission=lambda: True,
    )
    payload = {
        "contract_version": "marketfy.fiscal-tax-snapshot.v2", "external_id": str(sale_id),
        "items": [], "totals": {}, "snapshot_sha256": "will-be-replaced",
    }
    payload["snapshot_sha256"] = canonical_contract_sha256(payload)
    order: list[str] = []
    docs: list[FiscalDocument] = []
    doc_repo = AsyncMock()
    doc_repo.get_by_sale.return_value = None
    async def save(document):
        order.append("save")
        docs.append(document)
        return document
    doc_repo.save.side_effect = save
    quota = AsyncMock()
    async def reserve(**kwargs):
        order.append("quota")
        return QuotaReserveResult(consuming_addon=False)
    quota.check_and_reserve.side_effect = reserve
    serializer = MagicMock()
    serializer.build.return_value = payload
    service = FiscalEmissionService(
        config_repo=AsyncMock(get_by_market=AsyncMock(return_value=config)),
        doc_repo=doc_repo, event_repo=AsyncMock(), quota_service=quota,
        pre_validator=MagicMock(), sale_repo=AsyncMock(get_by_id=AsyncMock(return_value=sale)),
        market_repo=AsyncMock(), arq_pool=AsyncMock(), contract_serializer=serializer,
    )

    result = await service.request_emission(market_id, sale_id, owner_id)

    assert result["status"] == FiscalDocumentStatus.QUEUED.value
    assert order.index("save") < order.index("quota")
    assert docs[0].request_contract_version == "marketfy.fiscal-tax-snapshot.v2"
    assert docs[0].request_payload_json == payload
    assert docs[0].request_payload_sha256 == canonical_contract_sha256(payload)


@pytest.mark.asyncio
async def test_worker_reuses_exact_persisted_payload_after_defaults_change() -> None:
    """Changing mutable configuration cannot alter a queued v2 request."""
    from application.jobs.fiscal_jobs import _select_persisted_payload_for_emission
    from application.services.fiscal.fiscal_contract_v2 import canonical_contract_sha256
    from application.services.fiscal.snapshot_integrity import canonical_json
    from domain.fiscal import FiscalRuleEnforcement

    payload = {
        "contract_version": "marketfy.fiscal-tax-snapshot.v2", "external_id": "sale-1",
        "items": [{"cfop": "5405", "tax": {"icms": {"group": "ICMSSN500"}}}],
    }
    payload["snapshot_sha256"] = canonical_contract_sha256(payload)
    doc = FiscalDocument(market_id=uuid.uuid4(), sale_id=uuid.uuid4())
    doc.request_payload_json = payload
    doc.request_contract_version = payload["contract_version"]
    doc.request_payload_sha256 = canonical_contract_sha256(payload)
    config = SimpleNamespace(
        fiscal_rule_enforcement=FiscalRuleEnforcement.BLOCK, default_cfop="5102"
    )
    before = canonical_json(payload)
    config.default_cfop = "6108"  # a mutable tenant default changed after queueing

    transmitted = await _select_persisted_payload_for_emission(doc, config, AsyncMock())

    assert canonical_json(transmitted) == before
    assert transmitted["snapshot_sha256"] == canonical_contract_sha256(transmitted)


@pytest.mark.asyncio
async def test_block_emission_with_invalid_snapshot_returns_controlled_rejection() -> None:
    """Invalid v2 evidence is a fiscal rejection, never an HTTP 500 path."""
    from application.services.fiscal.fiscal_emission_service import FiscalEmissionService
    from domain.fiscal import FiscalEnvironment, FiscalRuleEnforcement
    from domain.shared import BusinessRuleException

    market_id, sale_id = uuid.uuid4(), uuid.uuid4()
    config = SimpleNamespace(
        enabled=True, provider="neectify_fiscal", environment=FiscalEnvironment.HOMOLOGATION,
        fiscal_rule_enforcement=FiscalRuleEnforcement.BLOCK, neectify_issuer_id="iss_1",
        is_ready_for_emission=lambda: True,
    )
    sale = SimpleNamespace(id=sale_id, market_id=market_id, items=[], payments=[])
    serializer = MagicMock()
    serializer.build.side_effect = BusinessRuleException("sale.fiscal_tax_snapshot_missing; sku=product-1")
    saved: list[FiscalDocument] = []
    doc_repo = AsyncMock(get_by_sale=AsyncMock(return_value=None))
    async def save(doc):
        saved.append(doc)
        return doc
    doc_repo.save.side_effect = save
    quota = AsyncMock()
    service = FiscalEmissionService(
        config_repo=AsyncMock(get_by_market=AsyncMock(return_value=config)),
        doc_repo=doc_repo, event_repo=AsyncMock(), quota_service=quota,
        pre_validator=MagicMock(), sale_repo=AsyncMock(get_by_id=AsyncMock(return_value=sale)),
        market_repo=AsyncMock(), contract_serializer=serializer,
    )

    result = await service.request_emission(market_id, sale_id, uuid.uuid4())

    assert result["status"] == FiscalDocumentStatus.REJECTED.value
    assert saved[-1].sefaz_message == "sale.fiscal_tax_snapshot_missing; sku=product-1"
    quota.check_and_reserve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["marketfy.fiscal-tax-snapshot.v1", "legacy", None])
async def test_block_worker_rejects_any_non_v2_persisted_contract(version: str | None) -> None:
    """Block mode accepts only the production v2 contract at provider boundary."""
    from application.jobs.fiscal_jobs import _select_persisted_payload_for_emission
    from application.services.fiscal.fiscal_contract_v2 import canonical_contract_sha256
    from domain.fiscal import FiscalRuleEnforcement

    payload = {"contract_version": version, "items": []}
    payload["snapshot_sha256"] = canonical_contract_sha256(payload)
    doc = FiscalDocument(market_id=uuid.uuid4(), sale_id=uuid.uuid4())
    doc.request_contract_version = version
    doc.request_payload_json = payload
    doc.request_payload_sha256 = canonical_contract_sha256(payload)
    repo = AsyncMock()
    config = SimpleNamespace(fiscal_rule_enforcement=FiscalRuleEnforcement.BLOCK)

    result = await _select_persisted_payload_for_emission(doc, config, repo)

    assert result == {"status": "payload_invalid"}
    assert doc.status is FiscalDocumentStatus.MANUAL_ACTION_REQUIRED
    repo.save.assert_awaited_once_with(doc)
