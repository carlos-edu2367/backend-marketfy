from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, date
from uuid import UUID
from decimal import Decimal

# ===========================
# IDENTITY & ACCESS DTOs
# ===========================

class UserCreateDTO(BaseModel):
    name: str
    email: EmailStr
    cpf: str
    # Removemos o max_length do Field para tratar manualmente no validator
    password: str = Field(..., min_length=6) 

    @field_validator('password')
    @classmethod
    def truncate_password_if_needed(cls, v: str) -> str:
        """
        Trunca automaticamente a senha para 72 bytes (limite do Bcrypt).
        Isso evita o erro 500 'password cannot be longer than 72 bytes'.
        """
        # Converte para bytes para checar o tamanho real
        v_bytes = v.encode('utf-8')
        
        if len(v_bytes) > 72:
            # Corta nos primeiros 72 bytes
            truncated_bytes = v_bytes[:72]
            # Decodifica de volta, ignorando erros se cortar um caractere multi-byte no meio
            return truncated_bytes.decode('utf-8', errors='ignore')
            
        return v

class UserResponseDTO(BaseModel):
    id: UUID
    name: str
    email: str
    role: str
    plan_id: Optional[UUID] = None
    plan_name: Optional[str] = None
    plan_expiration: Optional[datetime] = None
    is_active: bool = True
    
    # CORREÇÃO: Validadores 'before' para extrair valores de objetos de domínio (Email, Role)
    # Isso permite passar o objeto User direto do banco para o DTO sem conversão manual.
    
    @field_validator('email', mode='before')
    @classmethod
    def parse_email_value_object(cls, v: Any) -> str:
        # Se receber um objeto Email(value='...'), extrai o value
        if hasattr(v, 'value'):
            return v.value
        return str(v)

    @field_validator('role', mode='before')
    @classmethod
    def parse_role_enum(cls, v: Any) -> str:
        # Se receber um Enum UserRole, extrai o value ('admin', 'owner', etc)
        if hasattr(v, 'value'):
            return v.value
        return str(v)

    class Config:
        from_attributes = True

class MarketCreateDTO(BaseModel):
    name: str
    document: str  # CNPJ
    address: str

class SubscribeDTO(BaseModel):
    user_id_override: Optional[UUID] = None
    duration_days: int

# ===========================
# PLANS & SUBSCRIPTION DTOs
# ===========================

class PlanCreateDTO(BaseModel):
    name: str
    type: str
    max_markets: int
    max_terminals: int
    price_monthly: Decimal
    price_180days: Decimal
    price_annual: Decimal
    is_active: bool = True

class PlanUpdateDTO(BaseModel):
    name: Optional[str] = None
    max_markets: Optional[int] = None
    max_terminals: Optional[int] = None
    price_monthly: Optional[Decimal] = None
    price_180days: Optional[Decimal] = None
    price_annual: Optional[Decimal] = None

class PlanResponseDTO(PlanCreateDTO):
    id: UUID

# ===========================
# INVENTORY & SALES DTOs
# ===========================

class ProductCreateDTO(BaseModel):
    name: str
    code: str
    barcode: Optional[str] = None
    price: Decimal
    cost_price: Decimal = Decimal("0.00")
    ncm: Optional[str] = None
    origin: int = 0

class SaleItemDTO(BaseModel):
    product_id: UUID
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal
    ncm_snapshot: Optional[str] = None

class PaymentDTO(BaseModel):
    method: str
    amount: Decimal
    installments: int = 1

class SaleCreateDTO(BaseModel):
    id: Optional[UUID] = None
    box_id: UUID
    operator_id: UUID
    total_amount: Decimal
    discount: Decimal = Decimal("0.00")
    acrescimo: Decimal = Decimal("0.00")
    created_at: datetime
    customer_cpf: Optional[str] = None
    offline_id: Optional[str] = None
    items: List[SaleItemDTO]
    payments: List[PaymentDTO]

class SaleSyncDTO(BaseModel):
    sales: List[SaleCreateDTO]

