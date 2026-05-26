import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.setup import get_db
from domain.identity import User, UserRole
from domain.shared import BusinessRuleException
from application.dtos import (
    AdminDashboardResponseDTO, AdminUserListDTO, AdminPasswordResetDTO,
    PlanCreateDTO, PlanUpdateDTO, PlanResponseDTO, SubscribeDTO,
    AdminBillingReconcileResponseDTO,
)
from application.services.admin_dashboard_service import AdminDashboardService
from application.services.admin_service import AdminService
from application.services.subscription_service import SubscriptionService
from application.services.audit_service import AuditService
from infra.repositories.admin_stats_repo import AdminStatsRepository
from infra.repositories.sqlalchemy_repos import (
    SQLAlchemyUserRepository, SQLAlchemyPlanRepository, SQLAlchemyTicketRepository
)
from infra.web.dependencies import (
    get_current_user,
    get_subscription_service,
    get_audit_service,
    require_admin,
)
from infra.security.auth_handler import AuthHandler
from infra.observability.audit import record_audit_event

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

def verify_admin_role(user: User = Depends(require_admin)):
    """Compat shim: delega para a política central `require_admin`."""
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
    request: Request,
    user_id: uuid.UUID,
    dto: AdminPasswordResetDTO,
    current_user: User = Depends(verify_admin_role),
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
    audit: AuditService = Depends(get_audit_service),
):
    result = await service.admin_reset_password(user_id, dto)
    await record_audit_event(
        audit,
        request,
        actor=current_user,
        action="admin.user.password_reset",
        resource_type="user",
        resource_id=str(user_id),
        result="success",
    )
    return result

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
    request: Request,
    dto: PlanCreateDTO,
    current_user: User = Depends(verify_admin_role),
    service: AdminService = Depends(get_admin_plans_service),
    audit: AuditService = Depends(get_audit_service),
):
    p = await service.create_plan(dto)
    await record_audit_event(
        audit,
        request,
        actor=current_user,
        action="admin.plan.created",
        resource_type="plan",
        resource_id=str(p.id),
        result="success",
        metadata={"type": p.type.value if hasattr(p.type, "value") else str(p.type)},
    )
    return PlanResponseDTO(
        id=p.id, name=p.name, type=p.type.value, 
        max_markets=p.max_markets, max_terminals=p.max_terminals,
        price_monthly=p.price_monthly, price_180days=p.price_180days,
        price_annual=p.price_annual, is_active=p.is_active
    )

@router.put("/plans/{plan_id}", response_model=PlanResponseDTO)
async def update_plan(
    request: Request,
    plan_id: uuid.UUID,
    dto: PlanUpdateDTO,
    current_user: User = Depends(verify_admin_role),
    service: AdminService = Depends(get_admin_plans_service),
    audit: AuditService = Depends(get_audit_service),
):
    p = await service.update_plan(plan_id, dto)
    await record_audit_event(
        audit,
        request,
        actor=current_user,
        action="admin.plan.updated",
        resource_type="plan",
        resource_id=str(plan_id),
        result="success",
        metadata={"is_active": p.is_active},
    )
    return PlanResponseDTO(
        id=p.id, name=p.name, type=p.type.value,
        max_markets=p.max_markets, max_terminals=p.max_terminals,
        price_monthly=p.price_monthly, price_180days=p.price_180days,
        price_annual=p.price_annual, is_active=p.is_active
    )

# =============================================================================
# MANUAL SUBSCRIPTION
# =============================================================================

@router.post("/identity/plans/{plan_id}/subscribe")
async def admin_subscribe_user(
    request: Request,
    plan_id: uuid.UUID,
    dto: SubscribeDTO, # CORREÇÃO: Usando SubscribeDTO que tem user_id_override
    current_user: User = Depends(verify_admin_role),
    service: SubscriptionService = Depends(get_subscription_service),
    audit: AuditService = Depends(get_audit_service),
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
        plan_id=plan_id,
    )
    await record_audit_event(
        audit,
        request,
        actor=current_user,
        action="admin.subscription.assigned",
        resource_type="billing_subscription",
        resource_id=str(target_id),
        result="success",
        metadata={"plan_id": plan_id, "duration_days": dto.duration_days},
    )
    return {"message": "Plano atribuído com sucesso."}


# =============================================================================
# BILLING RECONCILIATION (Fase 4 / PR 15)
# =============================================================================

@router.post("/billing/reconcile/{user_id}", response_model=AdminBillingReconcileResponseDTO)
async def reconcile_user_billing(
    request: Request,
    user_id: uuid.UUID,
    current_user: User = Depends(verify_admin_role),
    db: AsyncSession = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
):
    """Reconciliação manual de assinatura para um usuário.

    Sincroniza cache UserModel.plan_id/plan_expiration com
    BillingSubscriptionModel. Útil quando webhook falhou ou
    status ficou inconsistente.

    Protegido por role admin. Toda execução é auditável via logs.
    """
    from application.services.subscription_service import SubscriptionService
    from infra.repositories.sqlalchemy_repos import SQLAlchemyUserRepository, SQLAlchemyPlanRepository
    from infra.repositories.billing_repo import (
        SQLAlchemyBillingSubscriptionRepository,
        SQLAlchemyBillingEventRepository,
    )

    service = SubscriptionService(
        user_repo=SQLAlchemyUserRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        subscription_repo=SQLAlchemyBillingSubscriptionRepository(db),
        event_repo=SQLAlchemyBillingEventRepository(db),
    )

    try:
        result = await service.reconcile_user(user_id)
        await record_audit_event(
            audit,
            request,
            actor=current_user,
            action="admin.billing.reconciled",
            resource_type="billing_subscription",
            resource_id=str(user_id),
            result="success",
            metadata={
                "previous_status": result.get("previous_status"),
                "new_status": result.get("new_status"),
            },
        )
        return AdminBillingReconcileResponseDTO(
            user_id=user_id,
            previous_status=result.get("previous_status"),
            new_status=result.get("new_status"),
            message=result.get("message", "Reconciliação concluída."),
        )
    except BusinessRuleException as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        from infra.config.logger import get_logger
        get_logger("admin").error(f"[reconcile] Erro admin={current_user.id} target={user_id}: {exc}")
        raise HTTPException(status_code=500, detail="Erro ao reconciliar assinatura.")
