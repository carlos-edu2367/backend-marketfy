from __future__ import annotations

import uuid
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession


from application.services.audit_service import AuditService
from application.services.fiscal.fiscal_credits_service import FiscalCreditsService
from application.services.fiscal.fiscal_notification_service import FiscalNotificationService
from application.services.fiscal.fiscal_quota_service import FiscalQuotaService
from application.services.plan_access_service import PlanAccessService
from domain.identity import User
from uuid import UUID
from infra.clients.billing_core_client import (
    BillingCoreClient,
    BillingCoreAuthError,
    BillingCoreValidationError,
    BillingCoreUnavailableError,
    BillingCoreRateLimitError,
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


class CustomCheckoutRequest(BaseModel):
    quantity: int = Field(..., ge=1, le=100_000)
    idempotency_key: str = Field(..., min_length=8, max_length=160)


class PackageInfo(BaseModel):
    slug: str
    emission_count: int
    price_gross: str


class CheckoutResponse(BaseModel):
    package_id: UUID
    job_id: str
    message: str
    package: PackageInfo


class CheckoutStatusResponse(BaseModel):
    status: str
    checkout_url: str | None
    bc_payment_id: str | None
    error_code: str | None
    error_message: str | None


def _decimal_to_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _credits_service(db: AsyncSession) -> FiscalCreditsService:
    settings = get_settings()
    usage_repo = SQLAlchemyFiscalUsageRepository(db)
    quota_service = FiscalQuotaService(usage_repo)
    bc_client = BillingCoreClient()
    user_repo = SQLAlchemyUserRepository(db)
    plan_access = PlanAccessService(
        user_repo,
        SQLAlchemyPlanRepository(db),
        SQLAlchemyBillingSubscriptionRepository(db),
    )
    return FiscalCreditsService(
        credits_repo=usage_repo,
        quota_repo=usage_repo,
        mp_client=None,
        bc_client=bc_client,
        user_repo=user_repo,
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


@router.post("/{market_id}/credits/checkout", status_code=202, response_model=CheckoutResponse, tags=["Fiscal Credits"])
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
        if str(exc) == "customer_document_missing":
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "customer_document_missing",
                        "message": "CPF ou CNPJ do usuário é obrigatório para efetuar pagamentos."
                    }
                }
            )
        raise HTTPException(status_code=400, detail=str(exc))
    except (BillingCoreAuthError, BillingCoreValidationError) as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "payment_gateway_unavailable",
                    "message": "Gateway de pagamento temporariamente indisponível. Tente novamente."
                }
            }
        )
    except BillingCoreUnavailableError:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "payment_gateway_unavailable",
                    "message": "Gateway de pagamento temporariamente indisponível. Tente novamente."
                }
            }
        )

    return {
        "package_id": result.package_id,
        "job_id": result.job_id,
        "message": "Pagamento enviado para processamento.",
        "package": {
            "slug": result.package.slug,
            "emission_count": result.package.emission_count,
            "price_gross": _decimal_to_str(result.package.price_gross),
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
        "addon_total": balance.addon_total,
        "used_count": balance.used_count,
        "reserved_count": balance.reserved_count,
        "remaining": balance.remaining,
        "percentage_used": balance.percentage_used,
    }


@router.get("/credits/config", tags=["Fiscal Credits"])
async def credits_config():
    settings = get_settings()
    return {
        "min_qty": settings.FISCAL_CREDIT_MIN_QTY,
        "max_qty": settings.FISCAL_CREDIT_MAX_QTY,
        "unit_price": str(settings.FISCAL_CREDIT_UNIT_PRICE),
    }


@router.get("/credits/price", tags=["Fiscal Credits"])
async def preview_price(qty: int = Query(..., ge=1, le=100_000)):
    settings = get_settings()
    if qty < settings.FISCAL_CREDIT_MIN_QTY:
        raise HTTPException(
            status_code=400,
            detail=f"Minimo {settings.FISCAL_CREDIT_MIN_QTY} creditos por compra.",
        )
    price = (settings.FISCAL_CREDIT_UNIT_PRICE * qty).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return {
        "quantity": qty,
        "price_gross": str(price),
        "unit_price": str(settings.FISCAL_CREDIT_UNIT_PRICE),
    }


@router.post("/{market_id}/credits/checkout/custom", status_code=202, tags=["Fiscal Credits"])
async def create_custom_checkout(
    request: Request,
    market_id: uuid.UUID,
    payload: CustomCheckoutRequest,
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
    settings = get_settings()
    if payload.quantity < settings.FISCAL_CREDIT_MIN_QTY:
        raise HTTPException(
            status_code=400,
            detail=f"Minimo {settings.FISCAL_CREDIT_MIN_QTY} creditos por compra.",
        )
    if payload.quantity > settings.FISCAL_CREDIT_MAX_QTY:
        raise HTTPException(
            status_code=400,
            detail=f"Maximo {settings.FISCAL_CREDIT_MAX_QTY} creditos por compra.",
        )
    price_gross = (settings.FISCAL_CREDIT_UNIT_PRICE * payload.quantity).quantize(
        Decimal("0.01"), ROUND_HALF_UP
    )
    svc = _credits_service(db)
    try:
        result = await svc.initiate_custom_purchase(
            owner_id=current_user.id,
            market_id=market_id,
            quantity=payload.quantity,
            price_gross=price_gross,
            idempotency_key=payload.idempotency_key,
        )
    except ValueError as exc:
        if str(exc) == "customer_document_missing":
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "customer_document_missing",
                        "message": "CPF ou CNPJ do usuário é obrigatório para efetuar pagamentos."
                    }
                }
            )
        raise HTTPException(status_code=400, detail=str(exc))
    except (BillingCoreAuthError, BillingCoreValidationError) as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "payment_gateway_unavailable",
                    "message": "Gateway de pagamento temporariamente indisponível. Tente novamente."
                }
            }
        )
    except BillingCoreUnavailableError:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "payment_gateway_unavailable",
                    "message": "Gateway de pagamento temporariamente indisponível. Tente novamente."
                }
            }
        )

    return {
        "package_id": str(result.package_id),
        "job_id": result.job_id,
        "quantity": payload.quantity,
        "price_gross": str(price_gross),
    }


@router.get("/{market_id}/credits/checkout/status/{job_id}", response_model=CheckoutStatusResponse, tags=["Fiscal Credits"])
async def get_checkout_status(
    request: Request,
    market_id: uuid.UUID,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    await enforce_rate_limit_async(
        request,
        bucket=f"fiscal-checkout-status:{current_user.id}",
        limit=60,
        window_seconds=60,
    )
    svc = _credits_service(db)
    
    pkg = await svc.credits_repo.get_package_by_job_id(job_id)
    if not pkg:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    if pkg.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    if pkg.purchased_at_market_id != market_id and pkg.market_id != market_id:
        raise HTTPException(status_code=404, detail="Job não encontrado.")

    try:
        status = await svc.get_checkout_status(job_id)
        return CheckoutStatusResponse(**status)
    except (BillingCoreAuthError, BillingCoreValidationError) as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "payment_gateway_unavailable",
                    "message": "Gateway de pagamento temporariamente indisponível. Tente novamente."
                }
            }
        )
    except BillingCoreUnavailableError:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "payment_gateway_unavailable",
                    "message": "Gateway de pagamento temporariamente indisponível. Tente novamente."
                }
            }
        )