class SaleResponseDTO(BaseModel):
    id: UUID
    market_id: UUID
    box_id: UUID
    operator_id: UUID
    status: str
    total_amount: Decimal
    discount: Decimal
    acrescimo: Decimal
    created_at: datetime
    synced_at: Optional[datetime]
    customer_cpf: Optional[str]
    items: List[SaleItemDTO] = []
    payments: List[PaymentDTO] = []
    invoice: Any = None

    class Config:
        from_attributes = True

# ===========================
# FINANCEIRO & ANALYTICS DTOs
# ===========================

class TransactionCreateDTO(BaseModel):
    description: str
    amount: Decimal
    type: str # 'receita' ou 'despesa'
    category: str
    payment_method: Optional[str] = None
    due_date: datetime
    paid_at: Optional[datetime] = None

class FinancialKPIsDTO(BaseModel):
    revenue: Decimal
    revenue_trend: float
    expenses: Decimal
    expenses_trend: float
    profit: Decimal
    profit_trend: float
    pending: Decimal

class FinancialEvolutionPointDTO(BaseModel):
    name: str # "Sem 1", "Jan", etc
    receitas: Decimal
    despesas: Decimal

class FinancialCategoryDTO(BaseModel):
    name: str
    value: Decimal
    color: str

class SimpleTransactionDTO(BaseModel):
    id: Union[UUID, int, str]
    desc: str
    type: str # 'in' ou 'out'
    value: Decimal
    date: str

class FinancialDashboardUnifiedDTO(BaseModel):
    kpis: FinancialKPIsDTO
    evolution: List[FinancialEvolutionPointDTO]
    categories: List[FinancialCategoryDTO]
    recent_transactions: List[SimpleTransactionDTO]

class SalesByHourDTO(BaseModel):
    hour: int
    total: Decimal
    count: int

class TopProductDTO(BaseModel):
    product_name: str
    quantity_sold: Decimal
    total_revenue: Decimal

class PaymentMethodStatsDTO(BaseModel):
    method: str
    total: Decimal
    count: int
    percentage: float

class MarketAnalyticsDTO(BaseModel):
    market_id: UUID
    ticket_average: Decimal
    total_sold_today: Decimal
    sales_count_today: int
    sales_by_hour: List[SalesByHourDTO]
    top_products: List[TopProductDTO]
    payment_methods: List[PaymentMethodStatsDTO]

# ===========================
# ADMIN DASHBOARD DTOs
# ===========================

class AdminAnalyticsDTO(BaseModel):
    mrr: Decimal
    active_markets: int
    total_users: int
    active_users: int
    churn_rate: float
    tickets_open: int

class ExpiringUserDTO(BaseModel):
    user_id: UUID
    user_name: str
    days_left: int
    plan_name: str

class AdminDashboardMetricsDTO(BaseModel):
    mrr: Decimal
    active_users: int
    total_users: int
    total_markets: int
    open_tickets: int

class AdminDashboardResponseDTO(BaseModel):
    metrics: AdminDashboardMetricsDTO
    expiring_users: List[ExpiringUserDTO]

class AdminUserListDTO(BaseModel):
    user_id: UUID
    name: str
    email: str
    role: str
    plan_name: str
    plan_expiration: Optional[datetime]
    markets_count: int
    is_active: bool
    created_at: datetime

class AdminPasswordResetDTO(BaseModel):
    password: str

# ===========================
# SUPPORT & MISC DTOs
# ===========================

class TicketMessageDTO(BaseModel):
    id: UUID
    content: str
    created_at: datetime
    sender_id: UUID
    is_internal: bool

class TicketCreateDTO(BaseModel):
    subject: str
    message: str
    category: str = "Geral"
    priority: str = "baixa"

class TicketResponseDTO(BaseModel):
    id: UUID
    subject: str
    status: str
    priority: str
    created_at: datetime
    messages: List[TicketMessageDTO] = []
    user_name: Optional[str] = None
    user_email: Optional[str] = None

