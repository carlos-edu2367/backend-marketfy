"""RecurringService — assinatura recorrente (cartão) via billing core."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict

from domain.billing_periods import CYCLE_MAP, PERIOD_DAYS
from infra.config.logger import get_logger
from infra.observability.analytics import PostHogClient

logger = get_logger("recurring_service")


def _price(plan, subscription_type: str) -> Decimal:
    mapping = {
        "monthly": getattr(plan, "price_monthly", 0) or 0,
        "semiannual": getattr(plan, "price_180days", 0) or 0,
        "annual": getattr(plan, "price_annual", 0) or 0,
    }
    return Decimal(str(mapping.get(subscription_type, mapping["monthly"])))


def _only_digits(doc: str) -> str:
    return re.sub(r"\D", "", doc or "")


class RecurringService:
    def __init__(self, subscription_repo, plan_repo, user_repo, billing_client, settings, analytics=None):
        self._sub = subscription_repo
        self._plan = plan_repo
        self._user = user_repo
        self._bc = billing_client
        self._settings = settings
        self._analytics = analytics or PostHogClient()

    async def contract(self, user, plan_id: uuid.UUID, subscription_type: str,
                       document: str, idempotency_key: str) -> Dict[str, Any]:
        if subscription_type not in CYCLE_MAP:
            raise ValueError("subscription_type inválido para recorrente.")
        doc = _only_digits(document)
        if len(doc) not in (11, 14):
            raise ValueError("Documento inválido. Informe um CPF (11) ou CNPJ (14 dígitos).")

        existing = await self._sub.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return {"subscription_id": str(existing.id), "job_id": existing.billing_job_id}

        plan = await self._plan.get_by_id(plan_id)
        if plan is None or not plan.is_active:
            raise ValueError("Plano não disponível.")

        customer_provider_id = await self._ensure_customer(user, doc)

        value = _price(plan, subscription_type)
        webhook_link = self._webhook_link()

        from infra.database.models import BillingSubscriptionModel
        sub = BillingSubscriptionModel(
            owner_id=user.id, plan_id=plan_id,
            billing_system=self._settings.BILLING_CORE_SYSTEM,
            billing_mode="recurring",
            customer_provider_id=customer_provider_id,
            status="pending",
            subscription_type=subscription_type,
            value=value,
            expires_at=None,
            idempotency_key=idempotency_key,
        )
        sub = await self._sub.save(sub)
        sub.billing_system_sub_id = str(sub.id)

        job = await self._bc.create_subscription(
            system_sub_id=str(sub.id),
            customer_provider_id=customer_provider_id,
            description=f"Marketfy {plan.name}",
            value=float(value),
            subscription_type=CYCLE_MAP[subscription_type],
            expires_at=datetime.utcnow() + timedelta(days=365 * 5),  # teto do billing-core; a validade real e local
            webhook_link=webhook_link,
            idempotency_key=f"bc-sub-{sub.id}",
            back_url=self._back_url(sub.id),
        )
        sub.billing_job_id = job.get("job_id")
        sub = await self._sub.save(sub)

        await self._analytics.track_event(
            str(user.id), "subscription_created",
            {"plan_id": str(plan_id), "subscription_type": subscription_type, "billing_mode": "recurring"},
        )

        return {"subscription_id": str(sub.id), "job_id": sub.billing_job_id}

    def _back_url(self, local_subscription_id) -> str | None:
        base = getattr(self._settings, "PUBLIC_FRONTEND_URL", None)
        if not base:
            return None
        return f"{base.rstrip('/')}/billing/retorno?tipo=subscription&ref={local_subscription_id}"

    async def ensure_checkout(self, local_subscription_id) -> Dict[str, Any]:
        """Consulta o job do billing-core e persiste o checkout_url quando pronto.

        Chamado pelo endpoint POST /billing/subscriptions/{id}/checkout, com o
        mesmo padrao de polling que InvoiceService.refresh_checkout ja usa.
        """
        sub = await self._sub.get_by_id(local_subscription_id)
        if sub is None:
            return {"status": "not_found", "checkout_url": None}
        if sub.checkout_url:
            return {"status": "completed", "checkout_url": sub.checkout_url}
        if not sub.billing_job_id:
            return {"status": "pending", "checkout_url": None}

        job = await self._bc.get_job(sub.billing_job_id)
        job_status = job.get("status", "processing")
        if job_status != "completed":
            return {"status": job_status, "checkout_url": None}

        result = job.get("result") or {}
        checkout_url = result.get("checkout_url")
        billing_subscription_id = result.get("subscription_id")
        if checkout_url:
            sub.checkout_url = checkout_url
        if billing_subscription_id:
            sub.billing_subscription_id = billing_subscription_id
        if checkout_url or billing_subscription_id:
            await self._sub.save(sub)

        return {"status": "completed", "checkout_url": checkout_url}

    async def _ensure_customer(self, user, doc: str) -> str:
        if getattr(user, "asaas_customer_id", None):
            return user.asaas_customer_id
        email = user.email.value if hasattr(user.email, "value") else user.email
        kwargs = {"cpf": doc} if len(doc) == 11 else {"cnpj": doc}
        result = await self._bc.create_customer(
            nome_completo=getattr(user, "full_name", getattr(user, "name", "")),
            email=email,
            system_customer_id=str(user.id),
            system=self._settings.BILLING_CORE_SYSTEM,
            **kwargs,
        )
        provider_id = result["provider_customer_id"]
        await self._user.update_asaas_customer_id(user.id, provider_id)
        return provider_id

    def _webhook_link(self) -> str:
        host = self._settings.BILLING_CORE_WEBHOOK_HOST or "http://localhost:8000"
        return f"{host.rstrip('/')}/api/v1/billing/webhooks/internal"
