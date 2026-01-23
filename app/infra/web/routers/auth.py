from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from infra.database.setup import get_db
from infra.security.auth_handler import AuthHandler
from infra.repositories.sqlalchemy_repos import SQLAlchemyUserRepository, SQLAlchemyPlanRepository
from application.services.subscription_service import SubscriptionService
from application.dtos import UserResponseDTO
from infra.web.dependencies import get_current_user, get_subscription_service
from domain.identity import User
from domain.shared import BusinessRuleException

router = APIRouter()

@router.post("/token")
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    try:
        repo = SQLAlchemyUserRepository(db)
        user = await repo.get_by_email(form_data.username)
        
        valid_password = False
        if user:
            try:
                valid_password = AuthHandler.verify_password(form_data.password[:72], user.password_hash)
            except Exception:
                valid_password = False
        
        if not user or not valid_password:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email ou senha incorretos",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        if not user.is_active:
            raise HTTPException(status_code=400, detail="Usuário inativo ou plano expirado.")

        # Extrai string do Value Object Role para o token
        role_val = user.role.value if hasattr(user.role, 'value') else str(user.role)
        
        access_token = AuthHandler.create_access_token(
            data={"sub": str(user.id), "role": role_val}
        )
        
        return {"access_token": access_token, "token_type": "bearer"}

    except HTTPException:
        raise
    except Exception as e:
        print(f"Erro interno no login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Erro interno ao processar login."
        )

@router.get("/me", response_model=UserResponseDTO)
async def read_users_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db) # Necessário para buscar o plano
):
    """
    Retorna os dados do usuário atual validado no banco.
    """
    # 1. Correção do Erro 500: Extração manual dos Value Objects para strings
    email_val = current_user.email.value if hasattr(current_user.email, 'value') else str(current_user.email)
    role_val = current_user.role.value if hasattr(current_user.role, 'value') else str(current_user.role)

    # 2. Busca o Nome do Plano (Requirement: front precisa saber o nome)
    plan_name = None
    if current_user.plan_id:
        plan_repo = SQLAlchemyPlanRepository(db)
        plan = await plan_repo.get_by_id(current_user.plan_id)
        if plan:
            plan_name = plan.name

    return UserResponseDTO(
        id=current_user.id,
        name=current_user.name,
        email=email_val,
        role=role_val,
        plan_id=current_user.plan_id,
        plan_name=plan_name, # Campo populado dinamicamente
        plan_expiration=current_user.plan_expiration,
        is_active=current_user.is_active
    )

@router.post("/trial", response_model=UserResponseDTO)
async def activate_trial(
    service: SubscriptionService = Depends(get_subscription_service),
    current_user: User = Depends(get_current_user)
):
    """
    Ativa um período de testes de 14 dias para o usuário logado.
    """
    try:
        updated_user, plan_name = await service.activate_trial(current_user)
        
        # Extração manual para o DTO de resposta
        email_val = updated_user.email.value if hasattr(updated_user.email, 'value') else str(updated_user.email)
        role_val = updated_user.role.value if hasattr(updated_user.role, 'value') else str(updated_user.role)

        return UserResponseDTO(
            id=updated_user.id,
            name=updated_user.name,
            email=email_val,
            role=role_val,
            plan_id=updated_user.plan_id,
            plan_name=plan_name,
            plan_expiration=updated_user.plan_expiration,
            is_active=updated_user.is_active
        )
        
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Erro ao ativar período de testes.")