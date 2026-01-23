import uuid
from typing import List, Optional
from domain.identity import User, Plan, UserRole
from application.dtos import (
    AdminDashboardResponseDTO, AdminDashboardMetricsDTO, ExpiringUserDTO,
    AdminUserListDTO, AdminPasswordResetDTO
)
from domain.shared import BusinessRuleException
from datetime import datetime

# Interfaces necessárias (repositórios)
from domain.interfaces import PlanRepositoryInterface, UserRepositoryInterface, TicketRepositoryInterface
# Import do repo específico de stats
from infra.repositories.admin_stats_repo import AdminStatsRepository 

class AdminDashboardService:
    def __init__(
        self, 
        stats_repo: AdminStatsRepository,
        user_repo: UserRepositoryInterface,
        plan_repo: PlanRepositoryInterface,
        hasher # Função hash injetada
    ):
        self.stats_repo = stats_repo
        self.user_repo = user_repo
        self.plan_repo = plan_repo
        self.hasher = hasher

    async def get_dashboard_overview(self) -> AdminDashboardResponseDTO:
        """Retorna cards de estatísticas gerais e usuários vencendo."""
        stats = await self.stats_repo.get_general_stats()
        expiring = await self.stats_repo.get_expiring_plans(days_threshold=7)
        
        # Formata expiring users
        expiring_list = []
        for row in expiring:
            user = row[0] # UserModel ou Entity (depende do repo, aqui assume Row)
            plan_name = row[1]
            
            days_left = 0
            if user.plan_expiration:
                delta = user.plan_expiration - datetime.utcnow()
                days_left = delta.days

            expiring_list.append(ExpiringUserDTO(
                user_id=user.id,
                user_name=user.name,
                days_left=days_left,
                plan_name=plan_name or "Desconhecido"
            ))

        metrics = AdminDashboardMetricsDTO(
            mrr=stats["mrr"],
            active_users=stats["active_users"],
            total_users=stats["total_users"],
            total_markets=stats["total_markets"],
            open_tickets=stats["open_tickets"]
        )
        
        # CORREÇÃO: Nome do argumento alterado de 'expiring_soon' para 'expiring_users' 
        # para bater com a definição do Pydantic DTO
        return AdminDashboardResponseDTO(metrics=metrics, expiring_users=expiring_list)

    async def list_all_users(self) -> List[AdminUserListDTO]:
        """Lista usuários formatados para a tabela do Admin."""
        rows = await self.stats_repo.list_users_enriched()
        
        output = []
        for row in rows:
            user = row[0]
            plan_name = row[1]
            markets_count = row[2]
            
            # Conversão defensiva do role para string
            role_val = user.role.value if hasattr(user.role, 'value') else str(user.role)
            
            # Tratamento de e-mail (caso seja VO ou string)
            email_val = user.email.value if hasattr(user.email, 'value') else str(user.email)

            output.append(AdminUserListDTO(
                user_id=user.id, # Atenção: DTO pede user_id, mas model tem id
                name=user.name or "Sem Nome",
                email=email_val,
                role=role_val,
                plan_name=plan_name or "Sem Plano",
                plan_expiration=user.plan_expiration,
                markets_count=markets_count or 0,
                is_active=user.is_active if user.is_active is not None else False,
                created_at=user.created_at or datetime.utcnow()
            ))
        return output

    async def admin_reset_password(self, user_id: uuid.UUID, dto: AdminPasswordResetDTO):
        """Admin força troca de senha de usuário."""
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise BusinessRuleException("Usuário não encontrado.")
        
        # Gera novo hash
        user.password_hash = self.hasher(dto.password)
        await self.user_repo.save(user)
        return {"message": "Senha alterada com sucesso."}

    async def toggle_user_status(self, user_id: uuid.UUID):
        """Ativa/Inativa usuário (Bloqueio de inadimplente)."""
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise BusinessRuleException("Usuário não encontrado.")
        
        # Evita que admin se bloqueie
        role_val = user.role.value if hasattr(user.role, 'value') else str(user.role)
        if role_val == 'admin' or role_val == UserRole.ADMIN:
            raise BusinessRuleException("Não é possível bloquear um administrador.")
        
        user.is_active = not user.is_active
        await self.user_repo.save(user)
        
        status_msg = "ativado" if user.is_active else "bloqueado"
        return {"message": f"Usuário {status_msg} com sucesso.", "is_active": user.is_active}