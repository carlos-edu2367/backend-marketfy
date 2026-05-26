"""
FiscalEmissionService — orquestra o processo de emissão fiscal.

Responsabilidade: ser o ponto de entrada do backend para a emissão.
NÃO chama o provider diretamente — apenas prepara e enfileira.

Fluxo:
1. Verificar se fiscal está habilitado e configurado
2. Verificar/criar FiscalDocument (idempotência por sale_id)
3. Pré-validar dados da venda
4. Reservar cota fiscal
5. Enfileirar job de emissão no ARQ
6. Retornar status imediato ao PDV

O worker executa a chamada real ao provider de forma assíncrona.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, Tuple

from domain.fiscal import (
    FiscalDocument,
    FiscalDocumentStatus,
    FiscalEnvironment,
    FiscalEvent,
    FiscalEventSource,
    FiscalQuotaExceededError,
)
from domain.shared import BusinessRuleException
from infra.config.logger import get_logger
from infra.repositories.fiscal_repo import (
    SQLAlchemyFiscalDocumentRepository,
    SQLAlchemyFiscalEventRepository,
    SQLAlchemyFiscalTenantConfigRepository,
)

logger = get_logger("fiscal_emission_service")


class FiscalEmissionService:
    def __init__(
        self,
        config_repo: SQLAlchemyFiscalTenantConfigRepository,
        doc_repo: SQLAlchemyFiscalDocumentRepository,
        event_repo: SQLAlchemyFiscalEventRepository,
        quota_service,          # FiscalQuotaService
        pre_validator,          # FiscalPreValidator
        sale_repo,              # SaleRepositoryInterface
        market_repo,            # MarketRepositoryInterface
        arq_pool=None,          # ARQ pool (opcional — None em testes)
        plan_access_service=None,  # PlanAccessService (opcional — None em testes)
    ):
        self.config_repo = config_repo
        self.doc_repo = doc_repo
        self.event_repo = event_repo
        self.quota_service = quota_service
        self.pre_validator = pre_validator
        self.sale_repo = sale_repo
        self.market_repo = market_repo
        self.arq_pool = arq_pool
        self.plan_access_service = plan_access_service

    async def request_emission(
        self,
        market_id: uuid.UUID,
        sale_id: uuid.UUID,
        owner_id: uuid.UUID,
    ) -> dict:
        """
        Ponto de entrada do PDV para solicitar emissão.
        Retorna status imediato — emissão real ocorre no worker.

        Returns:
            dict com fiscal_document_id, status, provider_ref, message
        """
        # 1. Verificar configuração fiscal
        cfg = await self.config_repo.get_by_market(market_id)
        if not cfg or not cfg.enabled:
            await self._record_event_direct(
                market_id=market_id,
                doc_id=None,
                event_type="emission_skipped",
                message="Fiscal não habilitado para este mercado.",
            )
            return {
                "fiscal_document_id": None,
                "status": FiscalDocumentStatus.NOT_REQUESTED.value,
                "provider_ref": None,
                "message": "Emissão fiscal não habilitada para esta loja.",
            }

        if not cfg.is_ready_for_emission():
            return {
                "fiscal_document_id": None,
                "status": FiscalDocumentStatus.MANUAL_ACTION_REQUIRED.value,
                "provider_ref": None,
                "message": "Configuração fiscal incompleta. Contate o suporte.",
            }

        # 2. Verificar venda
        sale = await self.sale_repo.get_by_id(sale_id)
        if not sale:
            raise BusinessRuleException("Venda não encontrada.")
        if str(sale.market_id) != str(market_id):
            raise BusinessRuleException("Venda não pertence a este mercado.")

        # 3. Idempotência — buscar documento existente
        doc = await self.doc_repo.get_by_sale(market_id, sale_id)
        if doc:
            if doc.is_terminal():
                return self._doc_to_response(doc)
            # Já existe e não é terminal — retornar status atual
            return self._doc_to_response(doc)

        # 4. Pré-validação
        validation = self.pre_validator.validate(
            sale_items=sale.items,
            payments=sale.payments,
            customer_cpf=getattr(sale, "customer_cpf", None),
            sale_status=getattr(sale, "status", None),
            fiscal_config=cfg,
        )

        provider_ref = f"marketfy-{market_id}-{sale_id}"

        # 5. Criar FiscalDocument
        doc = FiscalDocument(
            market_id=market_id,
            sale_id=sale_id,
            document_type="nfce",
            provider=cfg.provider,
            provider_ref=provider_ref,
            environment=cfg.environment,
            idempotency_key=provider_ref,
        )

        if not validation.is_valid:
            doc.status = FiscalDocumentStatus.REJECTED
            doc.sefaz_message = "; ".join(validation.errors[:3])
            await self.doc_repo.save(doc)
            await self._append_event(doc, "pre_validation_failed",
                                      f"Pré-validação: {doc.sefaz_message}")
            return self._doc_to_response(doc)

        doc.status = FiscalDocumentStatus.QUEUED
        await self.doc_repo.save(doc)

        # 6. Reservar cota
        period = datetime.utcnow().strftime("%Y%m")
        included_limit = 0
        if self.plan_access_service:
            included_limit = await self.plan_access_service.get_fiscal_monthly_limit(owner_id)

        try:
            reserve_result = await self.quota_service.check_and_reserve(
                owner_id=owner_id,
                market_id=market_id,
                period=period,
                included_limit=included_limit,
            )
        except FiscalQuotaExceededError:
            doc.status = FiscalDocumentStatus.OFFLINE_RECEIPT_ISSUED
            doc.sefaz_message = "Limite mensal de emissões atingido. Adquira créditos extras."
            await self.doc_repo.save(doc)
            await self._append_event(doc, "quota_exceeded", doc.sefaz_message)
            return self._doc_to_response(doc)

        # 7. Enfileirar job
        await self._enqueue_emit(
            doc.id, market_id, owner_id,
            period=period,
            consuming_addon=reserve_result.consuming_addon,
        )

        await self._append_event(doc, "queued", "Emissão enfileirada.",
                                  source=FiscalEventSource.MARKETFY)

        logger.info(
            "fiscal_emission_queued",
            extra={"extra_data": {
                "doc_id": str(doc.id),
                "market_id": str(market_id),
                "sale_id": str(sale_id),
                "provider_ref": provider_ref,
            }},
        )

        return self._doc_to_response(doc)

    async def get_status(
        self, market_id: uuid.UUID, sale_id: uuid.UUID
    ) -> Optional[dict]:
        """Retorna status atual do documento fiscal de uma venda."""
        doc = await self.doc_repo.get_by_sale(market_id, sale_id)
        if not doc:
            return None
        return self._doc_to_response(doc)

    async def get_document(
        self, market_id: uuid.UUID, doc_id: uuid.UUID
    ) -> Optional[FiscalDocument]:
        doc = await self.doc_repo.get_by_id(doc_id)
        if not doc or str(doc.market_id) != str(market_id):
            return None
        return doc

    def _doc_to_response(self, doc: FiscalDocument) -> dict:
        return {
            "fiscal_document_id": str(doc.id),
            "status": doc.status.value,
            "provider_ref": doc.provider_ref,
            "access_key": doc.access_key,
            "protocol": doc.protocol,
            "number": doc.number,
            "series": doc.series,
            "sefaz_message": doc.sefaz_message,
            "authorized_at": doc.authorized_at.isoformat() if doc.authorized_at else None,
            "message": _status_message(doc.status),
        }

    async def _enqueue_emit(
        self,
        doc_id: uuid.UUID,
        market_id: uuid.UUID,
        owner_id: uuid.UUID,
        period: str = "",
        consuming_addon: bool = False,
    ):
        if self.arq_pool:
            await self.arq_pool.enqueue_job(
                "emit_nfce_job",
                doc_id=str(doc_id),
                market_id=str(market_id),
                owner_id=str(owner_id),
                period=period,
                consuming_addon=consuming_addon,
                _queue_name="fiscal:high",
            )
        else:
            logger.warning("arq_pool_unavailable_skipping_queue",
                           extra={"extra_data": {"doc_id": str(doc_id)}})

    async def _append_event(
        self,
        doc: FiscalDocument,
        event_type: str,
        message: str,
        source: FiscalEventSource = FiscalEventSource.MARKETFY,
        metadata: Optional[dict] = None,
    ):
        try:
            event = FiscalEvent(
                fiscal_document_id=doc.id,
                market_id=doc.market_id,
                event_type=event_type,
                source=source,
                message=message,
                metadata_json_sanitized=metadata,
            )
            await self.event_repo.append(event)
        except Exception as exc:
            logger.warning("fiscal_event_append_failed",
                           extra={"extra_data": {"error": str(exc)}})

    async def _record_event_direct(
        self, market_id: uuid.UUID, doc_id, event_type: str, message: str
    ):
        pass  # No-op quando doc ainda não existe


def _status_message(status: FiscalDocumentStatus) -> str:
    messages = {
        FiscalDocumentStatus.QUEUED: "NFC-e na fila de emissão.",
        FiscalDocumentStatus.PROCESSING: "Emitindo NFC-e...",
        FiscalDocumentStatus.AUTHORIZED: "NFC-e autorizada pela SEFAZ.",
        FiscalDocumentStatus.REJECTED: "NFC-e rejeitada. Verifique as pendências fiscais.",
        FiscalDocumentStatus.PROVIDER_ERROR: "Erro no provider. Reprocessamento automático em andamento.",
        FiscalDocumentStatus.SEFAZ_UNAVAILABLE: "SEFAZ indisponível. Reprocessando automaticamente.",
        FiscalDocumentStatus.OFFLINE_RECEIPT_ISSUED: "Cupom não fiscal emitido.",
        FiscalDocumentStatus.CONTINGENCY_REQUIRED: "Emissão em contingência necessária.",
        FiscalDocumentStatus.MANUAL_ACTION_REQUIRED: "Ação manual necessária. Contate o suporte.",
        FiscalDocumentStatus.CANCELED: "NFC-e cancelada.",
        FiscalDocumentStatus.NOT_REQUESTED: "Emissão fiscal não aplicável.",
    }
    return messages.get(status, "Status desconhecido.")
