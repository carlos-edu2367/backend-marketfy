from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from infra.config.logger import get_logger
from infra.config.settings import get_settings
from infra.database.setup import get_db

logger = get_logger("mercado_pago_webhooks")
router = APIRouter()


def validate_mp_signature(request_headers: dict, data_id: str, secret: str) -> bool:
    signature_header = request_headers.get("x-signature") or request_headers.get("X-Signature") or ""
    request_id = request_headers.get("x-request-id") or request_headers.get("X-Request-Id") or ""
    if not signature_header or not request_id or not data_id or not secret:
        return False

    try:
        parts = dict(item.split("=", 1) for item in signature_header.split(",") if "=" in item)
        ts = parts.get("ts", "")
        received_hash = parts.get("v1", "")
        if abs(time.time() - int(ts)) > 300:
            return False
    except Exception:
        return False

    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    expected = hmac.new(
        secret.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, received_hash)


class MercadoPagoWebhookProcessor:
    def __init__(self, webhook_repo, mp_client, credits_service):
        self.webhook_repo = webhook_repo
        self.mp_client = mp_client
        self.credits_service = credits_service

    async def process(self, payload: dict, raw_body: bytes, headers: dict) -> int:
        webhook_event = None
        try:
            notification_id = str(payload.get("id") or payload.get("resource") or "")
            if not notification_id:
                return 200

            existing = await self.webhook_repo.get_event("mercado_pago", notification_id)
            if existing and existing.processing_status == "processed":
                return 200

            raw_hash = hashlib.sha256(raw_body).hexdigest()
            webhook_event = existing or await self.webhook_repo.create_event(
                provider="mercado_pago",
                event_id=notification_id,
                provider_ref=str(payload.get("data", {}).get("id", "")),
                raw_payload_hash=raw_hash,
                processing_status="received",
            )

            event_type = payload.get("type")
            action = payload.get("action")
            if event_type == "payment" and action in ("payment.created", "payment.updated", None):
                payment_id = str(payload.get("data", {}).get("id", ""))
                if payment_id:
                    await self._handle_payment_event(payment_id)
            else:
                await self.webhook_repo.mark_processed(webhook_event.id)
                return 200

            await self.webhook_repo.mark_processed(webhook_event.id)
            return 200
        except Exception as exc:
            logger.exception(
                "mercado_pago_webhook_processing_failed",
                extra={"extra_data": {"error": type(exc).__name__}},
            )
            if webhook_event:
                try:
                    await self.webhook_repo.mark_failed(webhook_event.id)
                except Exception:
                    pass
            return 200

    async def _handle_payment_event(self, payment_id: str) -> None:
        payment = await self.mp_client.get_payment(payment_id)
        status = payment.get("status")
        external_reference = payment.get("external_reference")
        package_id = _parse_uuid(external_reference)
        if not package_id:
            return

        if status == "approved":
            await self.credits_service.activate_package(
                package_id=package_id,
                mp_payment_id=str(payment.get("id") or payment_id),
                payment_data=payment,
            )
        elif status in ("rejected", "cancelled", "cancelled"):
            await self.credits_service.mark_package_failed(
                package_id=package_id,
                reason=payment.get("status_detail", ""),
            )


@router.post("/mercado-pago")
async def mercado_pago_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}

    data_id = str(payload.get("data", {}).get("id", ""))
    if not validate_mp_signature(dict(request.headers), data_id, settings.MP_WEBHOOK_SECRET):
        logger.warning("mercado_pago_webhook_invalid_signature")
        return Response(status_code=401)

    from application.services.fiscal.fiscal_credits_service import FiscalCreditsService
    from application.services.fiscal.fiscal_notification_service import FiscalNotificationService
    from application.services.fiscal.fiscal_quota_service import FiscalQuotaService
    from infra.clients.mercado_pago_client import MercadoPagoClient
    from infra.repositories.audit_repo import SQLAlchemyAuditLogRepository
    from application.services.audit_service import AuditService
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalNotificationRepository,
        SQLAlchemyFiscalUsageRepository,
        SQLAlchemyProviderWebhookEventRepository,
    )

    usage_repo = SQLAlchemyFiscalUsageRepository(db)
    quota_service = FiscalQuotaService(usage_repo)
    notification_service = FiscalNotificationService(SQLAlchemyFiscalNotificationRepository(db))
    audit_service = AuditService(SQLAlchemyAuditLogRepository(db))
    mp_client = MercadoPagoClient(
        settings.MP_ACCESS_TOKEN,
        sandbox=settings.MP_SANDBOX,
        base_url=settings.MP_BASE_URL,
    )
    credits_service = FiscalCreditsService(
        credits_repo=usage_repo,
        quota_repo=usage_repo,
        mp_client=mp_client,
        quota_service=quota_service,
        notification_service=notification_service,
        audit_service=audit_service,
        settings=settings,
    )
    processor = MercadoPagoWebhookProcessor(
        SQLAlchemyProviderWebhookEventRepository(db),
        mp_client,
        credits_service,
    )
    status = await processor.process(payload, raw_body, dict(request.headers))
    return Response(status_code=status)


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
