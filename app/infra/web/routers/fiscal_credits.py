from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from application.services.audit_service import AuditService
from application.services.fiscal.fiscal_credits_service import FiscalCreditsService
from application.services.fiscal.fiscal_notification_service import FiscalNotificationService
from application.services.fiscal.fiscal_quota_service import FiscalQuotaService
from application.services.plan_access_service import PlanAccessService
from domain.identity import User
from infra.clients.mercado_pago_client import (
    MercadoPagoAuthError,
    MercadoPagoClient,
    MercadoPagoUnavailableError,
    MercadoPagoValidationError,
)
from infra.config.settings import get_settings
from infra.database.setup import get_db
from infra.repositories.audit_repo import SQLAlchemyAuditLogRepository
from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
from infra.repositories.fiscal_repo import SQLAlchemyFiscalNotificationRepository, SQLAlchemyFiscalUsageRepository
from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository, SQLAlchemyUserRepository
from infra.security.market_access import MarketPermission
from infra.security.rate_limiter import enforce_rate_limit_async
from infra.web.dependencies import get_current_user, require_market_access

router = APIRouter()


class CheckoutRequest(BaseModel):
    package_slug: str = Field(..., min_length=1, max_length=32)
    idempotency_key: str = Field(..., min_length=8, max_length=160)


def _decimal_to_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _credits_service(db: AsyncSession) -> FiscalCreditsService:
    settings = get_settings()
    usage_repo = SQLAlchemyFiscalUsageRepository(db)
    quota_service = FiscalQuotaService(usage_repo)
    mp_client = MercadoPagoClient(
        settings.MP_ACCESS_TOKEN,
        sandbox=settings.MP_SANDBOX,
        base_url=settings.MP_BASE_URL,
    )
    plan_access = PlanAccessService(
        SQLAlchemyUserRepository(db),
        SQLAlchemyPlanRepository(db),
        SQLAlchemyBillingSubscriptionRepository(db),
    )
    return FiscalCreditsService(
        credits_repo=usage_repo,
        quota_repo=usage_repo,
        mp_client=mp_client,
        quota_service=quota_service,
        notification_service=FiscalNotificationService(SQLAlchemyFiscalNotificationRepository(db)),
        audit_service=AuditService(SQLAlchemyAuditLogRepository(db)),
        settings=settings,
        plan_access_service=plan_access,
    )


@router.get("/credits/packages", tags=["Fiscal Credits"])
async def list_credit_packages(db: AsyncSession = Depends(get_db)):
    svc = _credits_service(db)
    return {
        "items": [
            {
                "slug": package.slug,
                "emission_count": package.emission_count,
                "price_gross": _decimal_to_str(package.price_gross),
                "price_net_target": _decimal_to_str(package.price_net_target),
            }
            for package in svc.get_packages()
        ]
    }


@router.post("/{market_id}/credits/checkout", tags=["Fiscal Credits"])
async def create_checkout(
    request: Request,
    market_id: uuid.UUID,
    payload: CheckoutRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    await enforce_rate_limit_async(
        request,
        bucket=f"fiscal-credits-checkout:{current_user.id}",
        limit=10,
        window_seconds=60,
    )
    svc = _credits_service(db)
    try:
        result = await svc.initiate_purchase(
            owner_id=current_user.id,
            market_id=market_id,
            package_slug=payload.package_slug,
            idempotency_key=payload.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except MercadoPagoAuthError:
        raise HTTPException(status_code=502, detail="Checkout indisponivel.")
    except MercadoPagoValidationError:
        raise HTTPException(status_code=502, detail="Checkout indisponivel.")
    except MercadoPagoUnavailableError:
        raise HTTPException(status_code=503, detail="Checkout temporariamente indisponivel.")

    return {
        "package_id": str(result.package_id),
        "init_point": result.init_point,
        "package": {
            "slug": result.package.slug,
            "emission_count": result.package.emission_count,
            "price_gross": _decimal_to_str(result.package.price_gross),
            "price_net_target": _decimal_to_str(result.package.price_net_target),
        },
    }


@router.get("/{market_id}/credits/history", tags=["Fiscal Credits"])
async def credits_history(
    market_id: uuid.UUID,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    svc = _credits_service(db)
    items = await svc.get_credits_history(current_user.id, page=page, per_page=per_page)
    return {
        "page": page,
        "per_page": per_page,
        "items": [
            {
                "package_id": str(item.package_id),
                "package_slug": item.package_slug,
                "quantity": item.quantity,
                "remaining": item.remaining,
                "payment_status": item.payment_status,
                "price_gross": _decimal_to_str(item.price_gross),
                "price_net_target": _decimal_to_str(item.price_net_target),
                "valid_from": item.valid_from.isoformat() if item.valid_from else None,
                "valid_until": item.valid_until.isoformat() if item.valid_until else None,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in items
        ],
    }


@router.get("/{market_id}/credits/balance", tags=["Fiscal Credits"])
async def credits_balance(
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    svc = _credits_service(db)
    balance = await svc.get_credits_balance(current_user.id)
    return {
        "period": balance.period,
        "included_limit": balance.included_limit,
        "addon_limit": balance.addon_limit,
        "used_count": balance.used_count,
        "reserved_count": balance.reserved_count,
        "remaining": balance.remaining,
        "percentage_used": balance.percentage_used,
    }
