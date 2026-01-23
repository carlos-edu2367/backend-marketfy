import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from infra.database.setup import get_db
from infra.web.dependencies import (
    get_inventory_service, get_current_user, PermissionChecker
)
from application.services.inventory_service import InventoryService
from application.dtos import ProductCreateDTO, StockMovementDTO, StockMovementResponseDTO, ProductSyncResponseDTO
from domain.shared import BusinessRuleException, ValidationException

router = APIRouter()

@router.get("/{market_id}/products")
async def list_market_products(
    market_id: uuid.UUID,
    service: InventoryService = Depends(get_inventory_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    # Mantendo compatibilidade com código antigo, mas idealmente front deve migrar para /sync
    return await service.list_products(market_id)

@router.get("/{market_id}/products/sync", response_model=ProductSyncResponseDTO)
async def sync_products(
    market_id: uuid.UUID,
    last_updated: Optional[str] = Query(None, description="ISO timestamp da última sincronização"),
    service: InventoryService = Depends(get_inventory_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Endpoint otimizado para sincronização offline.
    Retorna apenas o que mudou (Delta Sync).
    """
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    return await service.get_sync_data(market_id, last_updated)

@router.post("/{market_id}/products", status_code=status.HTTP_201_CREATED)
async def create_product(
    market_id: uuid.UUID,
    dto: ProductCreateDTO,
    service: InventoryService = Depends(get_inventory_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    try:
        return await service.create_product(market_id, dto)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Erro interno.")

@router.delete("/{market_id}/products/{product_id}")
async def delete_product(
    market_id: uuid.UUID,
    product_id: uuid.UUID,
    service: InventoryService = Depends(get_inventory_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    try:
        return await service.delete_product(market_id, product_id)
    except BusinessRuleException as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/{market_id}/movements", status_code=status.HTTP_201_CREATED)
async def add_stock_movement(
    market_id: uuid.UUID,
    dto: StockMovementDTO,
    service: InventoryService = Depends(get_inventory_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    try:
        return await service.register_movement(market_id, dto)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{market_id}/products/{product_id}/history", response_model=List[StockMovementResponseDTO])
async def get_product_history(
    market_id: uuid.UUID,
    product_id: uuid.UUID,
    service: InventoryService = Depends(get_inventory_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Novo Endpoint: Histórico de movimentações do produto.
    """
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    try:
        return await service.get_product_history(market_id, product_id)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))