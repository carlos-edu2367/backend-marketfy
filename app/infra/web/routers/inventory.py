import uuid
from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from infra.database.setup import get_db
from infra.security.market_access import MarketPermission
from infra.web.dependencies import (
    get_inventory_service,
    get_current_user,
    get_audit_service,
    require_market_access,
)
from application.services.inventory_service import InventoryService
from application.services.audit_service import AuditService
from application.dtos import (
    ProductCreateDTO,
    StockMovementDTO,
    StockMovementResponseDTO,
    ProductSyncResponseDTO,
    EditProductDTO,
)
from application.fiscal_tax_dtos import ProductTaxRuleAssignmentRequest
from domain.shared import BusinessRuleException, ValidationException
from domain.identity import User
from infra.observability.audit import record_audit_event
from infra.config.logger import get_logger

router = APIRouter()
logger = get_logger("inventory")

@router.get("/{market_id}/products")
async def list_market_products(
    market_id: uuid.UUID,
    service: InventoryService = Depends(get_inventory_service),
    db: AsyncSession = Depends(get_db),
    market=Depends(require_market_access(MarketPermission.INVENTORY_READ)),
):
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    products = await service.list_products(market_id)
    statuses = await SQLAlchemyProductTaxRuleRepository(db).list_product_fiscal_status(
        market_id, [product.id for product in products], date.today()
    )
    return [
        {
            "id": product.id,
            "market_id": product.market_id,
            "name": product.name,
            "code": product.code,
            "barcode": product.barcode,
            "price": product.price,
            "cost_price": product.cost_price,
            "current_stock": product.current_stock,
            "active": product.active,
            "ncm": product.ncm,
            "origin": product.origin,
            "tax_rule_id": product.tax_rule_id,
            "fiscal_status": statuses.get(product.id, "missing"),
            "updated_at": product.updated_at,
        }
        for product in products
    ]


@router.post("/{market_id}/products/tax-rule-assignment")
async def assign_product_tax_rule(
    request: Request,
    market_id: uuid.UUID,
    dto: ProductTaxRuleAssignmentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    """Temporary inventory alias for the canonical fiscal assignment route."""
    from infra.web.routers.fiscal_tax_rules import assign_tax_rule_products

    return await assign_tax_rule_products(
        request=request,
        market_id=market_id,
        dto=dto,
        db=db,
        current_user=current_user,
        audit=audit,
    )

@router.get("/{market_id}/products/sync", response_model=ProductSyncResponseDTO)
async def sync_products(
    market_id: uuid.UUID,
    last_updated: Optional[str] = Query(None, description="ISO timestamp da última sincronização"),
    service: InventoryService = Depends(get_inventory_service),
    db: AsyncSession = Depends(get_db),
    market=Depends(require_market_access(MarketPermission.INVENTORY_READ)),
):
    """
    Endpoint otimizado para sincronização offline.
    Retorna apenas o que mudou (Delta Sync).
    """
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    response = await service.get_sync_data(market_id, last_updated)
    product_ids = [item["id"] for item in response.updated]
    statuses = await SQLAlchemyProductTaxRuleRepository(db).list_product_fiscal_status(
        market_id, product_ids, date.today()
    )
    response.updated = [
        {**item, "fiscal_status": statuses.get(item["id"], "missing")}
        for item in response.updated
    ]
    return response

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