class TicketReplyDTO(BaseModel):
    content: str
    is_internal: bool = False

class TicketStatusUpdateDTO(BaseModel):
    status: str

class CustomerCreateDTO(BaseModel):
    name: str
    cpf: Optional[str] = None
    phone: Optional[str] = None
    credit_limit: Optional[Decimal] = Decimal("0.00")

class CustomerResponseDTO(BaseModel):
    id: UUID
    name: str
    cpf: Optional[str] = None
    phone: Optional[str] = None
    credit_limit: Decimal
    current_debt: Decimal
    status: str

    class Config:
        from_attributes = True

class DebtPaymentDTO(BaseModel):
    amount: Decimal
    description: Optional[str] = "Pagamento de Dívida"

class LedgerEntryDTO(BaseModel):
    id: UUID
    amount: Decimal
    type: str
    description: str
    created_at: datetime
    sale_id: Optional[UUID] = None

# ===========================
# PDV / CAIXA DTOs
# ===========================

class TerminalCreateDTO(BaseModel):
    name: str
    active: bool = True

class TerminalResponseDTO(BaseModel):
    id: UUID
    market_id: UUID
    name: str
    active: bool
    
    # CORREÇÃO: Trata campo active=None vindo do banco/entidade
    @field_validator('active', mode='before')
    @classmethod
    def handle_none_active(cls, v: Any) -> bool:
        if v is None:
            return True # Default to True if null
        return v

    class Config:
        from_attributes = True

class OpenBoxDTO(BaseModel):
    initial_balance: Decimal
    operator_id: Optional[UUID] = None

class CloseBoxDTO(BaseModel):
    final_balance_reported: Decimal
    closing_observation: Optional[str] = None

class BoxResponseDTO(BaseModel):
    id: UUID
    market_id: UUID
    operator_id: UUID
    terminal_id: UUID
    status: str
    opened_at: datetime
    closed_at: Optional[datetime] = None
    initial_balance: Decimal
    current_balance: Decimal
    final_balance_reported: Optional[Decimal] = None
    difference: Optional[Decimal] = None
    
    class Config:
        from_attributes = True

class MarketDashboardStatsDTO(BaseModel):
    today_sales_total: Decimal
    today_sales_count: int
    open_boxes: int
    low_stock_products: int
    total_receivables: Decimal

class DailySalesDTO(BaseModel):
    date: date
    total: Decimal
    count: int

class StockMovementDTO(BaseModel):
    product_id: UUID
    movement_type: str
    quantity: Decimal
    reason: Optional[str] = None

class StockMovementResponseDTO(BaseModel):
    id: UUID
    product_id: UUID
    movement_type: str
    quantity: Decimal
    cost_price: Decimal
    reason: Optional[str]
    created_at: datetime

class ProductSyncResponseDTO(BaseModel):
    updated: List[Dict[str, Any]]
    deleted: List[UUID]
    server_time: datetime

# ===========================
# FISCAL DTOs
# ===========================

class FiscalConfigCreateDTO(BaseModel):
    csc_token: str
    csc_id: str
    environment: str
    default_ncm: Optional[str] = None
    certificate_password: str

class FiscalConfigResponseDTO(BaseModel):
    environment: str
    csc_id: str
    has_certificate: bool
    default_ncm: Optional[str]

class InvoiceResponseDTO(BaseModel):
    id: UUID
    status: str
    access_key: Optional[str]
    number: Optional[int]
    series: int
    xml_url: Optional[str]
    pdf_url: Optional[str]
    error_message: Optional[str]
    emitted_at: Optional[datetime]

class FinancialReportDTO(BaseModel):
    total_sales: Decimal
    total_manual_revenue: Decimal
    total_manual_expense: Decimal
    balance: Decimal
    
    categories_summary: List[Dict[str, Any]]
    transactions: List[Dict[str, Any]] # Lista mista simples
    sales_daily: List[Dict[str, Any]]

class FinancialCategorySummaryDTO(BaseModel):
    category: str
    total: Decimal
    type: str