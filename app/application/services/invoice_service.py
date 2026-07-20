"""InvoiceService — assinatura no modo 'por pagamento' (faturas via /payments)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Optional

from infra.config.logger import get_logger

logger = get_logger("invoice_service")

PERIOD_DAYS = {"monthly": 30, "semiannual": 180, "annual": 365}


def price_for_period(plan, subscription_type: str) -> Decimal:
    mapping = {
        "monthly": getattr(plan, "price_monthly", 0) or 0,
        "semiannual": getattr(plan, "price_180days", 0) or 0,
        "annual": getattr(plan, "price_annual", 0) or 0,
    }
    return Decimal(str(mapping.get(subscription_type, mapping["monthly"])))


class InvoiceService:
    def __init__(self, invoice_repo, subscription_repo, plan_repo, billing_client, settings):
        self._inv = invoice_repo
        self._sub = subscription_repo
        self._plan = plan_repo
        self._bc = billing_client
        self._settings = settings

    # -- Contratação -------------------------------------------------------
    async def contract(self, owner_id: uuid.UUID, plan_id: uuid.UUID,
                       subscription_type: str, idempotency_key: str) -> Dict[str, Any]:
        if subscription_type not in PERIOD_DAYS:
            raise ValueError("subscription_type inválido para faturas.")

        # Idempotência: já contratado com essa chave?
        existing_inv = await self._inv.get_by_idempotency_key(idempotency_key)
        if existing_inv is not None:
            return {
                "subscription_id": str(existing_inv.subscription_id),
                "invoice_id": str(existing_inv.id),
                "job_id": existing_inv.bc_job_id,
                "checkout_url": existing_inv.checkout_url,
            }

        plan = await self._plan.get_by_id(plan_id)
        if plan is None or not plan.is_active:
            raise ValueError("Plano não disponível.")

        from infra.database.models import BillingSubscriptionModel
        sub = BillingSubscriptionModel(
            owner_id=owner_id, plan_id=plan_id,
            billing_system=self._settings.BILLING_CORE_SYSTEM,
            billing_system_sub_id=str(owner_id),
            billing_mode="invoice",
            status="pending",
            subscription_type=subscription_type,
            value=price_for_period(plan, subscription_type),
            idempotency_key=f"invsub-{owner_id}-{plan_id}-{subscription_type}",
        )
        sub = await self._sub.save(sub)

        now = datetime.utcnow()
        invoice = await self._create_invoice(
            owner_id=owner_id, subscription=sub, plan=plan,
            period_start=now, due_date=now,
            idempotency_key=idempotency_key,
        )
        await self._create_checkout(invoice, plan)
        checkout = await self.refresh_checkout(invoice.id)

        return {
            "subscription_id": str(sub.id),
            "invoice_id": str(invoice.id),
            "job_id": invoice.bc_job_id,
            "checkout_url": checkout.get("checkout_url"),
        }

    async def _create_invoice(self, *, owner_id, subscription, plan, period_start,
                              due_date, idempotency_key):
        days = PERIOD_DAYS[subscription.subscription_type]
        period_end = period_start + timedelta(days=days)
        return await self._inv.create(
            owner_id=owner_id,
            subscription_id=subscription.id,
            plan_id=plan.id,
            period_start=period_start,
            period_end=period_end,
            due_date=due_date,
            amount=price_for_period(plan, subscription.subscription_type),
            status="pending",
            idempotency_key=idempotency_key,
        )

    async def _create_checkout(self, invoice, plan) -> None:
        s = self._settings
        result = await self._bc.create_payment(
            value=f"{Decimal(str(invoice.amount)):.2f}",
            description=f"Assinatura Marketfy {plan.name} — {invoice.subscription_id}",
            system=s.BILLING_CORE_SYSTEM,
            system_payment_id=str(invoice.id),
            webhook_link=s.BILLING_CORE_WEBHOOK_INVOICE_URL,
            idempotency_key=str(invoice.id),
            minutes_to_expire=s.BILLING_CORE_CHECKOUT_EXPIRATION_MINUTES,
            items=[{
                "external_reference": str(invoice.id),
                "name": f"Assinatura {plan.name}",
                "description": "Fatura de assinatura Marketfy",
                "quantity": 1,
                "value": f"{Decimal(str(invoice.amount)):.2f}",
            }],
            success_url=f"{s.PUBLIC_FRONTEND_URL.rstrip('/')}/billing/success",
            cancel_url=f"{s.PUBLIC_FRONTEND_URL.rstrip('/')}/billing/cancel",
            expired_url=f"{s.PUBLIC_FRONTEND_URL.rstrip('/')}/billing/expired",
        )
        await self._inv.update_checkout(invoice.id, bc_job_id=result.get("job_id"))
        invoice.bc_job_id = result.get("job_id")

    async def refresh_checkout(self, invoice_id: uuid.UUID) -> Dict[str, Any]:
        invoice = await self._inv.get_by_id(invoice_id)
        if invoice is None:
            return {"status": "not_found", "checkout_url": None}
        if invoice.checkout_url:
            return {"status": "ready", "checkout_url": invoice.checkout_url}
        if not invoice.bc_job_id:
            return {"status": "pending", "checkout_url": None}
        job = await self._bc.get_job(invoice.bc_job_id)
        result = job.get("result") or {}
        checkout_url = result.get("checkout_url") or job.get("checkout_url")
        bc_payment_id = result.get("payment_id") or job.get("payment_id")
        if checkout_url or bc_payment_id:
            await self._inv.update_checkout(invoice_id, checkout_url=checkout_url, bc_payment_id=bc_payment_id)
        return {"status": job.get("status", "processing"), "checkout_url": checkout_url}

    # -- Ativação (webhook) ------------------------------------------------
    async def activate_invoice(self, invoice_id: uuid.UUID, bc_payment_id: str,
                               payment_data: Dict[str, Any]) -> None:
        invoice = await self._inv.get_by_id(invoice_id)
        if invoice is None:
            logger.warning("invoice_not_found", extra={"extra_data": {"invoice_id": str(invoice_id)}})
            return
        rows = await self._inv.mark_paid(invoice_id, bc_payment_id=bc_payment_id, paid_at=datetime.utcnow())
        if rows == 0:
            logger.info("invoice_already_paid", extra={"extra_data": {"invoice_id": str(invoice_id)}})
            return
        sub = await self._sub.get_by_id(invoice.subscription_id)
        if sub is not None:
            sub.status = "active"
            sub.expires_at = invoice.period_end
            sub.last_event_at = datetime.utcnow()
            await self._sub.save(sub)
        logger.info("invoice_activated", extra={"extra_data": {
            "invoice_id": str(invoice_id), "subscription_id": str(invoice.subscription_id)}})

    async def mark_invoice_failed(self, invoice_id: uuid.UUID, reason: str = "") -> None:
        await self._inv.mark_status(invoice_id, "canceled")
        logger.info("invoice_failed", extra={"extra_data": {"invoice_id": str(invoice_id), "reason": reason}})

    # -- Próxima fatura (worker) ------------------------------------------
    async def generate_next_invoice(self, subscription):
        """Gera a próxima fatura de uma assinatura invoice ativa, se necessário."""
        open_inv = await self._inv.get_open_invoice_for_subscription(subscription.id)
        if open_inv is not None:
            return None  # já existe fatura pendente
        plan = await self._plan.get_by_id(subscription.plan_id)
        if plan is None:
            return None
        period_start = subscription.expires_at or datetime.utcnow()
        idem = f"inv-{subscription.id}-{period_start.strftime('%Y%m%d')}"
        invoice = await self._create_invoice(
            owner_id=subscription.owner_id, subscription=subscription, plan=plan,
            period_start=period_start, due_date=period_start, idempotency_key=idem,
        )
        await self._create_checkout(invoice, plan)
        await self.refresh_checkout(invoice.id)
        return invoice
