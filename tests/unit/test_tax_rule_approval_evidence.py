"""Approval evidence must come from a verified internal NFC-e artifact."""
from __future__ import annotations

import hashlib
import sys
import uuid
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.append(str(APP_DIR))

from application.services.fiscal.tax_rule_approval_evidence import (
    HomologationFiscalArtifact,
    TaxRuleApprovalArtifactError,
    TaxRuleApprovalEvidenceService,
)


XML = b"<NFe><infNFe b='2' a='1'/></NFe>"


class FakeStorage:
    def __init__(self, contents: dict[str, bytes]):
        self.contents = contents

    async def load(self, storage_key: str):
        return self.contents.get(storage_key)

    async def exists(self, storage_key: str) -> bool:
        return storage_key in self.contents

    async def save(self, storage_key: str, content: bytes) -> str:
        self.contents[storage_key] = content
        return hashlib.sha256(content).hexdigest()


class FakeArtifactRepository:
    def __init__(self, artifact: HomologationFiscalArtifact | None):
        self.artifact = artifact

    async def get_by_storage_key(self, storage_key: str):
        if self.artifact and self.artifact.storage_key == storage_key:
            return self.artifact
        return None


def _artifact(*, market_id: uuid.UUID, document_id: uuid.UUID, **overrides) -> HomologationFiscalArtifact:
    storage_key = f"fiscal/homologacao/{market_id}/{document_id}/xml_authorized.xml"
    values = {
        "market_id": market_id,
        "document_id": document_id,
        "document_type": "nfce",
        "environment": "homologacao",
        "status": "authorized",
        "access_key": "52260712345678000123550010000000011000000010",
        "protocol": "152260000000001",
        "artifact_type": "xml_authorized",
        "storage_key": storage_key,
        "sha256": hashlib.sha256(XML).hexdigest(),
    }
    values.update(overrides)
    return HomologationFiscalArtifact(**values)


@pytest.mark.asyncio
async def test_nonexistent_or_unverified_artifact_is_rejected_even_when_xml_is_well_formed():
    market_id, document_id = uuid.uuid4(), uuid.uuid4()
    candidate = _artifact(market_id=market_id, document_id=document_id)
    service = TaxRuleApprovalEvidenceService(
        FakeStorage({candidate.storage_key: b"<NFe/>"}), FakeArtifactRepository(None)
    )

    with pytest.raises(TaxRuleApprovalArtifactError, match="interno autorizado"):
        await service.capture_approval(
            rule_id=uuid.uuid4(), market_id=market_id, accountant_user_id=uuid.uuid4(),
            source_storage_key=candidate.storage_key,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"market_id": uuid.uuid4()},
        {"environment": "producao"},
        {"document_type": "nfe"},
        {"status": "queued"},
        {"access_key": None},
        {"protocol": None},
    ],
)
async def test_wrong_market_environment_type_or_missing_authorization_is_rejected(overrides):
    market_id, document_id = uuid.uuid4(), uuid.uuid4()
    candidate = _artifact(market_id=market_id, document_id=document_id)
    values = {"market_id": market_id, "document_id": document_id}
    values.update(overrides)
    artifact = _artifact(**values)
    service = TaxRuleApprovalEvidenceService(
        FakeStorage({candidate.storage_key: XML}), FakeArtifactRepository(artifact)
    )

    with pytest.raises(TaxRuleApprovalArtifactError):
        await service.capture_approval(
            rule_id=uuid.uuid4(), market_id=market_id, accountant_user_id=uuid.uuid4(),
            source_storage_key=candidate.storage_key,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "storage_key",
    [
        "../outside.xml",
        "/fiscal/homologacao/outside.xml",
        "fiscal/homologacao/{market_id}/./xml_authorized.xml",
        "fiscal/homologacao/{market_id}/../{document_id}/xml_authorized.xml",
        "fiscal/homologacao/{market_id}//xml_authorized.xml",
    ],
)
async def test_traversal_and_noncanonical_segments_are_rejected_before_artifact_lookup(storage_key):
    market_id = uuid.uuid4()
    service = TaxRuleApprovalEvidenceService(FakeStorage({}), FakeArtifactRepository(None))
    candidate = storage_key.format(market_id=market_id, document_id=uuid.uuid4())

    with pytest.raises(TaxRuleApprovalArtifactError, match="chave de artefato inválida"):
        await service.capture_approval(
            rule_id=uuid.uuid4(), market_id=market_id, accountant_user_id=uuid.uuid4(),
            source_storage_key=candidate,
        )


@pytest.mark.asyncio
async def test_valid_internal_authorized_homologation_artifact_is_copied_and_immutable():
    market_id, document_id, rule_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    artifact = _artifact(market_id=market_id, document_id=document_id)
    storage = FakeStorage({artifact.storage_key: XML})
    service = TaxRuleApprovalEvidenceService(storage, FakeArtifactRepository(artifact))

    approval = await service.capture_approval(
        rule_id=rule_id, market_id=market_id, accountant_user_id=uuid.uuid4(),
        source_storage_key=artifact.storage_key,
    )

    assert await service.load_verified_xml(approval) == b'<NFe><infNFe a="1" b="2"></infNFe></NFe>'
    assert approval.homologation_xml_storage_key.endswith(f"/{approval.homologation_xml_sha256}.xml")
    with pytest.raises(FrozenInstanceError):
        approval.homologation_xml_storage_key = artifact.storage_key
