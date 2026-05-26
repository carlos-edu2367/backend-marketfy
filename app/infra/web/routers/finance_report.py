import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.setup import get_db
from application.services.finance_report_service import FinanceReportService
from application.services.plan_access_service import PlanAccessService, PlanFeature
from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
from infra.repositories.sqlalchemy_repos import (
    SQLAlchemySaleRepository,
    SQLAlchemyFinancialTransactionRepository,
    SQLAlchemyMarketRepository,
    SQLAlchemyUserRepository,
    SQLAlchemyPlanRepository,
)
from infra.security.market_access import MarketPermission
from infra.web.dependencies import get_current_user, require_market_access

router = APIRouter()


async def get_report_service(db: AsyncSession = Depends(get_db)):
    sale_repo = SQLAlchemySaleRepository(db)
    trans_repo = SQLAlchemyFinancialTransactionRepository(db)
    market_repo = SQLAlchemyMarketRepository(db)
    return FinanceReportService(sale_repo, trans_repo, market_repo)


@router.get("/{market_id}/download")
async def download_financial_report(
    market_id: uuid.UUID,
    year: int = Query(..., description="Ano do relatório"),
    month: int = Query(..., description="Mês do relatório"),
    format: str = Query("excel", regex="^(excel|pdf)$"),
    service: FinanceReportService = Depends(get_report_service),
    market=Depends(require_market_access(MarketPermission.REPORTS_READ)),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Gera e baixa o relatório financeiro detalhado (Entradas e Saídas).
    Autorização validada antes de qualquer geração de arquivo.
    """
    # Gate de feature: relatórios são feature paga
    plan_svc = PlanAccessService(
        user_repo=SQLAlchemyUserRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        subscription_repo=SQLAlchemyBillingSubscriptionRepository(db),
    )
    access = await plan_svc.check_feature(current_user.id, PlanFeature.REPORTS)
    if not access.allowed:
        raise HTTPException(status_code=403, detail=access.reason)

    if format == 'excel':
        file_stream = await service.export_to_excel(market_id, year, month)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"relatorio_financeiro_{year}_{month:02d}.xlsx"
    else:
        file_stream = await service.export_to_pdf(market_id, year, month)
        media_type = "application/pdf"
        filename = f"relatorio_financeiro_{year}_{month:02d}.pdf"

    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"'
    }

    return StreamingResponse(
        iter([file_stream.getvalue()]),
        media_type=media_type,
        headers=headers
    )
