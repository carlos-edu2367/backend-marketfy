from __future__ import annotations

from domain.fiscal import FiscalArtifact, FiscalArtifactType, FiscalDocumentStatus
from domain.shared import BusinessRuleException

from application.services.fiscal.nfce_print_parser import parse_nfce_for_print


class NfcePrintService:
    def __init__(self, doc_repo, artifact_repo, storage, provider, api_token: str = ""):
        self.doc_repo = doc_repo
        self.artifact_repo = artifact_repo
        self.storage = storage
        self.provider = provider
        self.api_token = api_token

    async def prepare_sale_print_payload(self, market_id, sale_id) -> dict:
        doc = await self.doc_repo.get_by_sale(market_id, sale_id)
        if not doc:
            raise BusinessRuleException("Documento fiscal nao encontrado para esta venda.")
        if str(doc.market_id) != str(market_id):
            raise BusinessRuleException("Documento fiscal nao pertence a este mercado.")
        if doc.status != FiscalDocumentStatus.AUTHORIZED:
            raise BusinessRuleException(f"NFC-e ainda nao autorizada: {doc.status.value}.")
        if not doc.provider_ref:
            raise BusinessRuleException("Documento fiscal autorizado sem referencia do provedor.")

        xml_bytes = await self._load_or_download_xml(doc)
        payload = parse_nfce_for_print(xml_bytes)
        payload["document"] = {
            "id": str(doc.id),
            "sale_id": str(doc.sale_id),
            "provider_ref": doc.provider_ref,
            "environment": doc.environment.value,
            "status": doc.status.value,
        }
        return payload

    async def _load_or_download_xml(self, doc) -> bytes:
        artifact = await self.artifact_repo.get_by_doc_and_type(
            doc.id,
            FiscalArtifactType.XML_AUTHORIZED.value,
        )
        if artifact:
            content = await self.storage.load(artifact.storage_key)
            if content:
                return content

        xml_bytes = await self.provider.download_xml(
            doc.provider_ref,
            self.api_token,
            doc.environment.value,
        )
        if not xml_bytes:
            raise BusinessRuleException("XML autorizado indisponivel para impressao.")

        storage_key = self.storage.build_storage_key(
            doc.environment.value,
            doc.market_id,
            doc.id,
            FiscalArtifactType.XML_AUTHORIZED.value,
            "xml",
        )
        sha256 = await self.storage.save(storage_key, xml_bytes)
        await self.artifact_repo.save(
            FiscalArtifact(
                fiscal_document_id=doc.id,
                artifact_type=FiscalArtifactType.XML_AUTHORIZED,
                storage_key=storage_key,
                sha256=sha256,
                content_type="application/xml",
                size_bytes=len(xml_bytes),
            )
        )
        return xml_bytes
