"""SubscriptionService — Fase 4 / PR 13-15.

Gerencia assinaturas locais e integração com Billing Core.

Regras:
  - Billing Core é chamado apenas neste serviço.
  - Frontend nunca recebe API key ou detalhes internos.
  - Idempotência garantida por idempotency_key antes de chamar o Billing Core.
  - Fallback seguro quando Billing Core estiver indisponível.
  - Eventos de webhook processados de forma idempotente por event_id.
  - Cache em UserModel.plan_id/plan_expiration atualizado após eventos.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from domain.identity import User, Plan, PlanType
from domain.interfaces import UserRepositoryInterface, PlanRepositoryInterface
from domain.shared import BusinessRuleException
from infra.config.logger import get_logger
from infra.config.settings import get_settings

logger = get_logger("subscription_service")
settings = get_settings()


class SubscriptionService:
    """Serviço de assinatura com integração opcional ao Billing Core."""

    def __init__(
        self,
        user_repo: UserRepositoryInterface,
        plan_repo: PlanRepositoryInterface,
        subscription_repo=None,   # SQLAlchemyBillingSubscriptionRepository
        event_repo=None,          # SQLAlchemyBillingEventRepository
        billing_client=None,      # BillingCoreClient — injetado para testabilidade
    ):
        self.user_repo = user_repo
        self.plan_repo = plan_repo
        self._sub_repo = subscription_repo
        self._event_repo = event_repo
        self._billing_client = billing_client

    # ------------------------------------------------------------------
    # Trial
    # ------------------------------------------------------------------

    async def activate_trial(self, user: User) -> Tuple[User, str]:
        """Ativa período de testes (14 dias) para o usuário.

        Cria BillingSubscriptionModel local com status 'trialing'.
        Não chama o Billing Core para trial (subscription_type=trial
        pode ser mapeado depois se o Billing Core suportar).
        """
        if user.plan_id is not None:
            raise BusinessRuleException("Usuário já possui histórico de planos.")

        plans = await self.plan_repo.list_all()
        trial_plan = next((p for p in plans if p.type == PlanType.TRIAL), None)

        if not trial_plan:
            raise BusinessRuleException("Plano Trial não configurado no sistema.")

        expires_at = datetime.utcnow() + timedelta(days=14)

        # Atualiza cache no UserModel
        user.plan_id = trial_plan.id
        user.plan_expiration = expires_at
        user.is_active = True
        updated_user = await self.user_repo.save(user)

        # Cria assinatura local
        if self._sub_repo is not None:
            await self._create_local_subscription(
                owner_id=user.id,
                plan_id=trial_plan.id,
                status="trialing",
                subscription_type="trial",
                value=0,
                expires_at=expires_at,
                idempotency_key=f"trial-{user.id}",
            )

        logger.info(f"[subscription] Trial ativado para owner={user.id} expires={expires_at}")
        return updated_user, trial_plan.name

    # ------------------------------------------------------------------
    # Iniciar assinatura via Billing Core
    # ------------------------------------------------------------------

    async def initiate_subscription(
        self,
        owner_id: uuid.UUID,
        plan_id: uuid.UUID,
        subscription_type: str,
        customer_provider_id: Optional[str],
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inicia assinatura via Billing Core com idempotência.

        1. Verifica se já existe assinatura com a mesma idempotency_key.
        2. Cria assinatura local com status 'pending'.
        3. Chama Billing Core (se habilitado) com Idempotency-Key.
        4. Atualiza job_id e billing_subscription_id.
        5. Retorna resultado ao chamador.

        Em caso de falha do Billing Core:
        - A assinatura local fica como 'pending'.
        - Não ativa o plano indevidamente.
        """
        from infra.clients.billing_core_client import BillingCoreClient, BillingCoreError
        from infra.database.models import BillingSubscriptionModel

        if idempotency_key is None:
            idempotency_key = f"sub-{owner_id}-{plan_id}-{subscription_type}"

        # Idempotência: retornar assinatura existente se já processada
        if self._sub_repo is not None:
            existing = await self._sub_repo.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                logger.info(f"[subscription] Idempotente: assinatura já existe idem_key={idempotency_key}")
                return {
                    "subscription_id": str(existing.id),
                    "billing_job_id": existing.billing_job_id,
                    "status": existing.status,
                    "idempotent": True,
                }

        plan = await self.plan_repo.get_by_id(plan_id)
        if not plan:
            raise BusinessRuleException(f"Plano {plan_id} não encontrado.")
        if not plan.is_active:
            raise BusinessRuleException("Plano não disponível para contratação.")

        # Valor conforme tipo
        value_map = {
            "monthly": float(plan.price_monthly or 0),
            "semiannual": float(plan.price_180days or 0),
            "annual": float(plan.price_annual or 0),
            "trial": 0.0,
        }
        value = value_map.get(subscription_type, float(plan.price_monthly or 0))

        # expires_at baseado no tipo
        days_map = {"trial": 14, "monthly": 30, "semiannual": 180, "annual": 365}
        expires_at = datetime.utcnow() + timedelta(days=days_map.get(subscription_type, 30))

        # Cria assinatura local com status pending
        local_sub = None
        if self._sub_repo is not None:
            from infra.database.models import BillingSubscriptionModel
            local_sub_model = BillingSubscriptionModel(
                owner_id=owner_id,
                plan_id=plan_id,
                billing_system=settings.BILLING_CORE_SYSTEM,
                billing_system_sub_id=str(owner_id),
                customer_provider_id=customer_provider_id,
                status="pending",
                subscription_type=subscription_type,
                value=value,
                expires_at=expires_at,
                idempotency_key=idempotency_key,
            )
            local_sub = await self._sub_repo.save(local_sub_model)

        # Chama Billing Core
        billing_result = {}
        billing_error = None

        client = self._billing_client or BillingCoreClient()
        webhook_link = self._build_webhook_link()

        try:
            billing_result = await client.create_subscription(
                system_sub_id=str(owner_id),
                customer_provider_id=customer_provider_id or "",
                description=f"Marketfy {plan.name} — {subscription_type}",
                value=value,
                subscription_type=subscription_type,
                expires_at=expires_at,
                webhook_link=webhook_link,
                idempotency_key=idempotency_key,
            )
            logger.info(f"[subscription] Billing Core respondeu owner={owner_id} result_keys={list(billing_result.keys())}")
        except BillingCoreError as exc:
            billing_error = str(exc)
            logger.warning(f"[subscription] Billing Core indisponível owner={owner_id}: {exc}")
        except Exception as exc:
            billing_error = str(exc)
            logger.error(f"[subscription] Erro inesperado owner={owner_id}: {exc}")

        # Atualiza assinatura local com dados do Billing Core
        if local_sub is not None and billing_result:
            local_sub.billing_job_id = billing_result.get("job_id")
            local_sub.billing_subscription_id = billing_result.get("subscription_id")
            local_sub.last_event_at = datetime.utcnow()
            await self._sub_repo.save(local_sub)

        response = {
            "subscription_id": str(local_sub.id) if local_sub else None,
            "billing_job_id": billing_result.get("job_id"),
            "status": "pending",
            "idempotent": False,
        }

        if billing_error:
            response["billing_error"] = "Billing Core indisponível. Assinatura criada localmente como pendente."

        return response

    # ------------------------------------------------------------------
    # Processamento de webhook (idempotente)
    # ------------------------------------------------------------------

    async def process_webhook_event(
        self,
        event_id: str,
        event_type: str,
        system: Optional[str],
        system_sub_id: Optional[str],
        billing_subscription_id: Optional[str],
        status_from_event: Optional[str],
        expires_at: Optional[datetime],
        raw_payload: Dict[str, Any],
    ) -> Dict[str, str]:
        """Processa evento do Billing Core de forma idempotente.

        1. Verifica se event_id já foi processado.
        2. Persiste evento bruto.
        3. Atualiza BillingSubscriptionModel e cache UserModel.
        4. Retorna resultado de processamento.
        """
        from infra.database.models import BillingEventModel

        if self._event_repo is None:
            raise BusinessRuleException("Repositório de eventos de billing não configurado.")

        # Idempotência
        existing_event = await self._event_repo.get_by_event_id(event_id)
        if existing_event is not None:
            logger.info(f"[webhook] Evento duplicado ignorado event_id={event_id}")
            return {"result": "duplicate", "event_id": event_id}

        # Persiste evento bruto
        event_model = BillingEventModel(
            event_id=event_id,
            event_type=event_type,
            idempotency_key=event_id,
            raw_payload=json.dumps(raw_payload, default=str),
            processing_status="received",
        )

        # Tenta localizar a assinatura
        owner_id = None
        if system_sub_id:
            try:
                owner_id = uuid.UUID(system_sub_id)
            except (ValueError, AttributeError):
                pass

        local_sub = None
        if self._sub_repo and billing_subscription_id:
            local_sub = await self._sub_repo.get_by_billing_subscription_id(billing_subscription_id)
        if local_sub is None and self._sub_repo and owner_id:
            local_sub = await self._sub_repo.get_active_by_owner(owner_id)

        if local_sub:
            event_model.subscription_id = local_sub.id
            event_model.owner_id = local_sub.owner_id
            owner_id = local_sub.owner_id

        await self._event_repo.save(event_model)

        # Processa o evento
        try:
            await self._apply_event(
                event_type=event_type,
                local_sub=local_sub,
                owner_id=owner_id,
                status_from_event=status_from_event,
                expires_at=expires_at,
                billing_subscription_id=billing_subscription_id,
            )
            event_model.processing_status = "processed"
            event_model.processed_at = datetime.utcnow()
        except Exception as exc:
            event_model.processing_status = "failed"
            event_model.processing_error = str(exc)[:500]
            logger.error(f"[webhook] Falha ao processar event_id={event_id}: {exc}")

        await self._event_repo.save(event_model)

        return {
            "result": event_model.processing_status,
            "event_id": event_id,
            "event_type": event_type,
        }

    async def process_recurring_event(
        self,
        event: str,
        billing_subscription_id: Optional[str],
        subscription_expires_at: Optional[datetime],
        payment_date: Optional[datetime],
        raw_payload: Dict[str, Any],
    ) -> Dict[str, str]:
        """Processa o webhook interno de assinatura do billing core.

        Payload real: {event, subscription_id, subscription_expires_at, payment_date}.
        event_id é sintetizado para idempotência.
        """
        from infra.database.models import BillingEventModel

        if self._event_repo is None:
            raise BusinessRuleException("Repositório de eventos de billing não configurado.")

        pd = payment_date.isoformat() if payment_date else "none"
        event_id = f"{billing_subscription_id}:{event}:{pd}"

        existing = await self._event_repo.get_by_event_id(event_id)
        if existing is not None:
            return {"result": "duplicate", "event_id": event_id}

        local_sub = None
        if billing_subscription_id:
            local_sub = await self._sub_repo.get_by_billing_subscription_id(billing_subscription_id)

        event_model = BillingEventModel(
            event_id=event_id, event_type=event, idempotency_key=event_id,
            raw_payload=json.dumps(raw_payload, default=str), processing_status="received",
        )
        if local_sub is not None:
            event_model.subscription_id = local_sub.id
            event_model.owner_id = local_sub.owner_id
        await self._event_repo.save(event_model)

        status_map = {
            "PAYMENT_RECEIVED": "active",
            "PAYMENT_REFUNDED": None,          # não altera acesso automaticamente
            "SUBSCRIPTION_INACTIVATED": "canceled",
        }
        new_status = status_map.get(event, None)

        try:
            if local_sub is not None and new_status is not None:
                local_sub.status = new_status
                local_sub.last_event_at = datetime.utcnow()
                if subscription_expires_at and new_status == "active":
                    local_sub.expires_at = subscription_expires_at
                await self._sub_repo.save(local_sub)

                user = await self.user_repo.get_by_id(local_sub.owner_id)
                if user is not None:
                    if local_sub.plan_id:
                        user.plan_id = local_sub.plan_id
                    if subscription_expires_at and new_status == "active":
                        user.plan_expiration = subscription_expires_at
                        user.is_active = True
                    await self.user_repo.save(user)

            event_model.processing_status = "processed"
            event_model.processed_at = datetime.utcnow()
        except Exception as exc:
            event_model.processing_status = "failed"
            event_model.processing_error = str(exc)[:500]
            logger.error(f"[webhook] Falha recorrente event_id={event_id}: {exc}")
        await self._event_repo.save(event_model)

        return {"result": event_model.processing_status, "event_id": event_id, "event": event}

    async def _apply_event(
        self,
        event_type: str,
        local_sub,
        owner_id: Optional[uuid.UUID],
        status_from_event: Optional[str],
        expires_at: Optional[datetime],
        billing_subscription_id: Optional[str],
    ) -> None:
        """Aplica efeito do evento na assinatura local e cache do usuário."""

        # Mapeamento de event_type para status local
        event_to_status = {
            "subscription.created":        "pending",
            "subscription.active":         "active",
            "subscription.renewed":        "active",
            "subscription.trialing":       "trialing",
            "subscription.past_due":       "past_due",
            "subscription.canceled":       "canceled",
            "subscription.expired":        "expired",
            "subscription.payment_failed": "failed",
            "subscription.updated":        None,  # usa status_from_event
        }

        new_status = event_to_status.get(event_type)
        if new_status is None and status_from_event:
            new_status = status_from_event

        if new_status is None:
            logger.warning(f"[webhook] Evento não mapeado event_type={event_type} — ignorado.")
            return

        # Atualiza assinatura local
        if local_sub is not None and self._sub_repo:
            local_sub.status = new_status
            local_sub.last_event_at = datetime.utcnow()
            if expires_at:
                local_sub.expires_at = expires_at
            if billing_subscription_id and not local_sub.billing_subscription_id:
                local_sub.billing_subscription_id = billing_subscription_id
            await self._sub_repo.save(local_sub)

        # Atualiza cache UserModel
        if owner_id:
            user = await self.user_repo.get_by_id(owner_id)
            if user:
                plan_id = local_sub.plan_id if local_sub else None
                if plan_id:
                    user.plan_id = plan_id
                if expires_at:
                    user.plan_expiration = expires_at
                if new_status in ("active", "trialing"):
                    user.is_active = True
                elif new_status in ("canceled", "expired", "failed"):
                    pass  # não desativa automaticamente; decisão de negócio
                await self.user_repo.save(user)

        logger.info(
            f"[webhook] Aplicado event_type={event_type} status={new_status} "
            f"owner={owner_id} sub_id={local_sub.id if local_sub else None}"
        )

    # ------------------------------------------------------------------
    # Reconciliação admin
    # ------------------------------------------------------------------

    async def reconcile_user(self, owner_id: uuid.UUID) -> Dict[str, Any]:
        """Reconcilia assinatura local com estado do Billing Core.

        Se Billing Core não estiver habilitado, sincroniza cache UserModel
        com BillingSubscriptionModel.
        """
        from infra.clients.billing_core_client import BillingCoreClient, BillingCoreError

        user = await self.user_repo.get_by_id(owner_id)
        if not user:
            raise BusinessRuleException("Usuário não encontrado.")

        local_sub = None
        if self._sub_repo:
            local_sub = await self._sub_repo.get_active_by_owner(owner_id)

        previous_status = local_sub.status if local_sub else None

        # Sincroniza cache UserModel a partir da assinatura local
        if local_sub and local_sub.plan_id:
            user.plan_id = local_sub.plan_id
        if local_sub and local_sub.expires_at:
            user.plan_expiration = local_sub.expires_at
        await self.user_repo.save(user)

        logger.info(
            f"[reconcile] admin reconciliou owner={owner_id} "
            f"status_local={local_sub.status if local_sub else None}"
        )

        return {
            "user_id": str(owner_id),
            "previous_status": previous_status,
            "new_status": local_sub.status if local_sub else None,
            "message": "Reconciliação concluída.",
        }

    # ------------------------------------------------------------------
    # Compatibilidade com fluxo admin manual (Fase 1-3)
    # ------------------------------------------------------------------

    async def subscribe_manually(
        self,
        admin_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
        plan_id: uuid.UUID,
        duration_days: int,
    ) -> User:
        """Atribuição manual de plano pelo admin (sem Billing Core).

        Mantido para compatibilidade com o painel admin existente.
        Cria/atualiza BillingSubscriptionModel local com status 'active'.
        """
        target_user = await self.user_repo.get_by_id(target_user_id)
        if not target_user:
            raise BusinessRuleException("Usuário cliente não encontrado.")

        plan = await self.plan_repo.get_by_id(plan_id)
        if not plan:
            raise BusinessRuleException(f"Plano {plan_id} não encontrado.")

        target_user.activate_plan(plan, duration_days)
        updated_user = await self.user_repo.save(target_user)

        # Atualiza assinatura local
        if self._sub_repo is not None:
            idempotency_key = f"manual-{admin_user_id}-{target_user_id}-{plan_id}"
            existing = await self._sub_repo.get_by_idempotency_key(idempotency_key)
            if existing is None:
                await self._create_local_subscription(
                    owner_id=target_user_id,
                    plan_id=plan_id,
                    status="active",
                    subscription_type="manual",
                    value=float(plan.price_monthly or 0),
                    expires_at=updated_user.plan_expiration,
                    idempotency_key=idempotency_key,
                )
            else:
                existing.status = "active"
                existing.expires_at = updated_user.plan_expiration
                existing.last_event_at = datetime.utcnow()
                await self._sub_repo.save(existing)

        logger.info(f"[subscription] Admin {admin_user_id} atribuiu plano {plan_id} a {target_user_id}")
        return updated_user

    async def request_plan_change(self, user_id: uuid.UUID, new_plan_id: uuid.UUID) -> User:
        """Usuário solicita troca de plano (sem Billing Core — pré-integração)."""
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise BusinessRuleException("Usuário não encontrado.")

        plan = await self.plan_repo.get_by_id(new_plan_id)
        if not plan:
            raise BusinessRuleException("Plano não encontrado.")
        if not plan.is_active:
            raise BusinessRuleException("Este plano não está disponível.")

        user.plan_id = plan.id
        return await self.user_repo.save(user)

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    async def _create_local_subscription(
        self,
        owner_id: uuid.UUID,
        plan_id: uuid.UUID,
        status: str,
        subscription_type: str,
        value: float,
        expires_at: Optional[datetime],
        idempotency_key: str,
    ):
        from infra.database.models import BillingSubscriptionModel
        sub = BillingSubscriptionModel(
            owner_id=owner_id,
            plan_id=plan_id,
            billing_system=settings.BILLING_CORE_SYSTEM,
            billing_system_sub_id=str(owner_id),
            status=status,
            subscription_type=subscription_type,
            value=value,
            expires_at=expires_at,
            last_event_at=datetime.utcnow(),
            idempotency_key=idempotency_key,
        )
        return await self._sub_repo.save(sub)

    def _build_webhook_link(self) -> str:
        """Monta o webhook_link para o Billing Core.

        O host deve estar em ALLOWED_INTERNAL_WEBHOOK_HOSTS no Billing Core.
        """
        host = settings.BILLING_CORE_WEBHOOK_HOST or "http://localhost:8000"
        return f"{host.rstrip('/')}/api/v1/billing/webhooks/internal"
