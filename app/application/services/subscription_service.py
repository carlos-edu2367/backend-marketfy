import uuid
from typing import Optional
from datetime import datetime, timedelta
from domain.identity import User, Plan, PlanType
from domain.interfaces import UserRepositoryInterface, PlanRepositoryInterface
from domain.shared import BusinessRuleException

class SubscriptionService:
    def __init__(self, user_repo: UserRepositoryInterface, plan_repo: PlanRepositoryInterface):
        self.user_repo = user_repo
        self.plan_repo = plan_repo

    async def request_plan_change(self, user_id: uuid.UUID, new_plan_id: uuid.UUID) -> User:
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise BusinessRuleException("Usuário não encontrado.")

        plan = await self.plan_repo.get_by_id(new_plan_id)
        if not plan:
            raise BusinessRuleException("Plano não encontrado.")

        if not plan.is_active:
            raise BusinessRuleException("Este plano não está disponível para contratação.")

        user.plan_id = plan.id
        return await self.user_repo.save(user)

    async def subscribe_manually(self, admin_user_id: uuid.UUID, target_user_id: uuid.UUID, plan_id: uuid.UUID, duration_days: int) -> User:
        """
        Função exclusiva para ADMIN ativar ou renovar o plano de um cliente.
        """
        target_user = await self.user_repo.get_by_id(target_user_id)
        if not target_user:
            raise BusinessRuleException("Usuário cliente não encontrado.")

        # CORREÇÃO CRÍTICA: Buscar o plano pelo ID enviado pelo Admin (novo plano),
        # e não pelo target_user.plan_id (plano antigo/atual).
        plan = await self.plan_repo.get_by_id(plan_id)
        if not plan:
            raise BusinessRuleException(f"Plano {plan_id} não encontrado.")
        
        # Ativa o novo plano no usuário
        # O método activate_plan do domínio já atualiza o self.plan_id
        target_user.activate_plan(plan, duration_days)
        
        return await self.user_repo.save(target_user)

    async def activate_trial(self, user: User) -> tuple[User, str]:
        """
        Ativa o período de testes (Trial) para o usuário.
        """
        if user.plan_id is not None:
            raise BusinessRuleException("Usuário já possui histórico de planos.")

        plans = await self.plan_repo.list_all()
        # Busca plano do tipo TRIAL
        trial_plan = next((p for p in plans if p.type == PlanType.TRIAL), None)
        
        if not trial_plan:
            raise BusinessRuleException("Plano Trial não configurado no sistema.")

        user.plan_id = trial_plan.id
        user.plan_expiration = datetime.utcnow() + timedelta(days=14)
        user.is_active = True

        updated_user = await self.user_repo.save(user)
        return updated_user, trial_plan.name