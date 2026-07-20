"""RecurringService — assinatura recorrente (cartão) via billing core."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict

from infra.config.logger import get_logger

logger = get_logger("recurring_service")

CYCLE_MAP = {"monthly": "MONTHLY", "semiannual": "SEMIANNUALLY", "annual": "YEARLY"}
PERIOD_DAYS = {"monthly": 30, "semiannual": 180, "annual": 365}


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
    def __init__(self, subscription_repo, plan_repo, user_repo, billing_client, settings):
        self._sub = subscription_repo
        self._plan = plan_repo
        self._user = user_repo
        self._bc = billing_client
        self._settings = settings

    async def contract(self, user, plan_id: uuid.UUID, subscription_type: str,
                       document: str, idempotency_key: str) -> Dict[str, Any]:
        if subscription_type not in CYCLE_MAP:
            raise ValueError("subscription_type inválido para recorrente.")
        doc = _only_digits(document)
        if len(doc) not in (11, 14):
            raise ValueError("Documento inválido. Informe um CPF (11) ou CNPJ (14 dígitos).")

        existing = await self._sub.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return {"subscription_id": str(existing.id), "job_id": existing.billing_job_id,
                    "checkout_url": getattr(existing, "checkout_url", None)}

        plan = await self._plan.get_by_id(plan_id)
        if plan is None or not plan.is_active:
            raise ValueError("Plano não disponível.")

        customer_provider_id = await self._ensure_customer(user, doc)

        value = _price(plan, subscription_type)
        expires_at = datetime.utcnow() + timedelta(days=365 * 5)  # validade longa; billing controla ciclo
        webhook_link = self._webhook_link()

        job = await self._bc.create_subscription(
            system_sub_id=str(user.id),
            customer_provider_id=customer_provider_id,
            description=f"Assinatura Marketfy {plan.name}",
            value=float(value),
            subscription_type=CYCLE_MAP[subscription_type],
            expires_at=expires_at,
            webhook_link=webhook_link,
            idempotency_key=idempotency_key,
        )
        job_id = job.get("job_id")

        checkout_url, billing_sub_id = await self._poll_subscription_job(job_id)

        from infra.database.models import BillingSubscriptionModel
        sub = BillingSubscriptionModel(
            owner_id=user.id, plan_id=plan_id,
            billing_system=self._settings.BILLING_CORE_SYSTEM,
            billing_system_sub_id=str(user.id),
            billing_mode="recurring",
            billing_subscription_id=billing_sub_id,
            billing_job_id=job_id,
            customer_provider_id=customer_provider_id,
            status="pending",
            subscription_type=subscription_type,
            value=value,
            expires_at=None,
            idempotency_key=idempotency_key,
        )
        sub = await self._sub.save(sub)

        return {"subscription_id": str(sub.id), "job_id": job_id, "checkout_url": checkout_url}

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

    async def _poll_subscription_job(self, job_id: str) -> tuple[str | None, str | None]:
        if not job_id:
            return None, None
        job = await self._bc.get_job(job_id)
        result = job.get("result") or {}
        checkout_url = result.get("checkout_url") or job.get("checkout_url")
        billing_sub_id = result.get("subscription_id") or job.get("subscription_id")
        return checkout_url, billing_sub_id

    def _webhook_link(self) -> str:
        host = self._settings.BILLING_CORE_WEBHOOK_HOST or "http://localhost:8000"
        return f"{host.rstrip('/')}/api/v1/billing/webhooks/internal"
