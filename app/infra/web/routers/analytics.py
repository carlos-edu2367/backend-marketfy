import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from application.dtos import (
    AdminAnalyticsDTO,
    FinancialDashboardUnifiedDTO,
    MarketAnalyticsDTO,
)
from application.services.analytics_service import AnalyticsService
from application.services.plan_access_service import PlanAccessService, PlanFeature
from domain.identity import User
from infra.database.setup import get_db
from infra.repositories.analytics_repo import AnalyticsRepository
from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
from infra.repositories.sqlalchemy_repos import (
    SQLAlchemyCustomerRepository,
    SQLAlchemyFinancialTransactionRepository,
    SQLAlchemyUserRepository,
    SQLAlchemyPlanRepository,
)
from infra.security.market_access import MarketPermission
from infra.web.dependencies import (
    get_current_user,
    require_admin,
    require_market_access,
)

router = APIRouter()


async def get_analytics_service(db: AsyncSession = Depends(get_db)):
    repo = AnalyticsRepository(db)
    cust_repo = SQLAlchemyCustomerRepository(db)
    trans_repo = SQLAlchemyFinancialTransactionRepository(db)
    return AnalyticsService(repo, cust_repo, trans_repo)


@router.get("/admin/dashboard", response_model=AdminAnalyticsDTO)
async def get_admin_dashboard(
    service: AnalyticsService = Depends(get_analytics_service),
    current_user: User = Depends(require_admin),
):
    return await service.get_admin_dashboard()


@router.get("/financial", response_model=FinancialDashboardUnifiedDTO)
async def get_financial_dashboard_legacy(
    period: str = Query(..., pattern="^(month|year)$"),
    date: str = Query(...),
    current_user: User = Depends(get_current_user),
):
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Rota legada desativada. Use /api/v1/analytics/{market_id}/financial.",
    )


@router.get("/{market_id}/dashboard", response_model=MarketAnalyticsDTO)
async def get_market_dashboard(
    market_id: uuid.UUID,
    service: AnalyticsService = Depends(get_analytics_service),
    market=Depends(require_market_access(MarketPermission.ANALYTICS_READ)),
):
    return await service.get_market_dashboard(market_id)


@router.get("/{market_id}/financial", response_model=FinancialDashboardUnifiedDTO)
async def get_financial_dashboard_unified(
    market_id: uuid.UUID,
    period: str = Query(..., pattern="^(month|year)$"),
    date: str = Query(...),
    service: AnalyticsService = Depends(get_analytics_service),
    market=Depends(require_market_access(MarketPermission.ANALYTICS_READ)),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Gate de feature: financeiro é feature paga
    plan_svc = PlanAccessService(
        user_repo=SQLAlchemyUserRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        subscription_repo=SQLAlchemyBillingSubscriptionRepository(db),
    )
    access = await plan_svc.check_feature(current_user.id, PlanFeature.FINANCE)
    if not access.allowed:
        raise HTTPException(status_code=403, detail=access.reason)
    return await service.get_financial_unified(market_id, period, date)


@router.get("/{market_id}/evolution")
async def get_sales_evolution(
    market_id: uuid.UUID,
    view: str = Query("monthly", pattern="^(monthly|yearly)$"),
    year: int = Query(default=datetime.now().year),
    month: Optional[int] = Query(default=datetime.now().month),
    service: AnalyticsService = Depends(get_analytics_service),
    market=Depends(require_market_access(MarketPermission.ANALYTICS_READ)),
):
    return await service.get_sales_evolution(market_id, view, year, month)


@router.get("/{market_id}/payments")
async def get_payment_methods(
    market_id: uuid.UUID,
    year: int = Query(default=datetime.now().year),
    month: int = Query(default=datetime.now().month),
    service: AnalyticsService = Depends(get_analytics_service),
    market=Depends(require_market_access(MarketPermission.ANALYTICS_READ)),
):
    return await service.get_payment_methods_breakdown(market_id, year, month)
