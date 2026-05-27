from __future__ import annotations

import hashlib
import hmac
import json
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from infra.config.logger import get_logger
from infra.config.settings import get_settings
from infra.database.setup import get_db

logger = get_logger("billing_core_webhooks")
router = APIRouter()


def validate_bc_signature(
    raw_body: bytes,
    header: str,
    secret: str,
) -> bool:
    if not header or not secret:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, header)


class BillingCoreWebhookProcessor:
    def __init__(self, webhook_repo, credits_service):
        self.webhook_repo = webhook_repo
        self.credits_service = credits_service

    async def process(self, payload: dict, raw_body: bytes, headers: dict) -> int:
        webhook_event = None
        try:
            payment_id = str(payload.get("payment_id") or "")
            system_payment_id = str(payload.get("system_payment_id") or "")
            payment_status = str(payload.get("payment_status") or "")

            if not payment_id or not payment_status:
                logger.warning("billing_core_webhook_invalid_payload")
                return 200

            event_id = f"{payment_id}:{payment_status}"
            existing = await self.webhook_repo.get_event("billing_core", event_id)
            if existing and existing.processing_status == "processed":
                return 200

            raw_hash = hashlib.sha256(raw_body).hexdigest()
            webhook_event = existing or await self.webhook_repo.create_event(
                provider="billing_core",
                event_id=event_id,
                provider_ref=payment_id,
                raw_payload_hash=raw_hash,
                processing_status="received",
            )

            package_id = _parse_uuid(system_payment_id)
            if not package_id:
                logger.warning(f"billing_core_webhook_invalid_package_id: {system_payment_id}")
                await self.webhook_repo.mark_processed(webhook_event.id)
                return 200

            status_upper = payment_status.upper()
            if status_upper in ("CONFIRMED", "RECEIVED", "RECEIVED_IN_CASH"):
                await self.credits_service.activate_package(
                    package_id=package_id,
                    bc_payment_id=payment_id,
                    payment_data=payload,
                )
            elif status_upper in ("OVERDUE", "REFUNDED", "REFUND_IN_PROGRESS", "CHARGEBACK_REQUESTED"):
                await self.credits_service.mark_package_failed(
                    package_id=package_id,
                    reason=f"Status: {payment_status}",
                )
            else:
                pass

            await self.webhook_repo.mark_processed(webhook_event.id)
            return 200
        except Exception as exc:
            logger.exception(
                "billing_core_webhook_processing_failed",
                extra={"extra_data": {"error": type(exc).__name__}},
            )
            if webhook_event:
                try:
                    await self.webhook_repo.mark_failed(webhook_event.id)
                except Exception:
                    pass
            return 200


@router.post("/billing-core")
async def billing_core_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}

    # TODO(contract-validation):
    # Este contrato foi assumido conforme documentação atual.
    # Confirmar oficialmente com o time do Billing Core.
    signature = request.headers.get("X-Billing-Core-Signature") or request.headers.get("x-billing-core-signature")
    if not signature:
        logger.warning("billing_core_webhook_missing_signature")
        return Response(status_code=401)

    if not validate_bc_signature(raw_body, signature, settings.BILLING_CORE_WEBHOOK_SECRET):
        logger.warning("billing_core_webhook_invalid_signature")
        return Response(status_code=401)

    from application.services.fiscal.fiscal_credits_service import FiscalCreditsService
    from application.services.fiscal.fiscal_notification_service import FiscalNotificationService
    from application.services.fiscal.fiscal_quota_service import FiscalQuotaService
    from infra.clients.billing_core_client import BillingCoreClient
    from infra.repositories.audit_repo import SQLAlchemyAuditLogRepository
    from application.services.audit_service import AuditService
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalNotificationRepository,
        SQLAlchemyFiscalUsageRepository,
        SQLAlchemyProviderWebhookEventRepository,
    )
    from infra.repositories.sqlalchemy_repos import SQLAlchemyUserRepository

    usage_repo = SQLAlchemyFiscalUsageRepository(db)
    user_repo = SQLAlchemyUserRepository(db)
    quota_service = FiscalQuotaService(usage_repo)
    notification_service = FiscalNotificationService(SQLAlchemyFiscalNotificationRepository(db))
    audit_service = AuditService(SQLAlchemyAuditLogRepository(db))
    bc_client = BillingCoreClient()

    credits_service = FiscalCreditsService(
        credits_repo=usage_repo,
        quota_repo=usage_repo,
        mp_client=None,  # Not used by webhooks
        bc_client=bc_client,
        user_repo=user_repo,
        quota_service=quota_service,
        notification_service=notification_service,
        audit_service=audit_service,
        settings=settings,
    )
    processor = BillingCoreWebhookProcessor(
        SQLAlchemyProviderWebhookEventRepository(db),
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
