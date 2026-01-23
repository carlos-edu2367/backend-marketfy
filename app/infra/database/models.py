import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Integer, ForeignKey, DateTime, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from infra.database.setup import Base

# =============================================================================
# IDENTITY (Planos, Usuários e Mercados)
# =============================================================================

class PlanModel(Base):
    __tablename__ = "plans"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    max_markets = Column(Integer, nullable=False)
    max_terminals = Column(Integer, nullable=False)
    price_monthly = Column(Numeric(10, 2), default=0)
    price_180days = Column(Numeric(10, 2), default=0)
    price_annual = Column(Numeric(10, 2), default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class UserModel(Base):
    __tablename__ = "users"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    cpf = Column(String, nullable=True, unique=True) # Restaurado unique constraint
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    
    plan_id = Column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=True)
    plan_expiration = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) # Restaurado

class MarketModel(Base):
    __tablename__ = "markets"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    document = Column(String, nullable=False, unique=True) # CNPJ (Restaurado Unique)
    address = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) # Restaurado

# =============================================================================
# INVENTORY (Produtos e Estoque)
# =============================================================================

class ProductModel(Base):
    __tablename__ = "products"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False, index=True) # Index Restaurado
    
    name = Column(String, nullable=False)
    code = Column(String, nullable=False)
    barcode = Column(String, nullable=True, index=True) # Index Restaurado
    
    price = Column(Numeric(10, 2), nullable=False)
    cost_price = Column(Numeric(10, 2), default=0)
    current_stock = Column(Numeric(10, 3), default=0)
    
    active = Column(Boolean, default=True)
    ncm = Column(String, nullable=True)
    origin = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)

class StockMovementModel(Base):
    __tablename__ = "stock_movements"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True) # Index Restaurado
    
    movement_type = Column(String, nullable=False)
    quantity = Column(Numeric(10, 3), nullable=False)
    cost_price = Column(Numeric(10, 2), nullable=True)
    reason = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)

# =============================================================================
# SALES & POS (Terminais, Caixas, Vendas)
# =============================================================================

class TerminalModel(Base):
    __tablename__ = "terminals"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    name = Column(String, nullable=False)
    # A migração irá tratar a renomeação de is_active -> active se necessário
    # Mantemos 'active' pois é o que o código espera.
    active = Column(Boolean, default=True) 
    created_at = Column(DateTime, default=datetime.utcnow)

class BoxModel(Base):
    __tablename__ = "boxes"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    terminal_id = Column(UUID(as_uuid=True), ForeignKey("terminals.id"), nullable=False) # Not Null restaurado
    operator_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    status = Column(String, default="aberto")
    opened_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    
    initial_balance = Column(Numeric(10, 2), default=0)
    current_balance = Column(Numeric(10, 2), default=0)
    final_balance_reported = Column(Numeric(10, 2), nullable=True)
    difference = Column(Numeric(10, 2), nullable=True)
    closing_observation = Column(Text, nullable=True)

class BoxMovementModel(Base): # Tabela Restaurada
    __tablename__ = "box_movements"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    box_id = Column(UUID(as_uuid=True), ForeignKey("boxes.id"), nullable=False)
    type = Column(String, nullable=False) # sangria / suprimento
    amount = Column(Numeric(10, 2), nullable=False)
    reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class SaleModel(Base):
    __tablename__ = "sales"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False, index=True)
    box_id = Column(UUID(as_uuid=True), ForeignKey("boxes.id"), nullable=False)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    status = Column(String, default="concluida")
    total_amount = Column(Numeric(10, 2), default=0, nullable=False) # Nullable False restaurado
    discount = Column(Numeric(10, 2), default=0)
    acrescimo = Column(Numeric(10, 2), default=0)
    
    customer_cpf = Column(String, nullable=True)
    offline_id = Column(String, nullable=True)
    synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relacionamentos
    items = relationship("SaleItemModel", backref="sale", lazy="selectin")
    payments = relationship("PaymentModel", backref="sale", lazy="selectin")
    invoice = relationship("InvoiceModel", back_populates="sale", uselist=True, lazy="selectin")

class SaleItemModel(Base):
    __tablename__ = "sale_items"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    
    product_name_snapshot = Column(String, nullable=True)
    ncm_snapshot = Column(String, nullable=True)
    origin_snapshot = Column(Integer, default=0) # Restaurado
    
    quantity = Column(Numeric(10, 3), nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    total = Column(Numeric(10, 2), nullable=False)

    product = relationship("ProductModel")

class PaymentModel(Base):
    __tablename__ = "payments"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=False)
    method = Column(String, nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    installments = Column(Integer, default=1)

# =============================================================================
# FINANCE (Clientes, Fiado, Contas)
# =============================================================================

class CustomerModel(Base):
    __tablename__ = "customers"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    name = Column(String, nullable=False)
    cpf = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    credit_limit = Column(Numeric(10, 2), default=0)
    current_debt = Column(Numeric(10, 2), default=0)
    status = Column(String, default="ativo")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class CustomerLedgerModel(Base):
    __tablename__ = "customer_ledger"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=True)
    
    amount = Column(Numeric(10, 2), nullable=False)
    type = Column(String, nullable=False) # "divida" ou "pagamento"
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class FinancialTransactionModel(Base):
    __tablename__ = "financial_transactions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    
    description = Column(String, nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    type = Column(String, nullable=False) # "receita" ou "despesa"
    category = Column(String, default="Geral")
    
    due_date = Column(DateTime, nullable=False)
    paid_at = Column(DateTime, nullable=True)
    
    # Restaurado FKs opcionais para rastreabilidade
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=True) 
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) # Restaurado

# =============================================================================
# SUPPORT (Tickets)
# =============================================================================

class TicketModel(Base):
    __tablename__ = "tickets"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requester_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=True)
    assigned_to_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    subject = Column(String, nullable=False)
    status = Column(String, default="aberto")
    priority = Column(String, default="baixa")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    messages = relationship("TicketMessageModel", backref="ticket", lazy="selectin", cascade="all, delete-orphan")

class TicketMessageModel(Base):
    __tablename__ = "ticket_messages"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id"), nullable=False)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    content = Column(Text, nullable=False)
    is_internal = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# =============================================================================
# FISCAL (NFC-e)
# =============================================================================

class FiscalConfigModel(Base):
    __tablename__ = "fiscal_config"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False, unique=True)
    
    certificate_path = Column(String, nullable=False)
    certificate_password = Column(String, nullable=False)
    csc_token = Column(String, nullable=False)
    csc_id = Column(String, nullable=False)
    environment = Column(String, default="homologacao")
    
    default_ncm = Column(String, nullable=True)
    default_cfop = Column(String, default="5102")
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class InvoiceModel(Base):
    __tablename__ = "invoices"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=False)
    
    status = Column(String, default="pendente")
    
    access_key = Column(String, nullable=True)
    protocol = Column(String, nullable=True)
    series = Column(Integer, default=1)
    number = Column(Integer, nullable=True)
    
    xml_url = Column(String, nullable=True)
    pdf_url = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    
    emitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow) # Restaurado
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) # Restaurado
    
    sale = relationship("SaleModel", back_populates="invoice")