import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from infra.database.setup import get_db
from infra.security.auth_handler import AuthHandler
from infra.repositories.sqlalchemy_repos import SQLAlchemyUserRepository, SQLAlchemyPlanRepository
from infra.repositories.refresh_session_repo import SQLAlchemyRefreshSessionRepository
from application.services.subscription_service import SubscriptionService
from application.services.audit_service import AuditService
from application.dtos import UserResponseDTO
from infra.web.dependencies import get_audit_service, get_current_user, get_subscription_service
from infra.config.settings import get_settings
from domain.identity import User
from domain.shared import BusinessRuleException
from infra.config.logger import get_logger
from infra.observability.audit import record_audit_event
from infra.observability.metrics import metrics_registry

router = APIRouter()
logger = get_logger("auth")
settings = get_settings()


def _client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=settings.REFRESH_TOKEN_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.COOKIE_SAMESITE,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path="/api/v1/auth",
    )


async def _create_refresh_session(
    request: Request,
    response: Response,
    db: AsyncSession,
    user: User,
    role_val: str,
) -> str:
    jti = str(uuid.uuid4())
    expires_at = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    refresh_token = AuthHandler.create_refresh_token(
        subject=str(user.id),
        role=role_val,
        jti=jti,
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    refresh_repo = SQLAlchemyRefreshSessionRepository(db)
    await refresh_repo.create(
        user_id=user.id,
        jti_hash=AuthHandler.hash_token_jti(jti),
        expires_at=expires_at,
        user_agent=request.headers.get("User-Agent"),
        ip_address=_client_ip(request),
    )
    _set_refresh_cookie(response, refresh_token)
    return refresh_token

@router.post("/token")
async def login_for_access_token(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
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
            metrics_registry.record_auth_failure("invalid_credentials")
            await record_audit_event(
                audit,
                request,
                actor=None,
                action="auth.login_failed",
                resource_type="auth",
                result="failed",
                metadata={"reason": "invalid_credentials"},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email ou senha incorretos",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        if not user.is_active:
            metrics_registry.record_auth_failure("inactive_user")
            await record_audit_event(
                audit,
                request,
                actor=user,
                action="auth.login_failed",
                resource_type="auth",
                result="failed",
                metadata={"reason": "inactive_user"},
            )
            raise HTTPException(status_code=400, detail="Usuário inativo ou plano expirado.")

        # Extrai string do Value Object Role para o token
        role_val = user.role.value if hasattr(user.role, 'value') else str(user.role)
        
        access_token = AuthHandler.create_access_token(
            data={"sub": str(user.id), "role": role_val}
        )
        await _create_refresh_session(request, response, db, user, role_val)
        
        return {"access_token": access_token, "token_type": "bearer"}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Erro interno ao processar login")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Erro interno ao processar login."
        )


@router.post("/refresh")
async def refresh_access_token(
    request: Request,
    response: Response,
    refresh_token: Optional[str] = Cookie(default=None, alias=settings.REFRESH_TOKEN_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessao expirada.")

    payload = AuthHandler.decode_token(refresh_token)
    if not payload or payload.get("typ") != "refresh" or not payload.get("jti") or not payload.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessao invalida.")

    refresh_repo = SQLAlchemyRefreshSessionRepository(db)
    session = await refresh_repo.get_active(AuthHandler.hash_token_jti(payload["jti"]))
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessao expirada.")

    user_repo = SQLAlchemyUserRepository(db)
    user = await user_repo.get_by_id(uuid.UUID(payload["sub"]))
    if not user or not user.is_active:
        await refresh_repo.revoke(AuthHandler.hash_token_jti(payload["jti"]))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario inativo.")

    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
    access_token = AuthHandler.create_access_token(data={"sub": str(user.id), "role": role_val})
    await _create_refresh_session(request, response, db, user, role_val)
    await refresh_repo.revoke(AuthHandler.hash_token_jti(payload["jti"]))
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/logout")
async def logout(
    response: Response,
    refresh_token: Optional[str] = Cookie(default=None, alias=settings.REFRESH_TOKEN_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
):
    if refresh_token:
        payload = AuthHandler.decode_token(refresh_token)
        if payload and payload.get("jti"):
            refresh_repo = SQLAlchemyRefreshSessionRepository(db)
            await refresh_repo.revoke(AuthHandler.hash_token_jti(payload["jti"]))

    response.delete_cookie(
        key=settings.REFRESH_TOKEN_COOKIE_NAME,
        path="/api/v1/auth",
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.COOKIE_SAMESITE,
    )
    return {"message": "Sessao encerrada."}

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
