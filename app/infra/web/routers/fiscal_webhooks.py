"""
Router fiscal webhooks — recebe callbacks do provedor Focus NFe.

POST /api/v1/fiscal/webhooks/focus-nfe  — evento de atualização de status

Segurança:
  - Verificação de assinatura HMAC-SHA256 (header X-Focus-Signature)
  - Idempotência: dedup via provider_ref + event_type na tabela provider_webhook_events
  - Nunca loga payload completo
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from infra.config.logger import get_logger
from infra.config.settings import get_settings
from infra.database.setup import get_db

logger = get_logger("fiscal_webhooks")
router = APIRouter()

_FOCUS_STATUS_MAP = {
    "autorizado": "authorized",
    "rejeitado": "rejected",
    "cancelado": "canceled",
    "processando_autorizacao": "processing",
    "erro_autorizacao": "provider_error",
    "denegado": "rejected",
}


def _verify_focus_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verifica assinatura HMAC-SHA256 do webhook Focus NFe."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.lstrip("sha256="))


@router.post("/focus-nfe", tags=["fiscal-webhooks"])
async def focus_nfe_webhook(
    request: Request,
    x_focus_signature: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
):
    """
    Recebe notificação de status da Focus NFe.

    A Focus NFe envia POST quando o status de uma NFC-e muda.
    Nós consultamos idempotência antes de processar.
    """
    body = await request.body()
    settings = get_settings()

    # Verificar assinatura apenas se FISCAL_SECRET_KEY estiver configurada
    if settings.FISCAL_SECRET_KEY:
        if not _verify_focus_signature(body, x_focus_signature, settings.FISCAL_SECRET_KEY):
            logger.warning("focus_webhook_invalid_signature")
            raise HTTPException(401, "Assinatura inválida.")

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(400, "Payload inválido.")

    ref = payload.get("ref", "")
    status_raw = payload.get("status", "")
    status = _FOCUS_STATUS_MAP.get(status_raw, status_raw)

    if not ref:
        raise HTTPException(400, "Campo 'ref' obrigatório.")

    # Idempotência: verificar se já processamos este evento
    from infra.repositories.fiscal_repo import SQLAlchemyFiscalDocumentRepository
    from domain.fiscal import (
        FiscalDocumentStatus,
        FiscalEvent,
        FiscalEventSource,
        ProviderWebhookEvent,
    )

    # Registrar evento de webhook para auditoria / dedup
    from infra.database.models import ProviderWebhookEventModel
    from sqlalchemy import select

    dedup_key = f"focus:{ref}:{status_raw}"
    existing = await db.scalar(
        select(ProviderWebhookEventModel).where(
            ProviderWebhookEventModel.dedup_key == dedup_key
        )
    )
    if existing:
        logger.info("focus_webhook_duplicate", extra={"extra_data": {"ref": ref, "status": status_raw}})
        return {"message": "Já processado."}

    # Salvar evento bruto (sem PII — ref não contém dados pessoais)
    webhook_event = ProviderWebhookEventModel(
        id=uuid.uuid4(),
        provider="focus_nfe",
        event_type=status_raw,
        provider_ref=ref,
        dedup_key=dedup_key,
        raw_payload=json.dumps({"ref": ref, "status": status_raw}),
        received_at=datetime.utcnow(),
        processed=False,
    )
    db.add(webhook_event)

    # Localizar documento pelo provider_ref
    doc_repo = SQLAlchemyFiscalDocumentRepository(db)
    doc = await doc_repo.get_by_provider_ref("focus_nfe", ref)

    if not doc:
        logger.info(
            "focus_webhook_unknown_ref",
            extra={"extra_data": {"ref": ref, "status": status_raw}},
        )
        webhook_event.processed = True
        await db.commit()
        return {"message": "Ref desconhecida — evento ignorado."}

    # Mapear status para domínio e atualizar documento
    status_enum_map = {
        "authorized": FiscalDocumentStatus.AUTHORIZED,
        "rejected": FiscalDocumentStatus.REJECTED,
        "canceled": FiscalDocumentStatus.CANCELED,
        "processing": FiscalDocumentStatus.PROCESSING,
        "provider_error": FiscalDocumentStatus.PROVIDER_ERROR,
    }
    new_status = status_enum_map.get(status)
    if new_status and not doc.is_terminal():
        doc.status = new_status
        if new_status == FiscalDocumentStatus.AUTHORIZED:
            doc.authorized_at = datetime.utcnow()
            access_key = payload.get("chave_nfe") or payload.get("access_key")
            if access_key:
                doc.access_key = access_key
            doc.protocol = payload.get("protocolo") or payload.get("protocol")
        await doc_repo.save(doc)

        # Enfileirar artifacts se autorizado
        if new_status == FiscalDocumentStatus.AUTHORIZED:
            try:
                from infra.queues.arq_config import get_arq_pool
                arq_pool = await get_arq_pool()
                await arq_pool.enqueue_job(
                    "download_artifacts_job",
                    doc_id=str(doc.id),
                    market_id=str(doc.market_id),
                    _queue_name="fiscal:reconcile",
                )
            except Exception:
                pass

    webhook_event.processed = True
    await db.commit()

    logger.info(
        "focus_webhook_processed",
        extra={"extra_data": {"ref": ref, "status": status_raw, "doc_id": str(doc.id)}},
    )
    return {"message": "Processado."}
