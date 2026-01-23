import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.setup import get_db
from infra.web.dependencies import get_sales_service, get_current_user, PermissionChecker
from application.services.sales_service import SalesService
from application.dtos import (
    SaleSyncDTO, OpenBoxDTO, CloseBoxDTO, MarketDashboardStatsDTO, 
    TerminalCreateDTO, TerminalResponseDTO, SaleResponseDTO, 
    DailySalesDTO, BoxResponseDTO
)
from domain.shared import BusinessRuleException

router = APIRouter()

# ==================================================================================
# TERMINAIS (PDVs)
# ==================================================================================

@router.get("/{market_id}/terminals", response_model=List[TerminalResponseDTO])
async def list_terminals(
    market_id: uuid.UUID,
    service: SalesService = Depends(get_sales_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    return await service.list_terminals(market_id)

@router.post("/{market_id}/terminals", status_code=status.HTTP_201_CREATED, response_model=TerminalResponseDTO)
async def create_terminal(
    market_id: uuid.UUID,
    dto: TerminalCreateDTO,
    service: SalesService = Depends(get_sales_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    try:
        return await service.create_terminal(market_id, dto)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))

# ==================================================================================
# GESTÃO DE CAIXA (BOX)
# ==================================================================================

@router.get("/{market_id}/terminals/{terminal_id}/box", response_model=Optional[BoxResponseDTO])
async def get_current_box(
    market_id: uuid.UUID,
    terminal_id: uuid.UUID,
    service: SalesService = Depends(get_sales_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Verifica se há um caixa aberto neste terminal."""
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    return await service.get_open_box(market_id, terminal_id)

@router.post("/{market_id}/terminals/{terminal_id}/box/open", response_model=BoxResponseDTO)
async def open_box(
    market_id: uuid.UUID,
    terminal_id: uuid.UUID,
    dto: OpenBoxDTO,
    service: SalesService = Depends(get_sales_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    try:
        # Se operator_id não vier no DTO, usa o usuário logado
        op_id = dto.operator_id or current_user.id
        return await service.open_box(market_id, terminal_id, op_id, dto.initial_balance)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{market_id}/terminals/{terminal_id}/box/close", response_model=BoxResponseDTO)
async def close_box(
    market_id: uuid.UUID,
    terminal_id: uuid.UUID,
    dto: CloseBoxDTO,
    service: SalesService = Depends(get_sales_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    try:
        return await service.close_box(market_id, terminal_id, dto)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{market_id}/terminals/{terminal_id}/box/{box_id}/close", response_model=BoxResponseDTO)
async def close_box_with_id(
    market_id: uuid.UUID,
    terminal_id: uuid.UUID,
    box_id: uuid.UUID,
    dto: CloseBoxDTO,
    service: SalesService = Depends(get_sales_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    try:
        return await service.close_box(market_id, terminal_id, dto)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))

# ==================================================================================
# DASHBOARD (MOVIDO PARA CIMA - PRECEDÊNCIA DE ROTA)
# ==================================================================================

@router.get("/{market_id}/dashboard", response_model=MarketDashboardStatsDTO)
async def get_market_dashboard(
    market_id: uuid.UUID,
    service: SalesService = Depends(get_sales_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retorna os dados principais do PDV (Vendas Hoje, Caixas Abertos, etc)."""
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    return await service.get_dashboard_stats(market_id)

@router.get("/{market_id}/dashboard/summary", response_model=List[DailySalesDTO])
async def get_sales_summary(
    market_id: uuid.UUID,
    start_date: str = Query(..., description="Data inicial YYYY-MM-DD"),
    end_date: str = Query(..., description="Data final YYYY-MM-DD"),
    service: SalesService = Depends(get_sales_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Gráfico de vendas por período."""
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    try:
        return await service.get_sales_summary(market_id, start_date, end_date)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))

# ==================================================================================
# VENDAS E SYNC
# ==================================================================================

@router.post("/{market_id}/sync", response_model=List[SaleResponseDTO])
async def sync_sales(
    market_id: uuid.UUID,
    dto: SaleSyncDTO,
    service: SalesService = Depends(get_sales_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Recebe lote de vendas offline para sincronização."""
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    return await service.process_sync(market_id, dto.sales)

@router.get("/{market_id}", response_model=List[SaleResponseDTO])
async def list_sales(
    market_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    service: SalesService = Depends(get_sales_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retorna histórico de vendas recentes."""
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    return await service.list_sales(market_id, limit, offset)

# ROTA DE DETALHES DE VENDA
# Deve ficar por último ou depois de rotas fixas como /dashboard ou /sync
@router.get("/{market_id}/{sale_id}", response_model=Optional[SaleResponseDTO])
async def get_sale(
    market_id: uuid.UUID,
    sale_id: uuid.UUID,
    service: SalesService = Depends(get_sales_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Detalha uma venda específica (Itens e Pagamentos)."""
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    sale = await service.get_sale_by_id(market_id, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Venda não encontrada.")
    return sale

@router.post("/{market_id}/{sale_id}/cancel", response_model=SaleResponseDTO)
async def cancel_sale(
    market_id: uuid.UUID,
    sale_id: uuid.UUID,
    service: SalesService = Depends(get_sales_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    try:
        return await service.cancel_sale(market_id, sale_id)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))