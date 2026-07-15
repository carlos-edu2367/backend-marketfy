"""Resolve, canonicalize and verify accountant-approved homologation XML."""
from __future__ import annotations

import io
import hashlib
import uuid
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from domain.fiscal import TaxRuleApproval
from domain.shared import BusinessRuleException


class TaxRuleApprovalArtifactError(BusinessRuleException):
    """The approval artifact is missing, malformed or has lost integrity."""


@dataclass(frozen=True)
class HomologationFiscalArtifact:
    """Minimal provenance record for an internally issued homologation XML."""

    market_id: uuid.UUID
    document_id: uuid.UUID
    document_type: str
    environment: str
    status: str
    access_key: str | None
    protocol: str | None
    artifact_type: str
    storage_key: str
    sha256: str | None


class TaxRuleApprovalEvidenceService:
    """Stores an immutable approval copy and verifies it by canonical SHA-256."""

    def __init__(self, storage, artifact_repository):
        self.storage = storage
        self.artifact_repository = artifact_repository

    async def capture_approval(
        self,
        *,
        rule_id,
        market_id,
        accountant_user_id,
        source_storage_key: str,
    ) -> TaxRuleApproval:
        source_key, document_id = self._normalize_source_key(source_storage_key, market_id)
        artifact = await self.artifact_repository.get_by_storage_key(source_key)
        self._validate_artifact(artifact, market_id, document_id, source_key)
        content = await self.storage.load(source_key)
        if content is None:
            raise TaxRuleApprovalArtifactError("O XML homologado informado não foi encontrado.")
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise TaxRuleApprovalArtifactError("O XML homologado diverge do hash do artefato fiscal.")
        canonical_xml = self._canonicalize(content)
        content_hash = hashlib.sha256(canonical_xml).hexdigest()
        immutable_key = self._immutable_key(market_id, rule_id, content_hash)

        if await self.storage.exists(immutable_key):
            existing = await self.storage.load(immutable_key)
            if existing is None or self._canonicalize(existing) != canonical_xml:
                raise TaxRuleApprovalArtifactError(
                    "O XML homologado persistido diverge da evidência de aprovação."
                )
        else:
            await self.storage.save(immutable_key, canonical_xml)

        return TaxRuleApproval.from_verified_artifact(
            rule_id=rule_id,
            accountant_user_id=accountant_user_id,
            homologation_xml_storage_key=immutable_key,
            canonical_xml=canonical_xml,
        )

    async def load_verified_xml(self, approval: TaxRuleApproval) -> bytes:
        content = await self.storage.load(approval.homologation_xml_storage_key)
        if content is None:
            raise TaxRuleApprovalArtifactError("O XML homologado aprovado não foi encontrado.")
        canonical_xml = self._canonicalize(content)
        if hashlib.sha256(canonical_xml).hexdigest() != approval.homologation_xml_sha256:
            raise TaxRuleApprovalArtifactError(
                "O XML homologado persistido diverge do hash aprovado."
            )
        return canonical_xml

    @staticmethod
    def _canonicalize(content: bytes) -> bytes:
        try:
            ET.fromstring(content)
            return ET.canonicalize(from_file=io.BytesIO(content), with_comments=False).encode("utf-8")
        except (ET.ParseError, UnicodeDecodeError, ValueError, TypeError) as exc:
            raise TaxRuleApprovalArtifactError("O artefato informado não é um XML válido.") from exc

    @staticmethod
    def _immutable_key(market_id, rule_id, content_hash: str) -> str:
        """Content-address the copy so racing attempts cannot overwrite evidence."""
        return f"fiscal/homologacao/{market_id}/tax_rule_approvals/{rule_id}/{content_hash}.xml"

    @staticmethod
    def _normalize_source_key(storage_key: str, market_id) -> tuple[str, uuid.UUID]:
        key = storage_key.strip()
        if storage_key != key or not key or key.startswith("/") or "\\" in key:
            raise TaxRuleApprovalArtifactError("A chave de artefato inválida não pode ser usada como evidência.")
        parts = key.split("/")
        if (
            len(parts) != 5
            or any(not part or part in {".", ".."} for part in parts)
            or parts[0] != "fiscal"
            or parts[1] != "homologacao"
            or parts[2] != str(market_id)
            or parts[4] != "xml_authorized.xml"
        ):
            raise TaxRuleApprovalArtifactError("A chave de artefato inválida não pode ser usada como evidência.")
        try:
            document_id = uuid.UUID(parts[3])
        except ValueError as exc:
            raise TaxRuleApprovalArtifactError("A chave de artefato inválida não pode ser usada como evidência.") from exc
        return "/".join(parts), document_id

    @staticmethod
    def _validate_artifact(artifact, market_id, document_id, source_key: str) -> None:
        if artifact is None:
            raise TaxRuleApprovalArtifactError("A evidência não é um artefato fiscal interno autorizado.")
        if (
            artifact.market_id != market_id
            or artifact.document_id != document_id
            or artifact.storage_key != source_key
            or artifact.document_type != "nfce"
            or artifact.environment != "homologacao"
            or artifact.status != "authorized"
            or not artifact.access_key
            or not artifact.protocol
            or artifact.artifact_type != "xml_authorized"
            or not artifact.sha256
        ):
            raise TaxRuleApprovalArtifactError("A evidência não é um artefato fiscal interno autorizado.")
