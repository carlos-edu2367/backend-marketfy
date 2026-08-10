from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.exc import IntegrityError

from domain.fiscal import (
    CreditsBalance,
    EMISSION_PACKAGES,
    EmissionCreditPackage,
    FiscalEmissionPackage,
    GRANT_REASON_LABELS,
    GrantResult,
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

        result = await self.bc_client.create_payment(
            value=f"{package.price_gross:.2f}",
            description=f"Creditos NF-e - {package.slug}",
            system=self.settings.BILLING_CORE_SYSTEM,
            system_payment_id=str(db_package.id),
            webhook_link=self.settings.BILLING_CORE_WEBHOOK_CALLBACK_URL,
            idempotency_key=str(db_package.id),
            minutes_to_expire=self.settings.BILLING_CORE_CHECKOUT_EXPIRATION_MINUTES,
            items=[{
                "external_reference": str(db_package.id),
                "name": f"{package.emission_count} créditos NF-e",
                "description": "Créditos para emissão fiscal",
                "quantity": 1,
                "value": f"{package.price_gross:.2f}",
            }],
            success_url=f"{self.settings.PUBLIC_FRONTEND_URL.rstrip('/')}/billing/success",
            cancel_url=f"{self.settings.PUBLIC_FRONTEND_URL.rstrip('/')}/billing/cancel",
            expired_url=f"{self.settings.PUBLIC_FRONTEND_URL.rstrip('/')}/billing/expired",
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

    async def _resolve_included_limit(self, owner_id: uuid.UUID) -> int:
        """Limite mensal incluso no plano do owner.

        Precisa ser passado a add_addon_credits: se o counter do período ainda
        não existe, ele é criado com este valor. Passar 0 aqui apagaria a
        franquia do plano do usuário até o reset mensal.
        """
        if not self.plan_access_service:
            return 0
        return await self.plan_access_service.get_fiscal_monthly_limit(owner_id)

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

        included_limit = await self._resolve_included_limit(package.owner_id)
        await self.quota_service.add_addon_credits(
            owner_id=package.owner_id,
            period=now.strftime("%Y%m"),
            amount=package.quantity,
            idempotency_key=ledger_key,
            included_limit=included_limit,
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

    async def grant_credits(
        self,
        *,
        owner_id: uuid.UUID,
        amount: int,
        reason_code: str,
        granted_by_id: uuid.UUID,
        idempotency_key: str,
        note: Optional[str] = None,
        valid_days: int = 365,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> GrantResult:
        """Concede créditos NFC-e a um owner, sem cobrança.

        Cria um pacote pago de verdade — é o que faz o crédito sobreviver ao
        reset mensal, ser debitado em FIFO e aparecer no histórico do usuário.

        Idempotente em duas camadas: o pré-check por bc_idempotency_key e a
        unique constraint como backstop de corrida.
        """
        existing = await self.credits_repo.get_package_by_idempotency_key(idempotency_key)
        if existing:
            return GrantResult(package=existing, created=False)

        now = datetime.utcnow()
        valid_until = now + timedelta(days=valid_days)

        try:
            package = await self.credits_repo.create_grant_package(
                owner_id=owner_id,
                quantity=amount,
                valid_from=now,
                valid_until=valid_until,
                grant_reason_code=reason_code,
                grant_note=note,
                granted_by_id=granted_by_id,
                idempotency_key=idempotency_key,
                commit=False,
            )
        except IntegrityError:
            # Corrida com um request concorrente de mesma chave.
            await self.credits_repo.session.rollback()
            existing = await self.credits_repo.get_package_by_idempotency_key(idempotency_key)
            if existing:
                return GrantResult(package=existing, created=False)
            raise

        included_limit = await self._resolve_included_limit(owner_id)
        await self.quota_service.add_addon_credits(
            owner_id=owner_id,
            period=now.strftime("%Y%m"),
            amount=amount,
            idempotency_key=f"admin_grant:{package.id}",
            included_limit=included_limit,
            commit=False,
        )

        await self._record_grant_audit(
            package,
            granted_by_id=granted_by_id,
            amount=amount,
            reason_code=reason_code,
            note=note,
            valid_until=valid_until,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        await self.credits_repo.session.commit()

        # Notificação fora da transação: uma falha aqui não pode derrubar a
        # concessão já persistida.
        await self._notify_grant(package, amount=amount, reason_code=reason_code, valid_until=valid_until)

        logger.info(
            "fiscal_credits_granted",
            extra={"extra_data": {
                "package_id": str(package.id),
                "owner_id": str(owner_id),
                "amount": amount,
                "reason_code": reason_code,
                "granted_by_id": str(granted_by_id),
                "valid_until": valid_until.isoformat(),
            }},
        )
        return GrantResult(package=package, created=True)

    async def _record_grant_audit(
        self,
        package: FiscalEmissionPackage,
        *,
        granted_by_id: uuid.UUID,
        amount: int,
        reason_code: str,
        note: Optional[str],
        valid_until: datetime,
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> None:
        if not self.audit_service:
            return
        record = getattr(self.audit_service, "record", None)
        if not record:
            return
        await record(
            action="admin_fiscal_credits_granted",
            resource_type="fiscal_emission_package",
            resource_id=str(package.id),
            result="success",
            actor_user_id=granted_by_id,
            actor_role="admin",
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={
                "owner_id": str(package.owner_id),
                "amount": amount,
                "reason_code": reason_code,
                "note": note,
                "valid_until": valid_until.isoformat(),
            },
            commit=False,
        )

    async def _notify_grant(
        self,
        package: FiscalEmissionPackage,
        *,
        amount: int,
        reason_code: str,
        valid_until: datetime,
    ) -> None:
        if not self.notification_service:
            return
        create_notification = getattr(self.notification_service, "create_notification", None)
        if not create_notification:
            return
        label = GRANT_REASON_LABELS.get(reason_code, "Créditos concedidos pelo Marketfy")
        await create_notification(
            owner_id=package.owner_id,
            notification_type="credits_activated",
            severity="info",
            title=f"{amount} créditos NFC-e adicionados",
            message=(
                f"{label}. Você recebeu {amount} emissões NFC-e para usar em qualquer "
                f"uma das suas lojas. Válido até {valid_until.strftime('%d/%m/%Y')}."
            ),
            dedupe_key=f"admin_grant:{package.id}",
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

        result = await self.bc_client.create_payment(
            value=f"{price_gross:.2f}",
            description=f"Creditos NF-e - {package_slug}",
            system=self.settings.BILLING_CORE_SYSTEM,
            system_payment_id=str(db_package.id),
            webhook_link=self.settings.BILLING_CORE_WEBHOOK_CALLBACK_URL,
            idempotency_key=str(db_package.id),
            minutes_to_expire=self.settings.BILLING_CORE_CHECKOUT_EXPIRATION_MINUTES,
            items=[{
                "external_reference": str(db_package.id),
                "name": f"{quantity} créditos NF-e",
                "description": "Créditos para emissão fiscal",
                "quantity": 1,
                "value": f"{price_gross:.2f}",
            }],
            success_url=f"{self.settings.PUBLIC_FRONTEND_URL.rstrip('/')}/billing/success",
            cancel_url=f"{self.settings.PUBLIC_FRONTEND_URL.rstrip('/')}/billing/cancel",
            expired_url=f"{self.settings.PUBLIC_FRONTEND_URL.rstrip('/')}/billing/expired",
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
        DEPRECATED para o fluxo de creditos fiscais.

        Usar apenas para assinaturas recorrentes ou fluxos legados que ainda
        chamam endpoints do Billing Core que exigem customer_provider_id.
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
