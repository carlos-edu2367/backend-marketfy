"""
FiscalReconciliationService — resolve estados incertos sem duplicar emissão.

Regras críticas:
- Antes de reenviar após timeout, SEMPRE consultar provider por ref.
- Se provider disser "ref já utilizada", consultar e reconciliar.
- Se autorizado no provider mas não localmente, atualizar status.
- Circuit breaker por provider/market para parar tempestade de retries.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from domain.fiscal import (
    FiscalAttempt,
    FiscalAttemptOperation,
    FiscalAttemptStatus,
    FiscalDocument,
    FiscalDocumentStatus,
    FiscalEvent,
    FiscalEventSource,
)
from infra.config.logger import get_logger
from infra.providers.fiscal.base import EmitResultStatus, FiscalProviderGateway
from infra.repositories.fiscal_repo import (
    SQLAlchemyFiscalAttemptRepository,
    SQLAlchemyFiscalDocumentRepository,
    SQLAlchemyFiscalEventRepository,
)

logger = get_logger("fiscal_reconciliation_service")

# Circuit breaker — máximo de tentativas antes de marcar como manual_action_required
_MAX_ATTEMPTS = 5
_BACKOFF_DELAYS = [30, 120, 300, 600, 1800]  # segundos


class FiscalReconciliationService:
    def __init__(
        self,
        doc_repo: SQLAlchemyFiscalDocumentRepository,
        attempt_repo: SQLAlchemyFiscalAttemptRepository,
        event_repo: SQLAlchemyFiscalEventRepository,
        provider: FiscalProviderGateway,
    ):
        self.doc_repo = doc_repo
        self.attempt_repo = attempt_repo
        self.event_repo = event_repo
        self.provider = provider

    async def reconcile_document(
        self,
        doc_id: uuid.UUID,
        api_token: str,
    ) -> FiscalDocument:
        """
        Consulta o provider para resolver estado incerto de um documento.
        Atualiza status localmente baseado na resposta.
        """
        doc = await self.doc_repo.get_by_id(doc_id)
        if not doc:
            raise ValueError(f"Documento fiscal {doc_id} não encontrado.")

        if doc.is_terminal():
            return doc  # Nada a reconciliar

        attempt_count = await self.attempt_repo.count_attempts(doc_id, "consult")
        if attempt_count >= _MAX_ATTEMPTS:
            doc.set_manual_action_required(
                f"Máximo de tentativas de reconciliação atingido ({_MAX_ATTEMPTS})."
            )
            await self.doc_repo.save(doc)
            await self._append_event(doc, "reconciliation_exhausted",
                                      f"Máximo de {_MAX_ATTEMPTS} tentativas atingido.")
            return doc

        attempt = FiscalAttempt(
            fiscal_document_id=doc_id,
            market_id=doc.market_id,
            provider=doc.provider,
            operation=FiscalAttemptOperation.CONSULT,
            attempt_number=attempt_count + 1,
            started_at=datetime.utcnow(),
        )

        consult_result = await self.provider.consult_nfce(
            provider_ref=doc.provider_ref,
            api_token=api_token,
            environment=doc.environment.value,
        )

        if not consult_result.found:
            attempt.finish(FiscalAttemptStatus.FAILED, error_message="Documento não encontrado no provider.")
            await self.attempt_repo.save(attempt)
            # Manter estado atual — pode tentar reemissão
            delay = _BACKOFF_DELAYS[min(attempt_count, len(_BACKOFF_DELAYS) - 1)]
            doc.last_attempt_id = attempt.id
            await self.doc_repo.save(doc)
            await self._append_event(doc, "consult_not_found",
                                      f"Não encontrado no provider, aguardando {delay}s.")
            return doc

        attempt.finish(FiscalAttemptStatus.SUCCESS,
                       http_status=200, provider_request_id=consult_result.provider_ref)
        await self.attempt_repo.save(attempt)

        if consult_result.status == EmitResultStatus.AUTHORIZED:
            doc.set_authorized(
                access_key=consult_result.access_key or "",
                protocol=consult_result.protocol or "",
                number=consult_result.number or 0,
                series=consult_result.series or doc.series or 1,
                sefaz_code=consult_result.sefaz_status_code or "100",
                sefaz_msg=consult_result.sefaz_message or "Autorizado (reconciliado)",
            )
            doc.last_attempt_id = attempt.id
            await self.doc_repo.save(doc)
            await self._append_event(doc, "reconciled_authorized",
                                      "Status reconciliado: autorizado.", source=FiscalEventSource.WORKER)
            logger.info("fiscal_reconciled_authorized",
                        extra={"extra_data": {"doc_id": str(doc_id), "access_key": (consult_result.access_key or "")[:8]}})

        elif consult_result.status == EmitResultStatus.REJECTED:
            doc.set_rejected(
                sefaz_code=consult_result.sefaz_status_code or "",
                sefaz_msg=consult_result.sefaz_message or "Rejeitado (reconciliado)",
            )
            doc.last_attempt_id = attempt.id
            await self.doc_repo.save(doc)
            await self._append_event(doc, "reconciled_rejected",
                                      f"Rejeitado: {consult_result.sefaz_message}",
                                      source=FiscalEventSource.WORKER)
        else:
            # Estado ainda incerto — reagendar retry
            doc.status = FiscalDocumentStatus.PROVIDER_ERROR
            doc.last_attempt_id = attempt.id
            await self.doc_repo.save(doc)

        return doc

    async def should_retry(self, doc: FiscalDocument) -> tuple[bool, Optional[datetime]]:
        """
        Decide se documento deve ser retentado e quando.
        Retorna (deve_retry, próxima_tentativa).
        """
        if doc.is_terminal():
            return False, None

        count = await self.attempt_repo.count_attempts(doc.id, "emit")
        if count >= _MAX_ATTEMPTS:
            return False, None

        delay = _BACKOFF_DELAYS[min(count, len(_BACKOFF_DELAYS) - 1)]
        next_retry = datetime.utcnow() + timedelta(seconds=delay)
        return True, next_retry

    async def _append_event(
        self,
        doc: FiscalDocument,
        event_type: str,
        message: str,
        source: FiscalEventSource = FiscalEventSource.WORKER,
    ):
        try:
            event = FiscalEvent(
                fiscal_document_id=doc.id,
                market_id=doc.market_id,
                event_type=event_type,
                source=source,
                message=message,
            )
            await self.event_repo.append(event)
        except Exception as exc:
            logger.warning("event_append_failed", extra={"extra_data": {"error": str(exc)}})
