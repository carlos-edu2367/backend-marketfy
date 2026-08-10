from __future__ import annotations

import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from application.services.audit_service import AuditService
from application.services.fiscal.fiscal_credits_service import FiscalCreditsService
from application.services.fiscal.fiscal_notification_service import FiscalNotificationService
from application.services.fiscal.fiscal_quota_service import FiscalQuotaService
from application.services.plan_access_service import PlanAccessService
from domain.identity import User
from infra.config.settings import get_settings
from infra.database.setup import get_db
from infra.repositories.audit_repo import SQLAlchemyAuditLogRepository
from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
from infra.repositories.fiscal_repo import (
    SQLAlchemyFiscalNotificationRepository,
    SQLAlchemyFiscalUsageRepository,
)
from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository, SQLAlchemyUserRepository
from infra.security.rate_limiter import enforce_rate_limit_async, get_client_ip
from infra.web.dependencies import require_admin

router = APIRouter()

GrantReasonLiteral = Literal["courtesy", "compensation", "bonus", "migration"]


class GrantCreditsRequest(BaseModel):
    owner_id: uuid.UUID
    amount: int = Field(..., ge=1, le=50_000)
    reason_code: GrantReasonLiteral
    note: Optional[str] = Field(None, max_length=500)
    valid_days: int = Field(365, ge=1, le=1095)
    idempotency_key: str = Field(..., min_length=8, max_length=160)


def _credits_service(db: AsyncSession) -> FiscalCreditsService:
    settings = get_settings()
    usage_repo = SQLAlchemyFiscalUsageRepository(db)
    user_repo = SQLAlchemyUserRepository(db)
    plan_access = PlanAccessService(
        user_repo,
        SQLAlchemyPlanRepository(db),
        SQLAlchemyBillingSubscriptionRepository(db),
    )
    return FiscalCreditsService(
        credits_repo=usage_repo,
        quota_repo=usage_repo,
        quota_service=FiscalQuotaService(usage_repo),
        notification_service=FiscalNotificationService(
            SQLAlchemyFiscalNotificationRepository(db)
        ),
        audit_service=AuditService(SQLAlchemyAuditLogRepository(db)),
        settings=settings,
        plan_access_service=plan_access,
        user_repo=user_repo,
    )


@router.post("/fiscal/credits/grant", status_code=201, tags=["Admin Fiscal"])
async def admin_grant_credits(
    request: Request,
    response: Response,
    payload: GrantCreditsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Concede créditos NFC-e a um usuário, sem cobrança.

    201 na concessão; 200 quando a idempotency_key já foi usada (replay).
    """
    await enforce_rate_limit_async(
        request,
        bucket=f"admin-fiscal-grant:{current_user.id}",
        limit=30,
        window_seconds=60,
    )

    user_repo = SQLAlchemyUserRepository(db)
    owner = await user_repo.get_by_id(payload.owner_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    svc = _credits_service(db)
    result = await svc.grant_credits(
        owner_id=payload.owner_id,
        amount=payload.amount,
        reason_code=payload.reason_code,
        granted_by_id=current_user.id,
        idempotency_key=payload.idempotency_key,
        note=payload.note,
        valid_days=payload.valid_days,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    if not result.created:
        response.status_code = 200

    balance = await svc.get_credits_balance(payload.owner_id)
    package = result.package
    return {
        "package_id": str(package.id),
        "owner_id": str(package.owner_id),
        "granted": package.quantity,
        "reason_code": package.grant_reason_code,
        "valid_until": package.valid_until.isoformat() if package.valid_until else None,
        "created": result.created,
        "balance_after": {
            "included_limit": balance.included_limit,
            "addon_limit": balance.addon_limit,
            "remaining": balance.remaining,
        },
    }


@router.get("/fiscal/credits/{owner_id}", tags=["Admin Fiscal"])
async def admin_get_credits(
    owner_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Saldo e últimos pacotes do owner — alimenta o modal de concessão."""
    user_repo = SQLAlchemyUserRepository(db)
    owner = await user_repo.get_by_id(owner_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    svc = _credits_service(db)
    balance = await svc.get_credits_balance(owner_id)
    packages = await svc.get_credits_history(owner_id, page=1, per_page=10)

    return {
        "owner_id": str(owner_id),
        "period": balance.period,
        "included_limit": balance.included_limit,
        "addon_limit": balance.addon_limit,
        "addon_total": balance.addon_total,
        "used_count": balance.used_count,
        "remaining": balance.remaining,
        "packages": [
            {
                "package_id": str(item.package_id),
                "package_type": item.package_type,
                "package_slug": item.package_slug,
                "grant_reason_code": item.grant_reason_code,
                "quantity": item.quantity,
                "remaining": item.remaining,
                "payment_status": item.payment_status,
                "valid_until": item.valid_until.isoformat() if item.valid_until else None,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in packages
        ],
    }
