import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Any
from domain.shared import Entity, BusinessRuleException
from domain.inventory import Product

# =============================================================================
# ENUMS
# =============================================================================

class BoxStatus(Enum):
    OPEN = "aberto"
    CLOSED = "fechado"

class SaleStatus(Enum):
    COMPLETED = "concluida"
    CANCELED = "cancelada"
    PENDING_SYNC = "pendente_sync"

class PaymentMethod(Enum):
    CASH = "dinheiro"
    CREDIT_CARD = "cartao_credito"
    DEBIT_CARD = "cartao_debito"
    PIX = "pix"
    FIADO = "fiado"

class BoxMovementType(Enum):
    SANGRIA = "sangria"     # Saída de dinheiro
    SUPRIMENTO = "suprimento" # Entrada de dinheiro (troco extra)

# =============================================================================
# ENTIDADES
# =============================================================================

@dataclass
class Terminal(Entity):
    """Representa um PDV físico/lógico (Computador do Caixa)."""
    market_id: uuid.UUID
    name: str # Ex: "Caixa 01", "Balcão"
    active: bool = True

@dataclass
class Box(Entity):
    """Representa uma sessão de caixa aberta por um operador."""
    market_id: uuid.UUID
    terminal_id: uuid.UUID
    operator_id: uuid.UUID
    
    status: BoxStatus = BoxStatus.OPEN
    opened_at: datetime = field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    
    initial_balance: Decimal = Decimal("0.00")
    current_balance: Decimal = Decimal("0.00") # Atualizado a cada venda em dinheiro
    
    final_balance_reported: Optional[Decimal] = None
    difference: Optional[Decimal] = None
    closing_observation: Optional[str] = None

    def close(self, reported_balance: Decimal, observation: str = None):
        if self.status == BoxStatus.CLOSED:
            raise BusinessRuleException("Caixa já está fechado.")
        
        self.status = BoxStatus.CLOSED
        self.closed_at = datetime.utcnow()
        self.final_balance_reported = reported_balance
        self.difference = reported_balance - self.current_balance # Positivo = Sobra, Negativo = Falta
        self.closing_observation = observation
        self.update_timestamp()

@dataclass
class SaleItem(Entity):
    sale_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal
    
    # Snapshots fiscais
    ncm_snapshot: Optional[str] = None
    origin_snapshot: int = 0
    fiscal_tax_snapshot: Optional[dict] = None
    tax_rule_version_snapshot: Optional[int] = None

@dataclass
class Payment(Entity):
    sale_id: uuid.UUID
    method: PaymentMethod
    amount: Decimal
    installments: int = 1

@dataclass
class Sale(Entity):
    market_id: uuid.UUID
    box_id: uuid.UUID
    operator_id: uuid.UUID
    
    status: SaleStatus = SaleStatus.COMPLETED
    total_amount: Decimal = Decimal("0.00")
    discount: Decimal = Decimal("0.00")
    acrescimo: Decimal = Decimal("0.00")
    
    customer_cpf: Optional[str] = None
    offline_id: Optional[str] = None # ID gerado no front
    synced_at: Optional[datetime] = None
    
    items: List[SaleItem] = field(default_factory=list)
    payments: List[Payment] = field(default_factory=list)
    
    # Campo para carregar a nota fiscal (não persistido diretamente na tabela sales)
    invoice: Any = None

    def add_item(self, product: Product, quantity: Decimal):
        item_total = product.price * quantity
        
        item = SaleItem(
            sale_id=self.id,
            product_id=product.id,
            product_name=product.name,
            quantity=quantity,
            unit_price=product.price,
            total=item_total,
            ncm_snapshot=product.ncm,
            origin_snapshot=product.origin
        )
        self.items.append(item)
        self.calculate_total()

    def add_payment(self, method: PaymentMethod, amount: Decimal, installments: int = 1):
        payment = Payment(
            sale_id=self.id,
            method=method,
            amount=amount,
            installments=installments
        )
        self.payments.append(payment)

    def calculate_total(self):
        items_total = sum(item.total for item in self.items)
        self.total_amount = items_total - self.discount + self.acrescimo
