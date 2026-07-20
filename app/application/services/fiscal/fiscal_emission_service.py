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
    FiscalRuleEnforcement,
)
from domain.shared import BusinessRuleException
from application.services.fiscal.fiscal_contract_v2 import (
    FiscalContractV2Serializer,
    canonical_contract_sha256,
)
from application.services.fiscal.fiscal_rollout_service import (
    effective_fiscal_rule_enforcement,
)
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
        contract_serializer=None,
        fiscal_product_rules_enabled: bool = True,
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
        self.contract_serializer = contract_serializer or FiscalContractV2Serializer()
        self.fiscal_product_rules_enabled = fiscal_product_rules_enabled

    def _is_block_mode(self, cfg) -> bool:
        mode = getattr(cfg, "fiscal_rule_enforcement", "off")
        try:
            configured = (
                mode
                if isinstance(mode, FiscalRuleEnforcement)
                else FiscalRuleEnforcement(mode)
            )
        except (TypeError, ValueError):
            configured = FiscalRuleEnforcement.OFF
        return (
            effective_fiscal_rule_enforcement(
                configured,
                product_rules_enabled=self.fiscal_product_rules_enabled,
            )
            is FiscalRuleEnforcement.BLOCK
        )

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
            logger.warning(
                "solicitacao de emissao ignorada: fiscal nao habilitado",
                extra={"extra_data": {
                    "market_id": str(market_id),
                    "sale_id": str(sale_id),
                }}
            )
            return {
                "fiscal_document_id": None,
                "status": FiscalDocumentStatus.NOT_REQUESTED.value,
                "provider_ref": None,
                "message": "Emissão fiscal não habilitada para esta loja.",
            }

        if not cfg.is_ready_for_emission():
            logger.warning(
                "solicitacao de emissao ignorada: configuracao incompleta",
                extra={"extra_data": {
                    "market_id": str(market_id),
                    "sale_id": str(sale_id),
                }}
            )
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
            if doc.status in (FiscalDocumentStatus.AUTHORIZED, FiscalDocumentStatus.CANCELED):
                logger.info(
                    f"solicitacao de emissao ignorada: documento ja {doc.status.value}",
                    extra={"extra_data": {
                        "doc_id": str(doc.id),
                        "market_id": str(market_id),
                        "sale_id": str(sale_id),
                    }}
                )
                return self._doc_to_response(doc)

            # A evidence v2 is the retry boundary: never revalidate/rebuild it
            # from the mutable product catalog or tenant defaults.
            if self._is_block_mode(cfg) and not doc.request_payload_json:
                doc.set_manual_action_required("Payload fiscal persistido ausente em modo block.")
                await self.doc_repo.save(doc)
                return self._doc_to_response(doc)

            # Para os demais estados (QUEUED, PROCESSING, PROVIDER_ERROR, REJECTED, etc.),
            # permitimos reenfileirar. O job_id deterministico evita duplicar job ativo.
            # Vamos re-validar a venda (caso os itens ou dados tenham mudado)
            sale = await self.sale_repo.get_by_id(sale_id)
            if not sale:
                raise BusinessRuleException("Venda não encontrada.")
            if str(sale.market_id) != str(market_id):
                raise BusinessRuleException("Venda não pertence a este mercado.")

            validation = None if doc.request_payload_json else self.pre_validator.validate(
                sale_items=sale.items, payments=sale.payments,
                customer_cpf=getattr(sale, "customer_cpf", None),
                sale_status=getattr(sale, "status", None), fiscal_config=cfg,
            )

            if validation is not None and not validation.is_valid:
                doc.status = FiscalDocumentStatus.REJECTED
                doc.sefaz_message = "; ".join(validation.errors[:3])
                await self.doc_repo.save(doc)
                await self._append_event(doc, "pre_validation_failed",
                                          f"Pré-validação: {doc.sefaz_message}")
                logger.warning(
                    "solicitacao de emissao ignorada: pre-validacao falhou",
                    extra={"extra_data": {
                        "doc_id": str(doc.id),
                        "market_id": str(market_id),
                        "sale_id": str(sale_id),
                        "errors": validation.errors,
                    }}
                )
                return self._doc_to_response(doc)

            # Determina se precisamos reservar cota:
            # Se estava em status que liberou a cota (ex: REJECTED), precisamos reservar.
            # Se estava em QUEUED, PROVIDER_ERROR ou SEFAZ_UNAVAILABLE, a cota já está reservada.
            needs_quota_reservation = doc.status not in (
                FiscalDocumentStatus.QUEUED,
                FiscalDocumentStatus.PROCESSING,
                FiscalDocumentStatus.PROVIDER_ERROR,
                FiscalDocumentStatus.SEFAZ_UNAVAILABLE,
            )

            doc.status = FiscalDocumentStatus.QUEUED
            await self.doc_repo.save(doc)

            period = datetime.utcnow().strftime("%Y%m")
            consuming_addon = False

            if needs_quota_reservation:
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
                    consuming_addon = reserve_result.consuming_addon
                except FiscalQuotaExceededError:
                    doc.status = FiscalDocumentStatus.OFFLINE_RECEIPT_ISSUED
                    doc.sefaz_message = "Limite mensal de emissões atingido. Adquira créditos extras."
                    await self.doc_repo.save(doc)
                    await self._append_event(doc, "quota_exceeded", doc.sefaz_message)
                    logger.warning(
                        "solicitacao de emissao ignorada: limite mensal atingido (cota excedida)",
                        extra={"extra_data": {
                            "doc_id": str(doc.id),
                            "market_id": str(market_id),
                            "sale_id": str(sale_id),
                        }}
                    )
                    return self._doc_to_response(doc)

            # Enfileirar job
            await self._enqueue_emit(
                doc.id, market_id, owner_id,
                period=period,
                consuming_addon=consuming_addon,
                is_retry=True,
            )

            await self._append_event(doc, "queued", "Reemissão enfileirada.",
                                      source=FiscalEventSource.MARKETFY)

            logger.info(
                "job de reemissao enfileirado com sucesso",
                extra={"extra_data": {
                    "doc_id": str(doc.id),
                    "market_id": str(market_id),
                    "sale_id": str(sale_id),
                    "provider_ref": doc.provider_ref,
                }},
            )
            return self._doc_to_response(doc)


        # 4. Pré-validação
        provider_ref = f"marketfy-{market_id}-{sale_id}"
        payload = None
        payload_error = None
        if self._is_block_mode(cfg):
            try:
                payload = self.contract_serializer.build(
                    sale, cfg, getattr(cfg, "neectify_issuer_id", None) or "", provider_ref
                )
            except BusinessRuleException as exc:
                payload_error = str(exc)
            validation = None
        else:
            validation = self.pre_validator.validate(
                sale_items=sale.items, payments=sale.payments,
                customer_cpf=getattr(sale, "customer_cpf", None),
                sale_status=getattr(sale, "status", None), fiscal_config=cfg,
            )

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

        if payload_error or (validation is not None and not validation.is_valid):
            doc.status = FiscalDocumentStatus.REJECTED
            doc.sefaz_message = payload_error or "; ".join(validation.errors[:3])
            await self.doc_repo.save(doc)
            await self._append_event(doc, "pre_validation_failed",
                                      f"Pré-validação: {doc.sefaz_message}")
            logger.warning(
                "solicitacao de emissao ignorada: pre-validacao falhou",
                extra={"extra_data": {
                    "doc_id": str(doc.id),
                    "market_id": str(market_id),
                    "sale_id": str(sale_id),
                    "errors": validation.errors if validation is not None else [payload_error],
                }}
            )
            return self._doc_to_response(doc)

        if payload is not None:
            doc.request_contract_version = payload["contract_version"]
            doc.request_payload_json = payload
            doc.request_payload_sha256 = canonical_contract_sha256(payload)
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
            logger.warning(
                "solicitacao de emissao ignorada: limite mensal atingido (cota excedida)",
                extra={"extra_data": {
                    "doc_id": str(doc.id),
                    "market_id": str(market_id),
                    "sale_id": str(sale_id),
                }}
            )
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
            "job de emissao enfileirado com sucesso",
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
        is_retry: bool = False,
    ):
        queue_name = "fiscal:high"
        arq_job_id = f"emit_nfce:{doc_id}"
        if is_retry:
            # ARQ keeps completed job results in Redis for ~1h (keep_result_seconds).
            # Using the same job_id within that window causes enqueue_job to return None
            # silently, leaving the document stuck in QUEUED forever.
            # A timestamp suffix makes each retry a distinct job while still
            # preventing duplicates from rapid double-clicks within the same second.
            arq_job_id = f"{arq_job_id}:{int(datetime.utcnow().timestamp())}"
        if self.arq_pool is not None:
            logger.info(
                "solicitacao de enfileiramento de job fiscal iniciada",
                extra={"extra_data": {
                    "doc_id": str(doc_id),
                    "market_id": str(market_id),
                    "owner_id": str(owner_id),
                    "queue_name": queue_name,
                    "arq_job_id": arq_job_id,
                }},
            )
            try:
                job = await self.arq_pool.enqueue_job(
                    "emit_nfce_job",
                    doc_id=str(doc_id),
                    market_id=str(market_id),
                    owner_id=str(owner_id),
                    period=period,
                    consuming_addon=consuming_addon,
                    _job_id=arq_job_id,
                    _queue_name=queue_name,
                )
            except Exception as exc:
                logger.exception(
                    "problemas ao enfileirar job: erro de conexao ou redis",
                    extra={"extra_data": {
                        "doc_id": str(doc_id),
                        "market_id": str(market_id),
                        "owner_id": str(owner_id),
                        "queue_name": queue_name,
                        "arq_job_id": arq_job_id,
                        "error": str(exc),
                    }},
                )
                raise

            if job is None:
                logger.warning(
                    "problemas ao enfileirar job: enfileiramento nao confirmado",
                    extra={"extra_data": {
                        "doc_id": str(doc_id),
                        "market_id": str(market_id),
                        "owner_id": str(owner_id),
                        "queue_name": queue_name,
                        "arq_job_id": arq_job_id,
                    }},
                )
            else:
                logger.info(
                    "job enfileirado com sucesso",
                    extra={"extra_data": {
                        "doc_id": str(doc_id),
                        "market_id": str(market_id),
                        "owner_id": str(owner_id),
                        "queue_name": queue_name,
                        "job_id": job.job_id,
                        "arq_job_id": arq_job_id,
                    }},
                )
        else:
            logger.warning("problemas ao enfileirar job: arq_pool indisponivel",
                           extra={"extra_data": {"doc_id": str(doc_id), "queue_name": queue_name}})

    async def reprocess_document(
        self,
        market_id: uuid.UUID,
        doc_id: uuid.UUID,
        owner_id: uuid.UUID,
    ) -> dict:
        """Recoloca documento na fila para reprocessamento, garantindo cota se necessário."""
        doc = await self.doc_repo.get_by_id(doc_id)
        if not doc or str(doc.market_id) != str(market_id):
            raise BusinessRuleException("Documento fiscal não encontrado.")
            
        if doc.is_terminal() and doc.status != FiscalDocumentStatus.REJECTED:
            raise BusinessRuleException(f"Documento em estado terminal '{doc.status.value}' não pode ser reprocessado.")

        # Determina se precisa reservar cota:
        # Se estava em status que liberou a cota (ex: REJECTED), precisamos reservar.
        # Se estava em QUEUED, PROVIDER_ERROR ou SEFAZ_UNAVAILABLE, a cota já está reservada.
        needs_quota_reservation = doc.status not in (
            FiscalDocumentStatus.QUEUED,
            FiscalDocumentStatus.PROVIDER_ERROR,
            FiscalDocumentStatus.SEFAZ_UNAVAILABLE,
        )

        doc.status = FiscalDocumentStatus.QUEUED
        await self.doc_repo.save(doc)

        period = datetime.utcnow().strftime("%Y%m")
        consuming_addon = False

        if needs_quota_reservation:
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
                consuming_addon = reserve_result.consuming_addon
            except FiscalQuotaExceededError:
                doc.status = FiscalDocumentStatus.OFFLINE_RECEIPT_ISSUED
                doc.sefaz_message = "Limite mensal de emissões atingido. Adquira créditos extras."
                await self.doc_repo.save(doc)
                await self._append_event(doc, "quota_exceeded", doc.sefaz_message)
                return self._doc_to_response(doc)

        # Enfileirar job
        await self._enqueue_emit(
            doc.id, market_id, owner_id,
            period=period,
            consuming_addon=consuming_addon,
            is_retry=True,
        )

        await self._append_event(doc, "queued", "Reprocessamento enfileirado.",
                                  source=FiscalEventSource.MARKETFY)
        return self._doc_to_response(doc)

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
