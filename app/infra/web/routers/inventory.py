import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from infra.database.setup import get_db
from infra.security.market_access import MarketPermission
from infra.web.dependencies import (
    get_inventory_service,
    require_market_access,
)
from application.services.inventory_service import InventoryService
from application.dtos import ProductCreateDTO, StockMovementDTO, StockMovementResponseDTO, ProductSyncResponseDTO, EditProductDTO
from domain.shared import BusinessRuleException, ValidationException
from infra.config.logger import get_logger

router = APIRouter()
logger = get_logger("inventory")

@router.get("/{market_id}/products")
async def list_market_products(
    market_id: uuid.UUID,
    service: InventoryService = Depends(get_inventory_service),
    market=Depends(require_market_access(MarketPermission.INVENTORY_READ)),
):
    return await service.list_products(market_id)

@router.get("/{market_id}/products/sync", response_model=ProductSyncResponseDTO)
async def sync_products(
    market_id: uuid.UUID,
    last_updated: Optional[str] = Query(None, description="ISO timestamp da última sincronização"),
    service: InventoryService = Depends(get_inventory_service),
    market=Depends(require_market_access(MarketPermission.INVENTORY_READ)),
):
    """
    Endpoint otimizado para sincronização offline.
    Retorna apenas o que mudou (Delta Sync).
    """
    return await service.get_sync_data(market_id, last_updated)

@router.post("/{market_id}/products", status_code=status.HTTP_201_CREATED)
async def create_product(
    market_id: uuid.UUID,
    dto: ProductCreateDTO,
    service: InventoryService = Depends(get_inventory_service),
    market=Depends(require_market_access(MarketPermission.INVENTORY_WRITE)),
):
    try:
        return await service.create_product(market_id, dto)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Erro interno ao criar produto")
        raise HTTPException(status_code=500, detail="Erro interno.")

@router.put("/{market_id}/product/{product_id}", status_code=status.HTTP_201_CREATED)
async def update_product(
    market_id: uuid.UUID,
    product_id: uuid.UUID,
    dto: EditProductDTO,
    service: InventoryService = Depends(get_inventory_service),
    market=Depends(require_market_access(MarketPermission.INVENTORY_WRITE)),
):
    try:
        product = await service.product_repo.get_by_id(product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Produto não encontrado")
        if product.market_id != market_id:
            raise HTTPException(status_code=403, detail="Você não tem permissão para modificar este produto")
        ok = await service.edit_product(product, dto)
        if ok:
            return

    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Erro interno ao atualizar produto")
        raise HTTPException(status_code=500, detail="Erro interno.")

@router.delete("/{market_id}/products/{product_id}")
async def delete_product(
    market_id: uuid.UUID,
    product_id: uuid.UUID,
    service: InventoryService = Depends(get_inventory_service),
    market=Depends(require_market_access(MarketPermission.INVENTORY_WRITE)),
):
    try:
        return await service.delete_product(market_id, product_id)
    except BusinessRuleException as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/{market_id}/movements", status_code=status.HTTP_201_CREATED)
async def add_stock_movement(
    market_id: uuid.UUID,
    dto: StockMovementDTO,
    service: InventoryService = Depends(get_inventory_service),
    market=Depends(require_market_access(MarketPermission.INVENTORY_WRITE)),
):
    try:
        return await service.register_movement(market_id, dto)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{market_id}/products/{product_id}/history", response_model=List[StockMovementResponseDTO])
async def get_product_history(
    market_id: uuid.UUID,
    product_id: uuid.UUID,
    service: InventoryService = Depends(get_inventory_service),
    market=Depends(require_market_access(MarketPermission.INVENTORY_READ)),
):
    """
    Novo Endpoint: Histórico de movimentações do produto.
    """
    try:
        return await service.get_product_history(market_id, product_id)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))
