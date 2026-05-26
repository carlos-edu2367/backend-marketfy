from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from application.services.audit_service import AuditService
from application.services.fiscal.fiscal_quota_service import FiscalQuotaService
from domain.identity import User
from infra.database.setup import get_db
from infra.observability.audit import record_audit_event
from infra.repositories.audit_repo import SQLAlchemyAuditLogRepository
from infra.repositories.fiscal_repo import SQLAlchemyFiscalUsageRepository
from infra.security.rate_limiter import enforce_rate_limit_async
from infra.web.dependencies import get_current_user, require_admin

router = APIRouter()


class GrantCreditsRequest(BaseModel):
    owner_id: uuid.UUID
    amount: int = Field(..., ge=1, le=50_000)
    reason: str = Field(..., min_length=3, max_length=255)
    idempotency_key: str = Field(..., min_length=8, max_length=160)


def _current_period() -> str:
    now = datetime.utcnow()
    return f"{now.year}{now.month:02d}"


@router.post("/fiscal/credits/grant", tags=["Admin Fiscal"])
async def admin_grant_credits(
    request: Request,
    payload: GrantCreditsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    await enforce_rate_limit_async(
        request,
        bucket=f"admin-fiscal-grant:{current_user.id}",
        limit=30,
        window_seconds=60,
    )
    usage_repo = SQLAlchemyFiscalUsageRepository(db)
    quota_service = FiscalQuotaService(usage_repo)
    audit_service = AuditService(SQLAlchemyAuditLogRepository(db))

    await quota_service.add_addon_credits(
        owner_id=payload.owner_id,
        period=_current_period(),
        amount=payload.amount,
        idempotency_key=payload.idempotency_key,
    )

    await record_audit_event(
        audit_service,
        request,
        actor=current_user,
        action="admin_fiscal_credits_granted",
        resource_type="fiscal_usage_counter",
        resource_id=str(payload.owner_id),
        result="success",
        metadata={"amount": payload.amount, "reason": payload.reason},
    )

    return {"granted": payload.amount, "owner_id": str(payload.owner_id)}
