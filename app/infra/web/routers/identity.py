import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from infra.database.setup import get_db
from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository, SQLAlchemyMarketRepository
from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
from application.services.identity_service import IdentityService
from application.services.subscription_service import SubscriptionService
from application.services.plan_access_service import PlanAccessService, PlanFeature
from application.dtos import UserCreateDTO, MarketCreateDTO, UserResponseDTO, SubscribeDTO
from infra.web.dependencies import get_identity_service, get_current_user, get_subscription_service
from infra.security.authorization import is_admin_user

router = APIRouter()

# --- REGISTER & MARKETS ---
@router.post("/register", response_model=UserResponseDTO, status_code=status.HTTP_201_CREATED)
async def register_user(
    dto: UserCreateDTO,
    service: IdentityService = Depends(get_identity_service)
):
    try:
        return await service.register_user(dto)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/markets", status_code=status.HTTP_201_CREATED)
async def create_market(
    dto: MarketCreateDTO,
    service: IdentityService = Depends(get_identity_service),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Gate de plano: valida assinatura e limite de lojas antes de criar
    from infra.repositories.sqlalchemy_repos import SQLAlchemyUserRepository, SQLAlchemyPlanRepository

    plan_service = PlanAccessService(
        user_repo=SQLAlchemyUserRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        subscription_repo=SQLAlchemyBillingSubscriptionRepository(db),
    )
    current_markets = await SQLAlchemyMarketRepository(db).count_by_owner(current_user.id)
    access = await plan_service.check_limit(current_user.id, PlanFeature.MARKETS, current_markets)
    if not access.allowed:
        raise HTTPException(status_code=403, detail=access.reason)

    try:
        return await service.create_market(current_user.id, dto)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/markets")
async def list_my_markets(
    service: IdentityService = Depends(get_identity_service),
    current_user = Depends(get_current_user)
):
    try:
        return await service.get_user_markets(current_user.id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# --- PLANOS ---

@router.get("/plans")
async def list_plans(db: AsyncSession = Depends(get_db)):
    repo = SQLAlchemyPlanRepository(db)
    return await repo.list_all()

@router.post("/plans/{plan_id}/subscribe")
async def subscribe_to_plan(
    plan_id: uuid.UUID,
    dto: SubscribeDTO, 
    service: SubscriptionService = Depends(get_subscription_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        repo_plan = SQLAlchemyPlanRepository(db)
        repo_user = service.user_repo
        
        plan = await repo_plan.get_by_id(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="Plano não encontrado")

        # Valida duração permitida (incluindo Trial 14 dias)
        if dto.duration_days not in [14, 30, 180, 365]:
            raise HTTPException(status_code=400, detail="Duração inválida. Use 14, 30, 180 ou 365.")

        # Lógica para Admin atribuir plano a outro usuário
        target_user = current_user
        
        # Se um ID de override foi enviado
        if dto.user_id_override:
            # Verifica se quem chamou é ADMIN via política central
            if not is_admin_user(current_user):
                raise HTTPException(status_code=403, detail="Apenas administradores podem atribuir planos a terceiros.")
            
            # Busca o usuário alvo
            target_user = await repo_user.get_by_id(dto.user_id_override)
            if not target_user:
                raise HTTPException(status_code=404, detail="Usuário alvo para atribuição não encontrado.")

        # Ativação Imediata (Calcula data de expiração)
        target_user.plan_id = plan.id
        target_user.plan_expiration = datetime.utcnow() + timedelta(days=dto.duration_days)
        target_user.is_active = True 
        
        await repo_user.save(target_user)
        
        return {
            "message": f"Plano {plan.name} ativado com sucesso.",
            "expiration": target_user.plan_expiration,
            "user": target_user.email
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))