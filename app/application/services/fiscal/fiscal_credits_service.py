from __future__ import annotations

import uuid
from datetime import datetime, timedelta, date
from decimal import Decimal
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
        mp_client=None,
        quota_service,
        settings,
        quota_repo=None,
        notification_service=None,
        audit_service=None,
        plan_access_service=None,
        bc_client=None,
        user_repo=None,
    ):
        self.credits_repo = credits_repo
        self.mp_client = mp_client
        self.quota_service = quota_service
        self.quota_repo = quota_repo or getattr(quota_service, "repo", None)
        self.notification_service = notification_service
        self.audit_service = audit_service
        self.settings = settings
        self.plan_access_service = plan_access_service
        self.bc_client = bc_client
        self.user_repo = user_repo

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

        existing = await self.credits_repo.get_package_by_idempotency_key(idempotency_key)
        if existing and existing.bc_job_id:
            return PurchaseInitResult(
                package_id=existing.id,
                init_point="",
                package=package,
                job_id=existing.bc_job_id,
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
            bc_idempotency_key=idempotency_key,
        )

        user = await self.user_repo.get_by_id(owner_id)
        if not user:
            raise ValueError(f"Usuario nao encontrado: {owner_id}")

        customer_provider_id = await self._ensure_customer_provider_id(user)

        due_date = (
            date.today()
            + timedelta(days=self.settings.BILLING_CORE_PAYMENT_DUE_DAYS)
        ).isoformat()

        result = await self.bc_client.create_payment(
            customer_provider_id=customer_provider_id,
            value=str(package.price_gross),
            billing_type="UNDEFINED",
            due_date=due_date,
            description=f"Créditos NF-e — {package.slug}",
            system=self.settings.BILLING_CORE_SYSTEM,
            system_payment_id=str(db_package.id),
            webhook_link=self.settings.BILLING_CORE_WEBHOOK_CALLBACK_URL,
            idempotency_key=str(db_package.id),
        )

        await self.credits_repo.update_package_job_id(
            db_package.id,
            job_id=result["job_id"],
        )

        return PurchaseInitResult(
            package_id=db_package.id,
            init_point="",
            package=package,
            job_id=result["job_id"],
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
            addon_total=quota.addon_total,
        )

    async def activate_package(
        self,
        package_id: uuid.UUID,
        bc_payment_id: str,
        payment_data: dict,
    ) -> None:
        package = await self.credits_repo.get_package(package_id)
        if not package:
            logger.error(
                "fiscal_credit_package_not_found",
                extra={"extra_data": {"package_id": str(package_id), "bc_payment_id": bc_payment_id}},
            )
            return

        ledger_key = f"bc_payment:{bc_payment_id}"
        if self.quota_repo:
            existing_ledger = await self.quota_repo.get_ledger_entry_by_idempotency(ledger_key)
            if existing_ledger:
                logger.info(
                    "fiscal_credit_package_already_activated",
                    extra={"extra_data": {"package_id": str(package_id), "bc_payment_id": bc_payment_id}},
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
        rows_updated = await self.credits_repo.activate_package(
            package_id=package_id,
            bc_payment_id=bc_payment_id,
            payment_status="paid",
            valid_from=now,
            valid_until=valid_until,
            commit=False,
        )
        
        if rows_updated == 0:
            logger.info(
                "fiscal_credit_package_already_activated",
                extra={"extra_data": {"package_id": str(package_id), "bc_payment_id": bc_payment_id}},
            )
            return

        await self.quota_service.add_addon_credits(
            owner_id=package.owner_id,
            period=now.strftime("%Y%m"),
            amount=package.quantity,
            idempotency_key=ledger_key,
            included_limit=0,
            commit=False,
        )
        await self._record_activation_audit(package, bc_payment_id, commit=False)
        
        await self.credits_repo.session.commit()
        
        await self._notify_activation(package, bc_payment_id, valid_until)
        
        logger.info(
            "fiscal_credit_package_activated",
            extra={"extra_data": {
                "package_id": str(package_id),
                "owner_id": str(package.owner_id),
                "quantity": package.quantity,
            }},
        )

    async def initiate_custom_purchase(
        self,
        owner_id: uuid.UUID,
        market_id: uuid.UUID,
        quantity: int,
        price_gross: Decimal,
        idempotency_key: str,
    ) -> PurchaseInitResult:
        package_slug = f"custom_{quantity}"
        custom_package = EmissionCreditPackage(
            slug=package_slug,
            emission_count=quantity,
            price_gross=price_gross,
            price_net_target=price_gross,
        )

        existing = await self.credits_repo.get_package_by_idempotency_key(idempotency_key)
        if existing and existing.bc_job_id:
            return PurchaseInitResult(
                package_id=existing.id,
                init_point="",
                package=custom_package,
                job_id=existing.bc_job_id,
            )

        db_package = existing or await self.credits_repo.create_package(
            owner_id=owner_id,
            market_id=market_id,
            package_slug=package_slug,
            quantity=quantity,
            remaining=quantity,
            price_gross=price_gross,
            price_net_target=price_gross,
            payment_status="pending",
            bc_idempotency_key=idempotency_key,
        )

        user = await self.user_repo.get_by_id(owner_id)
        if not user:
            raise ValueError(f"Usuario nao encontrado: {owner_id}")

        customer_provider_id = await self._ensure_customer_provider_id(user)

        due_date = (
            date.today()
            + timedelta(days=self.settings.BILLING_CORE_PAYMENT_DUE_DAYS)
        ).isoformat()

        result = await self.bc_client.create_payment(
            customer_provider_id=customer_provider_id,
            value=str(price_gross),
            billing_type="UNDEFINED",
            due_date=due_date,
            description=f"Créditos NF-e — {package_slug}",
            system=self.settings.BILLING_CORE_SYSTEM,
            system_payment_id=str(db_package.id),
            webhook_link=self.settings.BILLING_CORE_WEBHOOK_CALLBACK_URL,
            idempotency_key=str(db_package.id),
        )

        await self.credits_repo.update_package_job_id(
            db_package.id,
            job_id=result["job_id"],
        )

        return PurchaseInitResult(
            package_id=db_package.id,
            init_point="",
            package=custom_package,
            job_id=result["job_id"],
        )


    async def mark_package_failed(self, package_id: uuid.UUID, reason: str = "") -> None:
        await self.credits_repo.mark_package_failed(package_id, reason=reason)



    async def _record_activation_audit(self, package: FiscalEmissionPackage, bc_payment_id: str, commit: bool = True) -> None:
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
                "bc_payment_id": bc_payment_id,
                "quantity": package.quantity,
                "package_slug": package.package_slug,
                "price_gross": str(package.price_gross),
            },
            commit=commit,
        )

    async def _notify_activation(
        self,
        package: FiscalEmissionPackage,
        bc_payment_id: str,
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
                dedupe_key=f"credits_activated:{bc_payment_id}",
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
                dedupe_key=f"credits_activated:{bc_payment_id}",
            )

    async def _ensure_customer_provider_id(self, user) -> str:
        """
        Garante que o usuário tenha um asaas_customer_id persistido localmente.
        Caso contrário, cria-o no Billing Core e persiste o ID retornado.
        """
        if getattr(user, "asaas_customer_id", None):
            return user.asaas_customer_id

        cpf = user.cpf.value if hasattr(user.cpf, "value") else user.cpf
        cnpj = user.cnpj.value if hasattr(user.cnpj, "value") else user.cnpj

        if not cpf and not cnpj:
            raise ValueError("customer_document_missing")

        email_val = user.email.value if hasattr(user.email, "value") else user.email
        result = await self.bc_client.create_customer(
            nome_completo=user.full_name,
            email=email_val,
            system_customer_id=str(user.id),
            system=self.settings.BILLING_CORE_SYSTEM,
            cpf=cpf or None,
            cnpj=cnpj or None,
        )

        provider_id = result["provider_customer_id"]
        await self.user_repo.update_asaas_customer_id(user.id, provider_id)
        user.asaas_customer_id = provider_id
        return provider_id

    async def get_checkout_status(self, job_id: str) -> dict:
        """
        Proxy para consultar o status do job de pagamento no Billing Core.
        Quando concluído, persiste bc_payment_id.
        """
        job = await self.bc_client.get_job(job_id)
        status = job.get("status", "processing")
        
        mapped_status = "completed" if status in ("completed", "done") else status
        
        result = job.get("result") or {}
        checkout_url = result.get("checkout_url") or job.get("checkout_url")
        bc_payment_id = result.get("payment_id") or job.get("payment_id")
        
        error_code = job.get("error_code")
        error_message = job.get("error_message")
        
        if mapped_status == "completed" and bc_payment_id:
            pkg = await self.credits_repo.get_package_by_job_id(job_id)
            if pkg:
                await self.credits_repo.update_package_payment_id(pkg.id, bc_payment_id)
                
        return {
            "status": mapped_status,
            "checkout_url": checkout_url,
            "bc_payment_id": bc_payment_id,
            "error_code": error_code,
            "error_message": error_message,
        }

