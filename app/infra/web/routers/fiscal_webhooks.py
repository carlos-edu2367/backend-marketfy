"""
Router fiscal webhooks — recebe callbacks dos provedores fiscais.

POST /api/v1/fiscal/webhooks/focus-nfe     — evento Focus NFe
POST /api/v1/fiscal/webhooks/neectify      — evento Neectify Fiscal

Segurança:
  - Focus NFe: HMAC-SHA256 via header X-Focus-Signature
  - Neectify:  HMAC-SHA256 com timestamp via X-Neectify-Signature + X-Neectify-Timestamp
  - Idempotência: dedup via provider_ref + event_type na tabela provider_webhook_events
  - Nunca loga payload completo
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
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

_NEECTIFY_STATUS_MAP = {
    "authorized": "authorized",
    "rejected": "rejected",
    "failed_permanent": "rejected",
    "cancelled": "canceled",
    "cancel_requested": "canceled",
    "failed_transient": "provider_error",
}

# Tolerância de clock para validar X-Neectify-Timestamp (5 minutos)
_NEECTIFY_TIMESTAMP_TOLERANCE_S = 300


def _verify_focus_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verifica assinatura HMAC-SHA256 do webhook Focus NFe."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # removeprefix evita o bug de lstrip que strip caracteres individuais do set
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


def _verify_neectify_signature(
    payload: dict,
    timestamp: int,
    signature: str,
    secret: str,
) -> bool:
    """Verifica assinatura HMAC-SHA256 do webhook Neectify Fiscal.

    Formato: HMAC-SHA256(key=secret, msg=f"{timestamp}.{canonical_json}")
    Cabeçalhos: X-Neectify-Timestamp + X-Neectify-Signature (sha256=<hex>)
    """
    if not secret or not signature:
        return False
    now = int(time.time())
    if abs(now - timestamp) > _NEECTIFY_TIMESTAMP_TOLERANCE_S:
        return False
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    signed_content = f"{timestamp}.{body}"
    expected = hmac.new(secret.encode(), signed_content.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


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
                from infra.queues.arq_config import get_arq_pool, QUEUE_FISCAL_RECONCILE
                arq_pool = await get_arq_pool()
                await arq_pool.enqueue_job(
                    "download_artifacts_job",
                    doc_id=str(doc.id),
                    market_id=str(doc.market_id),
                    _queue_name=QUEUE_FISCAL_RECONCILE,
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


# =============================================================================
# NEECTIFY FISCAL WEBHOOK
# =============================================================================

@router.post("/neectify", tags=["fiscal-webhooks"])
async def neectify_webhook(
    request: Request,
    x_neectify_timestamp: str = Header(default=""),
    x_neectify_signature: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
):
    """
    Recebe notificação de status do Neectify Fiscal.

    Neectify envia POST quando o status de uma NFC-e muda na SEFAZ.
    Assina com HMAC-SHA256 usando timestamp para prevenir replay attacks.
    """
    body = await request.body()
    settings = get_settings()

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(400, "Payload inválido.")

    # Verificar assinatura com timestamp anti-replay
    if settings.FISCAL_SECRET_KEY:
        try:
            ts = int(x_neectify_timestamp)
        except (ValueError, TypeError):
            raise HTTPException(401, "Timestamp inválido.")

        if not _verify_neectify_signature(payload, ts, x_neectify_signature, settings.FISCAL_SECRET_KEY):
            logger.warning("neectify_webhook_invalid_signature")
            raise HTTPException(401, "Assinatura inválida.")

    event_type = payload.get("event", "")
    data = payload.get("data") or {}
    nfce_id = data.get("id", "") or payload.get("document_id", "")
    external_id = data.get("external_id") or payload.get("external_id", "")
    neectify_status = data.get("status", "") or payload.get("status", "")
    status_mapped = _NEECTIFY_STATUS_MAP.get(neectify_status, "")

    if not nfce_id and not external_id:
        raise HTTPException(400, "Campos 'data.id' ou 'data.external_id' obrigatórios.")

    from infra.repositories.fiscal_repo import SQLAlchemyFiscalDocumentRepository
    from infra.database.models import ProviderWebhookEventModel
    from domain.fiscal import FiscalDocumentStatus
    from sqlalchemy import select

    dedup_key = f"neectify:{nfce_id}:{event_type}:{neectify_status}"
    existing = await db.scalar(
        select(ProviderWebhookEventModel).where(
            ProviderWebhookEventModel.dedup_key == dedup_key
        )
    )
    if existing:
        logger.info("neectify_webhook_duplicate", extra={"extra_data": {"nfce_id": nfce_id, "event": event_type}})
        return {"message": "Já processado."}

    webhook_event = ProviderWebhookEventModel(
        id=uuid.uuid4(),
        provider="neectify_fiscal",
        event_type=event_type,
        provider_ref=nfce_id,
        dedup_key=dedup_key,
        raw_payload=json.dumps({"event": event_type, "id": nfce_id, "status": neectify_status}),
        received_at=datetime.utcnow(),
        processed=False,
    )
    db.add(webhook_event)

    # Localizar documento pelo provider_ref (neectify id) ou external_id
    doc_repo = SQLAlchemyFiscalDocumentRepository(db)
    doc = await doc_repo.get_by_provider_ref("neectify_fiscal", nfce_id)
    if not doc and external_id:
        doc = await doc_repo.get_by_provider_ref("neectify_fiscal", external_id)

    if not doc:
        logger.info(
            "neectify_webhook_unknown_ref",
            extra={"extra_data": {"nfce_id": nfce_id, "external_id": external_id, "event": event_type}},
        )
        webhook_event.processed = True
        await db.commit()
        return {"message": "Ref desconhecida — evento ignorado."}

    status_enum_map = {
        "authorized": FiscalDocumentStatus.AUTHORIZED,
        "rejected": FiscalDocumentStatus.REJECTED,
        "canceled": FiscalDocumentStatus.CANCELED,
        "provider_error": FiscalDocumentStatus.PROVIDER_ERROR,
        "processing": FiscalDocumentStatus.PROCESSING,
    }
    new_status = status_enum_map.get(status_mapped)

    if new_status and not doc.is_terminal():
        doc.status = new_status
        if new_status == FiscalDocumentStatus.AUTHORIZED:
            doc.authorized_at = datetime.utcnow()
            access_key = data.get("access_key") or payload.get("access_key")
            if access_key:
                doc.access_key = access_key
            doc.protocol = data.get("authorization_protocol") or data.get("protocol") or payload.get("protocol")
        await doc_repo.save(doc)

        if new_status == FiscalDocumentStatus.AUTHORIZED:
            try:
                from infra.queues.arq_config import get_arq_pool, QUEUE_FISCAL_RECONCILE
                arq_pool = await get_arq_pool()
                await arq_pool.enqueue_job(
                    "download_artifacts_job",
                    doc_id=str(doc.id),
                    market_id=str(doc.market_id),
                    _queue_name=QUEUE_FISCAL_RECONCILE,
                )
            except Exception:
                pass

    webhook_event.processed = True
    await db.commit()

    logger.info(
        "neectify_webhook_processed",
        extra={"extra_data": {"nfce_id": nfce_id, "event": event_type, "status": neectify_status, "doc_id": str(doc.id)}},
    )
    return {"message": "Processado."}
