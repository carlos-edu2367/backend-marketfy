from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from infra.config.logger import get_logger
from infra.config.settings import get_settings
from infra.database.setup import get_db
from infra.web.routers.billing_core_webhooks import validate_bc_signature

logger = get_logger("billing_invoice_webhooks")
router = APIRouter()

_PAID = ("PAID", "CONFIRMED", "RECEIVED", "RECEIVED_IN_CASH")
_FAILED = ("OVERDUE", "REFUNDED", "REFUND_IN_PROGRESS", "CHARGEBACK_REQUESTED", "CANCELED", "EXPIRED")


class InvoiceWebhookProcessor:
    def __init__(self, invoice_service):
        self._svc = invoice_service

    async def process(self, payload: dict) -> int:
        payment_id = str(payload.get("payment_id") or "")
        system_payment_id = str(payload.get("system_payment_id") or "")
        payment_status = str(payload.get("payment_status") or "").upper()
        if not payment_id or not payment_status:
            logger.warning("invoice_webhook_invalid_payload")
            return 200
        try:
            invoice_id = uuid.UUID(system_payment_id)
        except (TypeError, ValueError):
            logger.warning("invoice_webhook_invalid_id", extra={"extra_data": {"system_payment_id": system_payment_id}})
            return 200
        if payment_status in _PAID:
            await self._svc.activate_invoice(invoice_id, payment_id, payload)
        elif payment_status in _FAILED:
            await self._svc.mark_invoice_failed(invoice_id, reason=f"Status: {payment_status}")
        return 200


@router.post("/billing-invoices")
async def billing_invoice_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}

    signature = request.headers.get("X-Webhook-Signature-256")
    if not signature or not validate_bc_signature(payload, signature, settings.BILLING_CORE_WEBHOOK_SECRET):
        logger.warning("invoice_webhook_invalid_signature")
        return Response(status_code=401)

    from application.services.invoice_service import InvoiceService
    from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
    from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository
    from infra.clients.billing_core_client import BillingCoreClient

    svc = InvoiceService(
        invoice_repo=SQLAlchemyBillingInvoiceRepository(db),
        subscription_repo=SQLAlchemyBillingSubscriptionRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        billing_client=BillingCoreClient(),
        settings=settings,
    )
    processor = InvoiceWebhookProcessor(svc)
    code = await processor.process(payload)
    await db.commit()
    return Response(status_code=code)
