import uuid
from decimal import Decimal
from typing import Optional, List, Dict
from datetime import datetime
from domain.inventory import Product, StockMovementType
from domain.interfaces import ProductRepositoryInterface, MarketRepositoryInterface
from domain.shared import BusinessRuleException
from application.dtos import ProductCreateDTO, StockMovementDTO, StockMovementResponseDTO, ProductSyncResponseDTO

class InventoryService:
    def __init__(self, product_repo: ProductRepositoryInterface, market_repo: MarketRepositoryInterface):
        self.product_repo = product_repo
        self.market_repo = market_repo

    async def create_product(self, market_id: uuid.UUID, dto: ProductCreateDTO) -> Product:
        existing = await self.product_repo.get_by_code(market_id, dto.code)
        if existing:
            raise BusinessRuleException(f"Produto com código interno '{dto.code}' já existe neste mercado.")
        
        product = Product(
            market_id=market_id,
            name=dto.name,
            code=dto.code,
            barcode=dto.barcode,
            price=dto.price,
            cost_price=dto.cost_price,
            ncm=dto.ncm,
            origin=dto.origin
        )
        return await self.product_repo.save(product)

    async def list_products(self, market_id: uuid.UUID) -> List[Product]:
        return await self.product_repo.list_by_market(market_id)

    async def register_movement(self, market_id: uuid.UUID, dto: StockMovementDTO):
        product = await self.product_repo.get_by_id(dto.product_id)
        if not product:
            raise BusinessRuleException("Produto não encontrado.")
        if product.market_id != market_id:
            raise BusinessRuleException("Produto não pertence a este mercado.")

        try:
            move_type = StockMovementType(dto.movement_type)
        except ValueError:
             raise BusinessRuleException(f"Tipo de movimento inválido: {dto.movement_type}")

        if move_type in [StockMovementType.ADJUSTMENT_ADD, StockMovementType.ADJUSTMENT_SUB, StockMovementType.LOSS]:
            if not dto.reason:
                raise BusinessRuleException("Motivo é obrigatório para ajustes de estoque.")

        product.add_movement(move_type, dto.quantity, dto.reason)
        return await self.product_repo.save(product)

    async def get_product_history(self, market_id: uuid.UUID, product_id: uuid.UUID) -> List[StockMovementResponseDTO]:
        """
        Retorna o histórico de movimentações de um produto.
        """
        # Verifica se o produto pertence ao mercado para segurança
        product = await self.product_repo.get_by_id(product_id)
        if not product or product.market_id != market_id:
            raise BusinessRuleException("Produto não encontrado ou acesso negado.")

        # CORREÇÃO: Passando market_id e product_id conforme assinatura do Repositório
        movements = await self.product_repo.list_movements(market_id, product_id)
        
        # CORREÇÃO: Preenchendo todos os campos obrigatórios do DTO para evitar ValidationError
        return [
            StockMovementResponseDTO(
                id=m.id,
                product_id=m.product_id, # Campo obrigatório adicionado
                movement_type=m.movement_type.value if hasattr(m.movement_type, 'value') else str(m.movement_type), # Renomeado de type para movement_type
                quantity=m.quantity,
                cost_price=m.cost_price or Decimal("0.00"), # Campo obrigatório adicionado (com fallback)
                reason=m.reason,
                created_at=m.created_at
            ) for m in movements
        ]

    async def delete_product(self, market_id: uuid.UUID, product_id: uuid.UUID):
        """Realiza Soft Delete do produto."""
        product = await self.product_repo.get_by_id(product_id)
        if not product or product.market_id != market_id:
            raise BusinessRuleException("Produto não encontrado.")
        
        product.mark_deleted()
        await self.product_repo.save(product)
        return {"message": "Produto removido com sucesso."}

    async def get_sync_data(self, market_id: uuid.UUID, last_updated_str: Optional[str] = None) -> ProductSyncResponseDTO:
        """
        Retorna o delta de modificações para o frontend sincronizar.
        Se last_updated_str for None, retorna todos os ativos (Full Sync).
        Se informado, retorna criados/modificados e IDs deletados desde então.
        """
        if not last_updated_str:
            # Full Sync
            all_products = await self.product_repo.list_by_market(market_id, active_only=True)
            return ProductSyncResponseDTO(
                updated=[self._serialize_product(p) for p in all_products],
                deleted=[],
                server_time=datetime.utcnow()
            )
        
        try:
            # Tenta parsear formato ISO
            last_updated = datetime.fromisoformat(last_updated_str.replace('Z', '+00:00'))
        except ValueError:
             # Se data inválida, fallback para full sync
             all_products = await self.product_repo.list_by_market(market_id, active_only=True)
             return ProductSyncResponseDTO(
                updated=[self._serialize_product(p) for p in all_products],
                deleted=[],
                server_time=datetime.utcnow()
            )

        delta = await self.product_repo.get_delta(market_id, last_updated)
        
        return ProductSyncResponseDTO(
            updated=[self._serialize_product(p) for p in delta['updated']],
            deleted=delta['deleted'],
            server_time=datetime.utcnow()
        )

    def _serialize_product(self, product: Product) -> dict:
        """Helper para serializar produto para dict simples (evita erro Pydantic com dataclass)."""
        return {
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
            "updated_at": product.updated_at
        }