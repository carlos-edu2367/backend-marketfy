"""PlanAccessService — Fase 4 / PR 13.

Serviço central de validação de plano e feature no backend.
Nunca confiar em status de plano vindo do frontend.

Fluxo:
  1. Busca a assinatura ativa do owner em BillingSubscriptionModel.
  2. Fallback para UserModel.plan_id / plan_expiration quando não há
     assinatura local ainda (transição do sistema manual).
  3. Consulta os limites do PlanModel local.
  4. Retorna PlanAccessResult com allowed, reason, limit e status.

Regras de status:
  - trialing / active  → acesso conforme features do plano
  - pending            → acesso apenas a operações básicas (sem features pagas)
  - past_due           → restrito a features essenciais; sem criação de novos recursos
  - canceled / expired / failed → bloqueio de uso operacional; apenas settings/suporte/planos
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from domain.identity import Plan, User
from domain.interfaces import UserRepositoryInterface, PlanRepositoryInterface
from domain.shared import BusinessRuleException


# ---------------------------------------------------------------------------
# Constantes de status de assinatura
# ---------------------------------------------------------------------------

class SubscriptionStatus:
    PENDING   = "pending"
    TRIALING  = "trialing"
    ACTIVE    = "active"
    PAST_DUE  = "past_due"
    CANCELED  = "canceled"
    EXPIRED   = "expired"
    FAILED    = "failed"

    OPERATIONAL = {TRIALING, ACTIVE}       # acesso completo
    RESTRICTED  = {PAST_DUE}               # acesso limitado; sem novos recursos
    BLOCKED     = {CANCELED, EXPIRED, FAILED}  # sem acesso operacional


# ---------------------------------------------------------------------------
# Features controláveis por plano
# ---------------------------------------------------------------------------

class PlanFeature:
    MARKETS   = "markets"       # criação de lojas
    TERMINALS = "terminals"     # criação de terminais
    FINANCE   = "finance"       # módulo financeiro / DRE
    REPORTS   = "reports"       # relatórios Excel / PDF
    FISCAL    = "fiscal"        # emissão NFC-e
    PDV       = "pdv"           # ponto de venda (básico — sempre disponível em ativo)
    CUSTOMERS = "customers"     # clientes e fiado (básico)
    SUPPORT   = "support"       # suporte (sempre disponível)
    SETTINGS  = "settings"      # configurações (sempre disponível)


# Features disponíveis apenas em planos pagos (não-cortesia)
PAID_FEATURES = {PlanFeature.FINANCE, PlanFeature.REPORTS, PlanFeature.FISCAL}

# Features disponíveis em qualquer assinatura operacional
BASIC_FEATURES = {PlanFeature.PDV, PlanFeature.CUSTOMERS, PlanFeature.SUPPORT, PlanFeature.SETTINGS}


# ---------------------------------------------------------------------------
# Resultado da verificação
# ---------------------------------------------------------------------------

@dataclass
class PlanAccessResult:
    allowed: bool
    reason: str
    subscription_status: str = SubscriptionStatus.PENDING
    current_usage: int = 0
    limit: Optional[int] = None
    plan_name: Optional[str] = None
    expires_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Repositório de assinatura (interface mínima)
# ---------------------------------------------------------------------------

class BillingSubscriptionRepositoryInterface:
    """Interface mínima usada pelo PlanAccessService.

    A implementação real fica em infra/repositories.
    """

    async def get_active_by_owner(self, owner_id: uuid.UUID):
        raise NotImplementedError

    async def get_by_id(self, subscription_id: uuid.UUID):
        raise NotImplementedError

    async def save(self, subscription) -> None:
        raise NotImplementedError

    async def list_by_owner(self, owner_id: uuid.UUID):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Serviço principal
# ---------------------------------------------------------------------------

class PlanAccessService:
    """Valida acesso a features e limites de plano no backend.

    Nunca chamar Billing Core de forma síncrona aqui.
    Usa cache local (BillingSubscriptionModel + UserModel).
    """

    def __init__(
        self,
        user_repo: UserRepositoryInterface,
        plan_repo: PlanRepositoryInterface,
        subscription_repo: BillingSubscriptionRepositoryInterface,
    ):
        self._user_repo = user_repo
        self._plan_repo = plan_repo
        self._sub_repo = subscription_repo

    # ------------------------------------------------------------------
    # Consulta de status de assinatura
    # ------------------------------------------------------------------

    async def get_subscription_status(self, owner_id: uuid.UUID) -> PlanAccessResult:
        """Retorna o status consolidado da assinatura do owner."""
        sub = await self._sub_repo.get_active_by_owner(owner_id)

        if sub is not None:
            plan = await self._plan_repo.get_by_id(sub.plan_id) if sub.plan_id else None
            return PlanAccessResult(
                allowed=sub.status in SubscriptionStatus.OPERATIONAL,
                reason="Assinatura ativa." if sub.status in SubscriptionStatus.OPERATIONAL else f"Status: {sub.status}",
                subscription_status=sub.status,
                plan_name=plan.name if plan else None,
                expires_at=sub.expires_at,
            )

        # Fallback: UserModel ainda é usado como cache temporário
        return await self._fallback_from_user(owner_id)

    async def _fallback_from_user(self, owner_id: uuid.UUID) -> PlanAccessResult:
        """Fallback para UserModel.plan_id quando BillingSubscriptionModel está vazio."""
        user = await self._user_repo.get_by_id(owner_id)
        if user is None:
            return PlanAccessResult(allowed=False, reason="Usuário não encontrado.", subscription_status=SubscriptionStatus.FAILED)

        if user.plan_id is None:
            return PlanAccessResult(allowed=False, reason="Sem plano ativo.", subscription_status=SubscriptionStatus.PENDING)

        plan = await self._plan_repo.get_by_id(user.plan_id)
        if plan is None or not plan.is_active:
            return PlanAccessResult(allowed=False, reason="Plano não encontrado ou inativo.", subscription_status=SubscriptionStatus.FAILED)

        if user.plan_expiration and user.plan_expiration < datetime.utcnow():
            return PlanAccessResult(
                allowed=False,
                reason="Plano expirado.",
                subscription_status=SubscriptionStatus.EXPIRED,
                plan_name=plan.name,
                expires_at=user.plan_expiration,
            )

        return PlanAccessResult(
            allowed=True,
            reason="Plano ativo (cache UserModel).",
            subscription_status=SubscriptionStatus.ACTIVE,
            plan_name=plan.name,
            expires_at=user.plan_expiration,
        )

    # ------------------------------------------------------------------
    # Verificação de feature
    # ------------------------------------------------------------------

    async def check_feature(self, owner_id: uuid.UUID, feature: str) -> PlanAccessResult:
        """Verifica se o owner tem acesso a uma feature específica.

        Features básicas (PDV, suporte, settings) são sempre disponíveis
        em qualquer assinatura operacional.
        Features pagas exigem plano do tipo 'pago' (não cortesia).
        """
        status_result = await self.get_subscription_status(owner_id)

        # Settings e suporte: sempre permitidos (para regularização)
        if feature in {PlanFeature.SUPPORT, PlanFeature.SETTINGS}:
            return PlanAccessResult(
                allowed=True,
                reason="Acesso sempre permitido.",
                subscription_status=status_result.subscription_status,
                plan_name=status_result.plan_name,
            )

        # Assinatura bloqueada
        if status_result.subscription_status in SubscriptionStatus.BLOCKED:
            return PlanAccessResult(
                allowed=False,
                reason=f"Assinatura {status_result.subscription_status}. Regularize para continuar.",
                subscription_status=status_result.subscription_status,
                plan_name=status_result.plan_name,
            )

        # Assinatura não operacional (pending, past_due, etc.)
        if status_result.subscription_status not in SubscriptionStatus.OPERATIONAL:
            return PlanAccessResult(
                allowed=False,
                reason=f"Assinatura em status '{status_result.subscription_status}'. Verifique sua assinatura.",
                subscription_status=status_result.subscription_status,
                plan_name=status_result.plan_name,
            )

        # Features pagas: verificar se o plano é do tipo 'pago'
        if feature in PAID_FEATURES:
            plan = await self._get_plan_for_owner(owner_id)
            if plan is None:
                return PlanAccessResult(
                    allowed=False,
                    reason="Plano não encontrado.",
                    subscription_status=status_result.subscription_status,
                )
            if plan.type not in ("pago", "trial"):
                return PlanAccessResult(
                    allowed=False,
                    reason=f"Feature '{feature}' disponível apenas em planos pagos.",
                    subscription_status=status_result.subscription_status,
                    plan_name=plan.name,
                )

        return PlanAccessResult(
            allowed=True,
            reason="Acesso permitido.",
            subscription_status=status_result.subscription_status,
            plan_name=status_result.plan_name,
            expires_at=status_result.expires_at,
        )

    # ------------------------------------------------------------------
    # Verificação de limites
    # ------------------------------------------------------------------

    async def check_limit(
        self,
        owner_id: uuid.UUID,
        resource: str,
        current_count: int,
    ) -> PlanAccessResult:
        """Verifica se o owner pode criar mais recursos (lojas, terminais).

        resource: 'markets' | 'terminals'
        current_count: quantidade atual no banco.
        """
        status_result = await self.get_subscription_status(owner_id)

        # Sem assinatura operacional = bloqueia criação de recursos
        if not status_result.allowed:
            return PlanAccessResult(
                allowed=False,
                reason=status_result.reason,
                subscription_status=status_result.subscription_status,
                current_usage=current_count,
                plan_name=status_result.plan_name,
            )

        plan = await self._get_plan_for_owner(owner_id)
        if plan is None:
            return PlanAccessResult(
                allowed=False,
                reason="Plano não encontrado.",
                subscription_status=status_result.subscription_status,
                current_usage=current_count,
            )

        limit = plan.max_markets if resource == PlanFeature.MARKETS else plan.max_terminals
        if limit is not None and current_count >= limit:
            return PlanAccessResult(
                allowed=False,
                reason=f"Limite de {resource} atingido ({current_count}/{limit}) para o plano {plan.name}.",
                subscription_status=status_result.subscription_status,
                current_usage=current_count,
                limit=limit,
                plan_name=plan.name,
            )

        return PlanAccessResult(
            allowed=True,
            reason="Dentro do limite.",
            subscription_status=status_result.subscription_status,
            current_usage=current_count,
            limit=limit,
            plan_name=plan.name,
            expires_at=status_result.expires_at,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_plan_for_owner(self, owner_id: uuid.UUID) -> Optional[Plan]:
        """Busca o plano do owner via assinatura local ou cache UserModel."""
        sub = await self._sub_repo.get_active_by_owner(owner_id)
        if sub and sub.plan_id:
            return await self._plan_repo.get_by_id(sub.plan_id)

        user = await self._user_repo.get_by_id(owner_id)
        if user and user.plan_id:
            return await self._plan_repo.get_by_id(user.plan_id)

        return None

    async def get_fiscal_monthly_limit(self, owner_id: uuid.UUID) -> int:
        """Retorna o limite mensal de emissões NFC-e do plano ativo do owner."""
        plan = await self._get_plan_for_owner(owner_id)
        if plan is None:
            return 0
        return getattr(plan, "fiscal_monthly_limit", 0) or 0

    async def get_plan_features(self, owner_id: uuid.UUID) -> dict:
        """Retorna dict com features e limites disponíveis para o owner.

        Usado pelo endpoint GET /billing/plans/features.
        """
        status_result = await self.get_subscription_status(owner_id)
        plan = await self._get_plan_for_owner(owner_id)

        base = {
            "subscription_status": status_result.subscription_status,
            "plan_name": status_result.plan_name,
            "expires_at": status_result.expires_at.isoformat() if status_result.expires_at else None,
            "features": {},
            "limits": {},
        }

        is_operational = status_result.subscription_status in SubscriptionStatus.OPERATIONAL
        is_paid = plan is not None and plan.type in ("pago", "trial")

        base["features"] = {
            PlanFeature.PDV: is_operational,
            PlanFeature.CUSTOMERS: is_operational,
            PlanFeature.FINANCE: is_operational and is_paid,
            PlanFeature.REPORTS: is_operational and is_paid,
            PlanFeature.FISCAL: is_operational and is_paid,
            PlanFeature.SUPPORT: True,
            PlanFeature.SETTINGS: True,
        }

        if plan:
            base["limits"] = {
                PlanFeature.MARKETS: plan.max_markets,
                PlanFeature.TERMINALS: plan.max_terminals,
            }

        return base
