import uuid
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from infra.database.setup import get_db
from domain.identity import User
from application.services.finance_report_service import FinanceReportService
from infra.repositories.sqlalchemy_repos import (
    SQLAlchemySaleRepository, 
    SQLAlchemyFinancialTransactionRepository,
    SQLAlchemyMarketRepository
)
from infra.web.routers.auth import get_current_user

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
    current_user: User = Depends(get_current_user)
):
    """
    Gera e baixa o relatório financeiro detalhado (Entradas e Saídas).
    """
    # 1. Gera o arquivo em memória
    if format == 'excel':
        file_stream = await service.export_to_excel(market_id, year, month)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"relatorio_financeiro_{year}_{month:02d}.xlsx"
    else:
        file_stream = await service.export_to_pdf(market_id, year, month)
        media_type = "application/pdf"
        filename = f"relatorio_financeiro_{year}_{month:02d}.pdf"

    # 2. Retorna como stream para download
    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"'
    }
    
    return StreamingResponse(
        iter([file_stream.getvalue()]), 
        media_type=media_type, 
        headers=headers
    )