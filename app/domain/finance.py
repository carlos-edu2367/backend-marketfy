import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional, List
from domain.shared import Entity, CPF, BusinessRuleException
from datetime import datetime

# =============================================================================
# ENUMS
# =============================================================================

class TransactionType(Enum):
    CREDIT = "receita"
    DEBIT = "despesa"

class LedgerType(Enum):
    DEBT = "divida"      # Aumenta a dívida (Compra Fiado)
    PAYMENT = "pagamento" # Diminui a dívida (Pagamento)

class CustomerStatus(Enum):
    ACTIVE = "ativo"
    BLOCKED = "bloqueado" # Ex: Devendo muito

# =============================================================================
# ENTIDADES
# =============================================================================

@dataclass
class CustomerLedger(Entity):
    """
    Registro imutável de movimentação na conta do cliente (Livro Razão).
    Substitui a lógica de apenas somar/subtrair um saldo solto.
    """
    customer_id: uuid.UUID
    amount: Decimal
    type: LedgerType
    description: str
    sale_id: Optional[uuid.UUID] = None # Link com a venda se for compra
    
    def __post_init__(self):
        if self.amount <= 0:
            raise BusinessRuleException("Valor da movimentação deve ser positivo.")

@dataclass
class Customer(Entity):
    """Cliente do mercado (foco no Fiado)."""
    market_id: uuid.UUID
    name: str
    cpf: Optional[CPF] = None
    phone: Optional[str] = None
    credit_limit: Decimal = Decimal("0.00")
    
    # Este campo agora é derivado ou um cache atualizado via eventos,
    # mas mantemos na entidade para leitura rápida.
    current_debt: Decimal = Decimal("0.00") 
    status: CustomerStatus = CustomerStatus.ACTIVE
    
    # Lista temporária para persistência
    _pending_ledger: List[CustomerLedger] = field(default_factory=list, init=False, repr=False)

    def add_debt(self, amount: Decimal, sale_id: Optional[uuid.UUID] = None, description: str = "Compra Fiado"):
        """Registra uma nova dívida (Compra)."""
        if self.status == CustomerStatus.BLOCKED:
            raise BusinessRuleException("Cliente bloqueado. Venda fiado não permitida.")
        
        if (self.current_debt + amount) > self.credit_limit:
            raise BusinessRuleException(f"Limite de crédito excedido. Disponível: {self.credit_limit - self.current_debt}")
        
        self.current_debt += amount
        self.update_timestamp()
        
        entry = CustomerLedger(
            customer_id=self.id,
            amount=amount,
            type=LedgerType.DEBT,
            description=description,
            sale_id=sale_id
        )
        self._pending_ledger.append(entry)
        return entry

    def pay_debt(self, amount: Decimal, description: str = "Pagamento de Dívida"):
        """Registra um pagamento (Abatimento)."""
        if amount <= 0:
            raise BusinessRuleException("Valor do pagamento deve ser positivo.")
        
        self.current_debt -= amount
        # Permitimos saldo negativo (crédito) se o cliente pagar adiantado ou a mais
        
        self.update_timestamp()
        
        entry = CustomerLedger(
            customer_id=self.id,
            amount=amount,
            type=LedgerType.PAYMENT,
            description=description
        )
        self._pending_ledger.append(entry)
        return entry

@dataclass
class FinancialTransaction(Entity):
    """Registro financeiro da Loja (Contas a Pagar/Receber Gerais)."""
    market_id: uuid.UUID
    description: str
    amount: Decimal
    type: TransactionType
    due_date: datetime
    paid_at: Optional[datetime] = None
    category: str = "Geral"
    
    sale_id: Optional[uuid.UUID] = None
    customer_id: Optional[uuid.UUID] = None # Se for pagamento de fiado entrando no caixa da loja

    def settle(self):
        """Marca como pago/recebido."""
        if self.paid_at:
            raise BusinessRuleException("Transação já liquidada.")
        self.paid_at = datetime.utcnow()
        self.update_timestamp()