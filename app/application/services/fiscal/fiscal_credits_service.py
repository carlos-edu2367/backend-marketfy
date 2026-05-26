from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from domain.fiscal import (
    CreditsBalance,
    EMISSION_PACKAGES,
    EmissionCreditPackage,
    FiscalEmissionPackage,
    NotificationSeverity,
    NotificationType,
    PackageHistoryItem,
    PurchaseInitResult,
)
from infra.config.logger import get_logger

logger = get_logger("fiscal_credits_service")


class FiscalCreditsService:
    def __init__(
        self,
        *,
        credits_repo,
        mp_client,
        quota_service,
        settings,
        quota_repo=None,
        notification_service=None,
        audit_service=None,
        plan_access_service=None,
    ):
        self.credits_repo = credits_repo
        self.mp_client = mp_client
        self.quota_service = quota_service
        self.quota_repo = quota_repo or getattr(quota_service, "repo", None)
        self.notification_service = notification_service
        self.audit_service = audit_service
        self.settings = settings
        self.plan_access_service = plan_access_service

    def get_packages(self) -> list[EmissionCreditPackage]:
        return list(EMISSION_PACKAGES.values())

    async def initiate_purchase(
        self,
        owner_id: uuid.UUID,
        market_id: uuid.UUID,
        package_slug: str,
        idempotency_key: str,
    ) -> PurchaseInitResult:
        package = EMISSION_PACKAGES.get(package_slug)
        if not package:
            raise ValueError(f"Pacote invalido: {package_slug}")

        existing = await self.credits_repo.get_package_by_external_ref(idempotency_key)
        if existing and existing.mp_preference_id:
            return PurchaseInitResult(
                package_id=existing.id,
                init_point=self._checkout_url_from_preference_id(existing.mp_preference_id),
                package=package,
            )

        db_package = existing or await self.credits_repo.create_package(
            owner_id=owner_id,
            market_id=market_id,
            package_slug=package_slug,
            quantity=package.emission_count,
            remaining=package.emission_count,
            price_gross=package.price_gross,
            price_net_target=package.price_net_target,
            payment_status="pending",
            mp_external_reference=idempotency_key,
        )

        payload = self._preference_payload(db_package, package)
        mp_response = await self.mp_client.create_preference(payload)
        await self.credits_repo.update_package_preference(
            package_id=db_package.id,
            mp_preference_id=mp_response["id"],
        )

        init_point = (
            mp_response.get("sandbox_init_point")
            if getattr(self.settings, "MP_SANDBOX", True)
            else mp_response.get("init_point")
        )
        return PurchaseInitResult(
            package_id=db_package.id,
            init_point=init_point or self._checkout_url_from_preference_id(mp_response["id"]),
            package=package,
        )

    async def get_credits_history(
        self,
        owner_id: uuid.UUID,
        page: int = 1,
        per_page: int = 20,
    ) -> list[PackageHistoryItem]:
        offset = max(page - 1, 0) * per_page
        packages = await self.credits_repo.list_packages_by_owner(
            owner_id,
            limit=min(per_page, 100),
            offset=offset,
        )
        return [PackageHistoryItem.from_package(package) for package in packages]

    async def get_credits_balance(self, owner_id: uuid.UUID, period: Optional[str] = None) -> CreditsBalance:
        period = period or datetime.utcnow().strftime("%Y%m")
        fallback_included_limit = 0
        if self.plan_access_service:
            fallback_included_limit = await self.plan_access_service.get_fiscal_monthly_limit(owner_id)
        quota = await self.quota_service.get_quota_status(
            owner_id, period, fallback_included_limit=fallback_included_limit
        )
        return CreditsBalance(
            period=quota.period,
            included_limit=quota.included_limit,
            addon_limit=quota.addon_limit,
            used_count=quota.used_count,
            reserved_count=quota.reserved_count,
            remaining=quota.remaining,
            percentage_used=quota.percentage_used,
        )

    async def activate_package(
        self,
        package_id: uuid.UUID,
        mp_payment_id: str,
        payment_data: dict,
    ) -> None:
        package = await self.credits_repo.get_package(package_id)
        if not package:
            logger.error(
                "fiscal_credit_package_not_found",
                extra={"extra_data": {"package_id": str(package_id), "mp_payment_id": mp_payment_id}},
            )
            return

        ledger_key = f"mp_payment:{mp_payment_id}"
        if self.quota_repo:
            existing_ledger = await self.quota_repo.get_ledger_entry_by_idempotency(ledger_key)
            if existing_ledger:
                logger.info(
                    "fiscal_credit_package_already_activated",
                    extra={"extra_data": {"package_id": str(package_id), "mp_payment_id": mp_payment_id}},
                )
                return

        if package.payment_status != "pending":
            logger.warning(
                "fiscal_credit_package_unexpected_status",
                extra={"extra_data": {"package_id": str(package_id), "status": package.payment_status}},
            )
            return

        now = datetime.utcnow()
        valid_until = now + timedelta(days=365)
        await self.credits_repo.activate_package(
            package_id=package_id,
            mp_payment_id=mp_payment_id,
            payment_status="paid",
            valid_from=now,
            valid_until=valid_until,
        )
        await self.quota_service.add_addon_credits(
            owner_id=package.owner_id,
            period=now.strftime("%Y%m"),
            amount=package.quantity,
            idempotency_key=ledger_key,
        )
        await self._record_activation_audit(package, mp_payment_id)
        await self._notify_activation(package, mp_payment_id, valid_until)

        logger.info(
            "fiscal_credit_package_activated",
            extra={"extra_data": {
                "package_id": str(package_id),
                "owner_id": str(package.owner_id),
                "quantity": package.quantity,
            }},
        )

    async def mark_package_failed(self, package_id: uuid.UUID, reason: str = "") -> None:
        await self.credits_repo.mark_package_failed(package_id, reason=reason)

    def _preference_payload(self, db_package: FiscalEmissionPackage, package: EmissionCreditPackage) -> dict:
        api_base = (getattr(self.settings, "PUBLIC_API_BASE_URL", "") or "").rstrip("/")
        return {
            "items": [{
                "id": package.slug,
                "title": f"Creditos NFC-e - {package.emission_count} emissoes",
                "description": f"Pacote de {package.emission_count} emissoes extras para NFC-e",
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": float(package.price_gross),
            }],
            "external_reference": str(db_package.id),
            "notification_url": f"{api_base}/webhooks/mercado-pago",
            "back_urls": {
                "success": getattr(self.settings, "FISCAL_CREDITS_BACK_URL_SUCCESS", ""),
                "failure": getattr(self.settings, "FISCAL_CREDITS_BACK_URL_FAILURE", ""),
                "pending": getattr(self.settings, "FISCAL_CREDITS_BACK_URL_PENDING", ""),
            },
            "auto_return": "approved",
            "statement_descriptor": "MARKETFY",
            "expires": True,
            "expiration_date_to": (datetime.utcnow() + timedelta(hours=24)).isoformat() + "Z",
        }

    def _checkout_url_from_preference_id(self, preference_id: str) -> str:
        if getattr(self.settings, "MP_SANDBOX", True):
            return f"https://sandbox.mercadopago.com.br/checkout/v1/redirect?pref_id={preference_id}"
        return f"https://www.mercadopago.com.br/checkout/v1/redirect?pref_id={preference_id}"

    async def _record_activation_audit(self, package: FiscalEmissionPackage, mp_payment_id: str) -> None:
        if not self.audit_service:
            return
        record = getattr(self.audit_service, "record", None)
        if not record:
            return
        await record(
            action="fiscal_credits_activated",
            resource_type="fiscal_emission_package",
            resource_id=str(package.id),
            result="success",
            market_id=package.purchased_at_market_id or package.market_id,
            metadata={
                "mp_payment_id": mp_payment_id,
                "quantity": package.quantity,
                "package_slug": package.package_slug,
                "price_gross": str(package.price_gross),
            },
        )

    async def _notify_activation(
        self,
        package: FiscalEmissionPackage,
        mp_payment_id: str,
        valid_until: datetime,
    ) -> None:
        if not self.notification_service:
            return
        create_notification = getattr(self.notification_service, "create_notification", None)
        if create_notification:
            await create_notification(
                owner_id=package.owner_id,
                notification_type="credits_activated",
                severity="info",
                title=f"{package.quantity} creditos de emissao adicionados",
                message=(
                    f"Seu pacote de {package.quantity} emissoes NFC-e foi ativado com sucesso. "
                    f"Valido ate {valid_until.strftime('%d/%m/%Y')}."
                ),
                dedupe_key=f"credits_activated:{mp_payment_id}",
            )
            return
        send = getattr(self.notification_service, "_send", None)
        if send:
            await send(
                owner_id=package.owner_id,
                market_id=package.purchased_at_market_id or package.market_id,
                notification_type=NotificationType.ADDON_PURCHASED,
                severity=NotificationSeverity.INFO,
                title=f"{package.quantity} creditos de emissao adicionados",
                message=(
                    f"Seu pacote de {package.quantity} emissoes NFC-e foi ativado com sucesso. "
                    f"Valido ate {valid_until.strftime('%d/%m/%Y')}."
                ),
                dedupe_key=f"credits_activated:{mp_payment_id}",
            )
