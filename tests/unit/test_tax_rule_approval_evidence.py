"""Approval evidence must be real, immutable XML—not an arbitrary URI."""
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.append(str(APP_DIR))

from application.services.fiscal.tax_rule_approval_evidence import (
    TaxRuleApprovalArtifactError,
    TaxRuleApprovalEvidenceService,
)


class FakeStorage:
    def __init__(self, contents: dict[str, bytes]):
        self.contents = contents

    async def load(self, storage_key: str):
        return self.contents.get(storage_key)

    async def exists(self, storage_key: str) -> bool:
        return storage_key in self.contents

    async def save(self, storage_key: str, content: bytes) -> str:
        self.contents[storage_key] = content
        return "unused-by-service"


def _source_key(market_id: uuid.UUID) -> str:
    return f"fiscal/homologacao/{market_id}/source/nfce-st.xml"


@pytest.mark.asyncio
async def test_nonexistent_homologation_reference_is_rejected():
    market_id = uuid.uuid4()
    service = TaxRuleApprovalEvidenceService(FakeStorage({}))

    with pytest.raises(TaxRuleApprovalArtifactError, match="não foi encontrado"):
        await service.capture_approval(
            rule_id=uuid.uuid4(),
            market_id=market_id,
            accountant_user_id=uuid.uuid4(),
            source_storage_key=_source_key(market_id),
        )


@pytest.mark.asyncio
async def test_changed_approved_xml_is_rejected_by_its_persisted_hash():
    market_id, rule_id = uuid.uuid4(), uuid.uuid4()
    source_key = _source_key(market_id)
    storage = FakeStorage({source_key: b"<NFe><infNFe Id='A'/></NFe>"})
    service = TaxRuleApprovalEvidenceService(storage)

    approval = await service.capture_approval(
        rule_id=rule_id,
        market_id=market_id,
        accountant_user_id=uuid.uuid4(),
        source_storage_key=source_key,
    )
    storage.contents[approval.homologation_xml_storage_key] = b"<NFe><infNFe Id='B'/></NFe>"

    with pytest.raises(TaxRuleApprovalArtifactError, match="diverge"):
        await service.load_verified_xml(approval)


@pytest.mark.asyncio
async def test_approved_xml_is_retrievable_and_approval_link_is_immutable():
    market_id, rule_id = uuid.uuid4(), uuid.uuid4()
    source_key = _source_key(market_id)
    storage = FakeStorage({source_key: b"<NFe><infNFe b='2' a='1'/></NFe>"})
    service = TaxRuleApprovalEvidenceService(storage)

    approval = await service.capture_approval(
        rule_id=rule_id,
        market_id=market_id,
        accountant_user_id=uuid.uuid4(),
        source_storage_key=source_key,
    )

    assert await service.load_verified_xml(approval) == b'<NFe><infNFe a="1" b="2"></infNFe></NFe>'
    assert approval.homologation_xml_storage_key.endswith(f"/{approval.homologation_xml_sha256}.xml")
    with pytest.raises(FrozenInstanceError):
        approval.homologation_xml_storage_key = source_key
