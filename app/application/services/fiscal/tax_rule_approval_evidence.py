"""Resolve, canonicalize and verify accountant-approved homologation XML."""
from __future__ import annotations

import io
import hashlib
from xml.etree import ElementTree as ET

from domain.fiscal import TaxRuleApproval
from domain.shared import BusinessRuleException


class TaxRuleApprovalArtifactError(BusinessRuleException):
    """The approval artifact is missing, malformed or has lost integrity."""


class TaxRuleApprovalEvidenceService:
    """Stores an immutable approval copy and verifies it by canonical SHA-256."""

    def __init__(self, storage):
        self.storage = storage

    async def capture_approval(
        self,
        *,
        rule_id,
        market_id,
        accountant_user_id,
        source_storage_key: str,
    ) -> TaxRuleApproval:
        source_key = self._validate_market_key(source_storage_key, market_id)
        content = await self.storage.load(source_key)
        if content is None:
            raise TaxRuleApprovalArtifactError("O XML homologado informado não foi encontrado.")
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
    def _validate_market_key(storage_key: str, market_id) -> str:
        key = storage_key.strip()
        prefix = f"fiscal/homologacao/{market_id}/"
        if not key.startswith(prefix):
            raise TaxRuleApprovalArtifactError("A evidência XML não pertence à loja em homologação.")
        return key
