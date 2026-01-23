import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional, List
from datetime import datetime
from domain.shared import Entity, BusinessRuleException

# =============================================================================
# ENUMS
# =============================================================================

class StockMovementType(Enum):
    ENTRY = "entrada"
    SALE = "venda"
    ADJUSTMENT_ADD = "ajuste_entrada"
    ADJUSTMENT_SUB = "ajuste_saida"
    RETURN = "devolucao"
    LOSS = "sangria_estoque"

# =============================================================================
# ENTIDADES
# =============================================================================

@dataclass
class StockMovement(Entity):
    product_id: uuid.UUID
    movement_type: StockMovementType
    quantity: Decimal
    cost_price: Optional[Decimal] = None
    reason: Optional[str] = None
    
    def __post_init__(self):
        if self.quantity <= 0:
            raise BusinessRuleException("Quantidade da movimentação deve ser positiva.")

@dataclass
class Product(Entity):
    market_id: uuid.UUID
    name: str
    code: str
    barcode: Optional[str]
    price: Decimal
    cost_price: Decimal = Decimal("0.00")
    current_stock: Decimal = Decimal("0.00")
    active: bool = True
    ncm: Optional[str] = None
    origin: int = 0
    
    # Soft Delete para Sync Incremental
    deleted_at: Optional[datetime] = None
    
    # Lista temporária para persistência (não mapeada diretamente no ORM do Produto, mas salva pelo Repo)
    _pending_movements: List[StockMovement] = field(default_factory=list, init=False, repr=False)

    def add_movement(self, movement_type: StockMovementType, quantity: Decimal, reason: str = None) -> StockMovement:
        if quantity <= 0:
            raise BusinessRuleException("Quantidade deve ser maior que zero.")

        if movement_type in [StockMovementType.ENTRY, StockMovementType.ADJUSTMENT_ADD, StockMovementType.RETURN]:
            self.current_stock += quantity
        else:
            self.current_stock -= quantity

        self.update_timestamp()
        
        movement = StockMovement(
            product_id=self.id,
            movement_type=movement_type,
            quantity=quantity,
            reason=reason,
            cost_price=self.cost_price
        )
        self._pending_movements.append(movement)
        return movement

    def mark_deleted(self):
        """Marca o produto como deletado sem remover do banco (Soft Delete)."""
        self.deleted_at = datetime.utcnow()
        self.active = False
        self.update_timestamp() # Importante para entrar no próximo sync delta