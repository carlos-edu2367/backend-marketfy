"""
FiscalArtifactService — baixa, valida e armazena XML/PDF fiscais.

O download de artefatos acontece em job separado, APÓS a autorização.
Se o download falhar, o documento permanece autorizado — apenas o artefato
estará indisponível até a próxima tentativa.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Optional

from domain.fiscal import FiscalArtifact, FiscalArtifactType, FiscalDocument
from infra.config.logger import get_logger
from infra.providers.fiscal.base import FiscalProviderGateway
from infra.repositories.fiscal_repo import SQLAlchemyFiscalArtifactRepository
from infra.storage.fiscal_artifact_storage import FiscalArtifactStorage

logger = get_logger("fiscal_artifact_service")


class FiscalArtifactService:
    def __init__(
        self,
        artifact_repo: SQLAlchemyFiscalArtifactRepository,
        storage: FiscalArtifactStorage,
        provider: FiscalProviderGateway,
    ):
        self.artifact_repo = artifact_repo
        self.storage = storage
        self.provider = provider

    async def download_and_store(
        self,
        doc: FiscalDocument,
        api_token: str,
    ) -> dict[str, bool]:
        """
        Baixa XML e PDF do provider e armazena em storage privado.
        Retorna dict indicando sucesso por tipo de artefato.
        """
        results = {}

        # XML
        xml_bytes = await self.provider.download_xml(
            doc.provider_ref, api_token, doc.environment.value
        )
        if xml_bytes:
            key = self.storage.build_storage_key(
                doc.environment.value, doc.market_id, doc.id, "xml_authorized", "xml"
            )
            sha256 = await self.storage.save(key, xml_bytes)
            artifact = FiscalArtifact(
                fiscal_document_id=doc.id,
                artifact_type=FiscalArtifactType.XML_AUTHORIZED,
                storage_key=key,
                sha256=sha256,
                content_type="application/xml",
                size_bytes=len(xml_bytes),
            )
            await self.artifact_repo.save(artifact)
            results["xml"] = True
            logger.info("fiscal_xml_stored",
                        extra={"extra_data": {"doc_id": str(doc.id), "sha256": sha256[:8]}})
        else:
            results["xml"] = False
            logger.warning("fiscal_xml_download_failed",
                           extra={"extra_data": {"doc_id": str(doc.id)}})

        # PDF/DANFE
        pdf_bytes = await self.provider.download_pdf(
            doc.provider_ref, api_token, doc.environment.value
        )
        if pdf_bytes:
            key = self.storage.build_storage_key(
                doc.environment.value, doc.market_id, doc.id, "danfe_pdf", "pdf"
            )
            sha256 = await self.storage.save(key, pdf_bytes)
            artifact = FiscalArtifact(
                fiscal_document_id=doc.id,
                artifact_type=FiscalArtifactType.DANFE_PDF,
                storage_key=key,
                sha256=sha256,
                content_type="application/pdf",
                size_bytes=len(pdf_bytes),
            )
            await self.artifact_repo.save(artifact)
            results["pdf"] = True
        else:
            results["pdf"] = False

        return results

    async def get_artifact(
        self,
        doc: FiscalDocument,
        artifact_type: str,
        market_id: uuid.UUID,
    ) -> Optional[bytes]:
        """
        Recupera artefato do storage.
        Verifica que o documento pertence ao market_id (isolamento tenant).
        """
        if str(doc.market_id) != str(market_id):
            return None

        artifact = await self.artifact_repo.get_by_doc_and_type(doc.id, artifact_type)
        if not artifact:
            return None

        content = await self.storage.load(artifact.storage_key)
        if content and artifact.sha256:
            actual = hashlib.sha256(content).hexdigest()
            if actual != artifact.sha256:
                logger.error("fiscal_artifact_integrity_failure",
                             extra={"extra_data": {"doc_id": str(doc.id), "type": artifact_type}})
                return None
        return content

    def get_signed_url(self, storage_key: str, expires_in: int = 300) -> str:
        return self.storage.generate_signed_url(storage_key, expires_in)
