from __future__ import annotations
import hashlib
from infra.config.logger import get_logger

logger = get_logger("mp_webhook_processor")


class MercadoPagoWebhookProcessor:
    """Dedupe + resolução de tentativa + tripla âncora de tenant + delegação.

    Regra de ouro: o corpo do webhook NUNCA é tratado como fonte de verdade.
    Este processador usa o payload apenas para descobrir QUAL tentativa
    reconsultar; quem decide o estado real é sempre
    ``PixPaymentService.verify`` (reconsulta autoritativa na API da MP).
    """

    def __init__(self, webhook_repo, attempt_repo, connection_repo, payment_service):
        self.webhook_repo = webhook_repo
        self.attempt_repo = attempt_repo
        self.connection_repo = connection_repo
        self.payment_service = payment_service

    async def process(self, payload: dict, raw_body: bytes, headers: dict) -> int:
        event = None
        try:
            # data.id é normalizado para minúsculas de forma consistente com
            # validate_mp_signature (Task 2): a MP não garante que o casing
            # do data.id no webhook corresponda exatamente ao usado quando a
            # order/tentativa foi criada, então normalizamos aqui tanto para
            # a chave de dedupe quanto para a busca da tentativa — evita
            # linhas de dedupe duplicadas ou tentativas não encontradas.
            data_id = str((payload.get("data") or {}).get("id") or "").lower()
            action = str(payload.get("action") or "")
            if not data_id or not action:
                logger.warning("mp_webhook_invalid_payload")
                return 200
            event_id = f"{data_id}:{action}"
            existing = await self.webhook_repo.get_event("mercado_pago", event_id)
            if existing and existing.processing_status == "processed":
                return 200
            event = existing or await self.webhook_repo.create_event(
                provider="mercado_pago", event_id=event_id, provider_ref=data_id,
                raw_payload_hash=hashlib.sha256(raw_body).hexdigest(),
                processing_status="received", action=action,
                request_id=headers.get("x-request-id"))

            attempt = await self.attempt_repo.get_by_order_id(data_id)
            if attempt is None:
                # aprovado sem venda / order desconhecida → conciliação
                logger.warning("mp_webhook_attempt_not_found", extra={"extra_data": {"order": data_id[:6]}})
                await self.webhook_repo.mark_processed(event.id)
                return 200

            # tripla âncora de tenant
            conn = await self.connection_repo.get_by_market(attempt.market_id)
            payload_user = str(payload.get("user_id") or "")
            if conn is None or (payload_user and conn.mp_user_id and payload_user != conn.mp_user_id):
                logger.warning("mp_webhook_tenant_mismatch")
                await self.webhook_repo.mark_processed(event.id)
                return 200

            # delega a reconsulta autoritativa + conclusão idempotente
            await self.payment_service.verify(
                market_id=attempt.market_id, attempt_id=attempt.id, source="webhook")

            await self.webhook_repo.mark_processed(event.id)
            return 200
        except Exception:
            logger.exception("mp_webhook_processing_failed")
            if event:
                try:
                    await self.webhook_repo.mark_failed(event.id)
                except Exception:
                    pass
            return 200  # MP re-tenta; conciliação cobre
