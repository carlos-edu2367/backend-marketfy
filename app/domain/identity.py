import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional
from domain.shared import Entity, CPF, Email, CNPJ, BusinessRuleException

# =============================================================================
# ENUMS
# =============================================================================

class PlanType(Enum):
    FREE = "cortesia"
    PAID = "pago"
    TRIAL = "trial"

class UserRole(Enum):
    ADMIN = "admin"
    OWNER = "owner"
    MANAGER = "manager"
    ACCOUNTANT = "accountant"
    CASHIER = "cashier"

class PlanDuration(Enum):
    TRIAL = 14
    MONTHLY = 30
    SEMIANNUAL = 180
    ANNUAL = 365

# =============================================================================
# ENTIDADES
# =============================================================================

@dataclass
class Plan(Entity):
    name: str
    type: PlanType
    max_markets: int
    max_terminals: int # Renomeado de max_boxes_per_market para conceito de PDV Fixo
    
    # Preços padronizados como Decimal
    price_monthly: Decimal = field(default_factory=lambda: Decimal("0.00"))
    price_180days: Decimal = field(default_factory=lambda: Decimal("0.00"))
    price_annual: Decimal = field(default_factory=lambda: Decimal("0.00"))

    fiscal_monthly_limit: int = 0

    is_active: bool = True

    def is_limit_reached(self, current_count: int, context: str) -> bool:
        if context == 'markets':
            return current_count >= self.max_markets
        if context == 'terminals':
            return current_count >= self.max_terminals
        return False

@dataclass
class User(Entity):
    name: str
    email: Email
    cpf: CPF
    password_hash: str
    role: UserRole
    is_active: bool = True
    plan_id: Optional[uuid.UUID] = None 
    plan_expiration: Optional[datetime] = None
    cnpj: Optional[CNPJ] = None
    asaas_customer_id: Optional[str] = None

    @property
    def full_name(self) -> str:
        return self.name


    def check_plan_validity(self):
        """Verifica se o usuário (Dono) possui um plano válido e ativo."""
        if self.role == UserRole.OWNER:
            if not self.plan_id:
                raise BusinessRuleException("Usuário não possui plano contratado.")
            
            if not self.plan_expiration:
                raise BusinessRuleException("Plano inativo ou data de expiração inválida.")
            
            # Comparação timezone-aware (ambos UTC)
            now = datetime.now(timezone.utc) if self.plan_expiration.tzinfo else datetime.utcnow()
            
            if now > self.plan_expiration:
                raise BusinessRuleException("Seu plano expirou. Renove para continuar.")

    def activate_plan(self, plan: Plan, duration_days: int):
        """Ativa um plano para o usuário com base na duração em dias."""
        from datetime import timedelta
        self.plan_id = plan.id
        self.plan_expiration = datetime.utcnow() + timedelta(days=duration_days)
        self.is_active = True

@dataclass
class Market(Entity):
    owner_id: uuid.UUID
    name: str
    document: CNPJ
    address: str
    active: bool = True
