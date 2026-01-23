import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.setup import get_db
from domain.identity import User, UserRole
from application.dtos import (
    AdminDashboardResponseDTO, AdminUserListDTO, AdminPasswordResetDTO,
    PlanCreateDTO, PlanUpdateDTO, PlanResponseDTO, SubscribeDTO # Alterado para usar SubscribeDTO
)
from application.services.admin_dashboard_service import AdminDashboardService
from application.services.admin_service import AdminService
from application.services.subscription_service import SubscriptionService
from infra.repositories.admin_stats_repo import AdminStatsRepository
from infra.repositories.sqlalchemy_repos import (
    SQLAlchemyUserRepository, SQLAlchemyPlanRepository, SQLAlchemyTicketRepository
)
from infra.web.dependencies import get_current_user, get_subscription_service # Import das dependências
from infra.security.auth_handler import AuthHandler

router = APIRouter()

# =============================================================================
# DEPENDENCIES & HELPERS
# =============================================================================

async def get_admin_dashboard_service(db: AsyncSession = Depends(get_db)):
    stats_repo = AdminStatsRepository(db)
    user_repo = SQLAlchemyUserRepository(db)
    plan_repo = SQLAlchemyPlanRepository(db)
    return AdminDashboardService(
        stats_repo, user_repo, plan_repo, 
        hasher=AuthHandler.get_password_hash
    )

async def get_admin_plans_service(db: AsyncSession = Depends(get_db)):
    plan_repo = SQLAlchemyPlanRepository(db)
    return AdminService(plan_repo)

def verify_admin_role(user: User = Depends(get_current_user)):
    role = user.role.value if hasattr(user.role, 'value') else str(user.role)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores.")
    return user

# =============================================================================
# ADMIN DASHBOARD & USERS
# =============================================================================

@router.get("/dashboard", response_model=AdminDashboardResponseDTO)
async def get_dashboard(
    current_user: User = Depends(verify_admin_role),
    service: AdminDashboardService = Depends(get_admin_dashboard_service)
):
    return await service.get_dashboard_overview()

@router.get("/users", response_model=List[AdminUserListDTO])
async def list_users(
    current_user: User = Depends(verify_admin_role),
    service: AdminDashboardService = Depends(get_admin_dashboard_service)
):
    return await service.list_all_users()

@router.post("/users/{user_id}/toggle-status")
async def toggle_status(
    user_id: uuid.UUID,
    current_user: User = Depends(verify_admin_role),
    service: AdminDashboardService = Depends(get_admin_dashboard_service)
):
    return await service.toggle_user_status(user_id)

@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: uuid.UUID,
    dto: AdminPasswordResetDTO,
    current_user: User = Depends(verify_admin_role),
    service: AdminDashboardService = Depends(get_admin_dashboard_service)
):
    return await service.admin_reset_password(user_id, dto)

# =============================================================================
# PLANS MANAGEMENT
# =============================================================================

@router.get("/plans", response_model=List[PlanResponseDTO])
async def list_plans(
    current_user: User = Depends(verify_admin_role),
    service: AdminService = Depends(get_admin_plans_service)
):
    plans = await service.plan_repo.list_all()
    return [
        PlanResponseDTO(
            id=p.id, name=p.name, type=p.type.value, 
            max_markets=p.max_markets, max_terminals=p.max_terminals,
            price_monthly=p.price_monthly, price_180days=p.price_180days,
            price_annual=p.price_annual, is_active=p.is_active
        ) for p in plans
    ]

@router.post("/plans", response_model=PlanResponseDTO, status_code=status.HTTP_201_CREATED)
async def create_plan(
    dto: PlanCreateDTO,
    current_user: User = Depends(verify_admin_role),
    service: AdminService = Depends(get_admin_plans_service)
):
    p = await service.create_plan(dto)
    return PlanResponseDTO(
        id=p.id, name=p.name, type=p.type.value, 
        max_markets=p.max_markets, max_terminals=p.max_terminals,
        price_monthly=p.price_monthly, price_180days=p.price_180days,
        price_annual=p.price_annual, is_active=True
    )

@router.put("/plans/{plan_id}")
async def update_plan(
    plan_id: uuid.UUID,
    dto: PlanUpdateDTO,
    current_user: User = Depends(verify_admin_role),
    service: AdminService = Depends(get_admin_plans_service)
):
    p = await service.update_plan(plan_id, dto)
    return {"message": "Plano atualizado com sucesso", "id": p.id}

# =============================================================================
# MANUAL SUBSCRIPTION
# =============================================================================

@router.post("/identity/plans/{plan_id}/subscribe")
async def admin_subscribe_user(
    plan_id: uuid.UUID,
    dto: SubscribeDTO, # CORREÇÃO: Usando SubscribeDTO que tem user_id_override
    current_user: User = Depends(verify_admin_role),
    service: SubscriptionService = Depends(get_subscription_service)
):
    """
    Endpoint para o Admin atribuir manualmente um plano a um usuário.
    Geralmente usado após confirmação de PIX.
    """
    # Se não informar o ID de destino, assume que o admin está se auto-atribuindo o plano (útil para testes)
    if not dto.user_id_override:
        raise HTTPException(status_code=400, detail="Falta o id do usuário (user_id_override)")
    target_id = dto.user_id_override

    await service.subscribe_manually(
        admin_user_id=current_user.id,
        target_user_id=target_id,
        duration_days=dto.duration_days,
        plan_id= plan_id
    )
    return {"message": "Plano atribuído com sucesso."}