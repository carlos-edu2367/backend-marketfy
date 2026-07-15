import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Integer, ForeignKey, DateTime, Numeric, Text, UniqueConstraint, Index, Date
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
    fiscal_monthly_limit = Column(Integer, nullable=False, server_default="0")
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
    asaas_customer_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) # Restaurado


class RefreshSessionModel(Base):
    __tablename__ = "refresh_sessions"
    __table_args__ = (
        Index("ix_refresh_sessions_user", "user_id"),
        Index("ix_refresh_sessions_expires_at", "expires_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    jti_hash = Column(String, nullable=False, unique=True)
    user_agent = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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


class MarketMemberModel(Base):
    """Vínculo entre usuário e loja (Fase 3 / PR 10).

    Suporta multi-tenancy real: além do owner, operadores legítimos
    (manager, cashier) podem acessar a loja com permissões mapeadas por role.
    Owner é representado tanto por `MarketModel.owner_id` quanto por uma linha
    nesta tabela com role='owner' (criada na migração).
    """

    __tablename__ = "market_members"
    __table_args__ = (
        UniqueConstraint("market_id", "user_id", name="uq_market_member"),
        Index("ix_market_members_user", "user_id"),
        Index("ix_market_members_market", "market_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String, nullable=False)  # owner | manager | cashier
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

# =============================================================================
# INVENTORY (Produtos e Estoque)
# =============================================================================

class ProductModel(Base):
    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_market_code", "market_id", "code"),
        Index("ix_products_market_updated", "market_id", "updated_at"),
    )
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
    tax_rule_id = Column(
        UUID(as_uuid=True),
        ForeignKey("product_tax_rules.id", name="fk_products_tax_rule_id"),
        nullable=True,
    )
    tax_rule = relationship("ProductTaxRuleModel", foreign_keys=[tax_rule_id])
    
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
    __table_args__ = (
        Index("ix_sales_market_created_at", "market_id", "created_at"),
        Index("ix_sales_market_offline_id", "market_id", "offline_id"),
    )
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
    fiscal_documents = relationship(
        "FiscalDocumentModel",
        primaryjoin="SaleModel.id == FiscalDocumentModel.sale_id",
        foreign_keys="[FiscalDocumentModel.sale_id]",
        backref="sale",
        lazy="selectin",
        uselist=True
    )

class SaleItemModel(Base):
    __tablename__ = "sale_items"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    
    product_name_snapshot = Column(String, nullable=True)
    ncm_snapshot = Column(String, nullable=True)
    origin_snapshot = Column(Integer, default=0) # Restaurado
    fiscal_tax_snapshot_json = Column(Text, nullable=True)
    tax_rule_version_snapshot = Column(Integer, nullable=True)
    
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
    __table_args__ = (
        Index("ix_customers_market_cpf", "market_id", "cpf"),
        Index("ix_customers_market_updated", "market_id", "updated_at"),
    )
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
    __table_args__ = (
        Index("ix_financial_market_created_at", "market_id", "created_at"),
        Index("ix_financial_market_due_date", "market_id", "due_date"),
    )
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
    __table_args__ = (
        Index("ix_tickets_requester_status", "requester_id", "status"),
        Index("ix_tickets_market_status", "market_id", "status"),
    )
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


# =============================================================================
# BILLING (Fase 4 / PR 13)
# Tabelas de assinatura local e eventos do Billing Core.
# Fonte de verdade: BillingSubscriptionModel.
# UserModel.plan_id e plan_expiration são cache temporário para leitura rápida.
# =============================================================================

class BillingSubscriptionModel(Base):
    """Assinatura local que espelha o estado recebido do Billing Core.

    system_sub_id = str(owner_user_id) — tenant comercial.
    status segue o ciclo: pending → trialing/active → past_due/canceled/expired/failed.
    """

    __tablename__ = "billing_subscriptions"
    __table_args__ = (
        Index("ix_billing_sub_owner", "owner_id"),
        Index("ix_billing_sub_status", "status"),
        Index("ix_billing_sub_owner_status", "owner_id", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Vínculo com o usuário owner (tenant comercial)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Plano local referenciado (cache; fonte de verdade é o Billing Core)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=True)

    # Identificadores no Billing Core
    billing_system = Column(String, default="marketfy", nullable=False)
    billing_system_sub_id = Column(String, nullable=True)   # str(owner_user_id)
    billing_subscription_id = Column(String, nullable=True)  # ID retornado pelo Billing Core
    billing_job_id = Column(String, nullable=True)           # job_id para polling

    # Identificador do cliente no provedor de pagamento
    customer_provider_id = Column(String, nullable=True)

    # Estado da assinatura
    # Valores: pending | trialing | active | past_due | canceled | expired | failed
    status = Column(String, default="pending", nullable=False)

    subscription_type = Column(String, nullable=True)   # trial | monthly | semiannual | annual
    value = Column(Numeric(10, 2), default=0, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    last_event_at = Column(DateTime, nullable=True)

    # Idempotência de criação
    idempotency_key = Column(String, nullable=True, unique=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relacionamentos
    events = relationship("BillingEventModel", back_populates="subscription", lazy="select")


class BillingEventModel(Base):
    """Evento recebido do Billing Core via webhook.

    Persiste payload bruto para auditoria.
    Processamento é idempotente por event_id.
    """

    __tablename__ = "billing_events"
    __table_args__ = (
        Index("ix_billing_event_sub", "subscription_id"),
        Index("ix_billing_event_type", "event_type"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Identificador único do evento fornecido pelo Billing Core
    event_id = Column(String, nullable=False, unique=True)

    # Assinatura relacionada (nullable — evento pode chegar antes da sub local)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("billing_subscriptions.id"), nullable=True)

    # Referência ao owner para facilitar lookup
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    event_type = Column(String, nullable=False)         # subscription.active, subscription.past_due, ...
    idempotency_key = Column(String, nullable=True)

    # Payload bruto (JSON serializado como texto)
    raw_payload = Column(Text, nullable=True)

    # Ciclo de processamento
    # Valores: received | processed | failed | skipped
    processing_status = Column(String, default="received", nullable=False)
    processing_error = Column(Text, nullable=True)
    processed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relacionamento
    subscription = relationship("BillingSubscriptionModel", back_populates="events")


# =============================================================================
# AUDIT (Fase 5)
# Trilha operacional de ações críticas, sem payloads sensíveis.
# =============================================================================

class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_actor", "actor_user_id"),
        Index("ix_audit_logs_market", "market_id"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_created_at", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    actor_role = Column(String, nullable=True)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=True)
    resource_type = Column(String, nullable=False)
    resource_id = Column(String, nullable=True)
    action = Column(String, nullable=False)
    result = Column(String, nullable=False)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    request_id = Column(String, nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# =============================================================================
# FISCAL ROBUSTO (PR1 — Fase 5 Fiscal)
# Tabelas novas — mantém InvoiceModel/FiscalConfigModel legados intactos.
# =============================================================================

class FiscalTenantConfigModel(Base):
    """Configuração fiscal completa por mercado."""
    __tablename__ = "fiscal_tenant_configs"
    __table_args__ = (
        Index("ix_ftc_market", "market_id"),
        Index("ix_ftc_validation", "validation_status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False, unique=True)
    provider = Column(String, default="focus_nfe", nullable=False)
    environment = Column(String, default="homologacao", nullable=False)
    enabled = Column(Boolean, default=False, nullable=False)

    legal_name = Column(String, nullable=True)
    trade_name = Column(String, nullable=True)
    cnpj = Column(String, nullable=True)
    state_registration = Column(String, nullable=True)
    municipal_registration = Column(String, nullable=True)
    tax_regime = Column(String, nullable=True)
    crt = Column(String, nullable=True)
    cnae_primary = Column(String, nullable=True)
    address_json = Column(Text, nullable=True)

    certificate_storage_key = Column(String, nullable=True)
    certificate_password_ciphertext = Column(String, nullable=True)
    certificate_fingerprint = Column(String, nullable=True)
    certificate_valid_until = Column(DateTime, nullable=True)

    csc_id_ciphertext = Column(String, nullable=True)
    csc_token_ciphertext = Column(String, nullable=True)

    nfce_series = Column(Integer, default=1, nullable=False)
    nfce_next_number = Column(Integer, default=1, nullable=False)
    numbering_mode = Column(String, default="provider_auto", nullable=False)
    contingency_series = Column(Integer, default=900, nullable=False)
    contingency_next_number = Column(Integer, default=1, nullable=False)

    default_cfop = Column(String, default="5102", nullable=False)
    default_ncm = Column(String, nullable=True)
    default_csosn = Column(String, default="102", nullable=True)
    default_cst = Column(String, nullable=True)
    responsavel_tecnico = Column(Text, nullable=True)

    validation_status = Column(String, default="not_validated", nullable=False)
    last_validated_at = Column(DateTime, nullable=True)

    # Neectify Fiscal — adicionados via migration
    neectify_issuer_id = Column(String, nullable=True)
    neectify_cert_id = Column(String, nullable=True)
    neectify_config_id = Column(String, nullable=True)
    neectify_onboarding_step = Column(String, nullable=True, server_default="pending")

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ProductTaxProfileModel(Base):
    """Perfil fiscal por produto — tributação reutilizável."""
    __tablename__ = "product_tax_profiles"
    __table_args__ = (
        Index("ix_ptp_market", "market_id"),
        Index("ix_ptp_active", "market_id", "active"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    name = Column(String, nullable=False)
    ncm = Column(String, nullable=True)
    cest = Column(String, nullable=True)
    cfop = Column(String, default="5102", nullable=False)
    origin = Column(String, default="0", nullable=False)
    icms_cst = Column(String, nullable=True)
    icms_csosn = Column(String, default="102", nullable=True)
    pis_cst = Column(String, default="07", nullable=False)
    cofins_cst = Column(String, default="07", nullable=False)
    aliquotas_json = Column(Text, nullable=True)
    effective_from = Column(DateTime, nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ProductTaxRuleModel(Base):
    """Versioned, accountant-approved fiscal rule; never inferred from catalog data."""

    __tablename__ = "product_tax_rules"
    __table_args__ = (
        Index("ix_ptr_market_status_effective", "market_id", "status", "effective_from"),
        Index("ix_ptr_market_name_version", "market_id", "name", "version"),
        UniqueConstraint("rule_family_id", "version", name="uq_product_tax_rule_family_version"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False, default="draft")
    version = Column(Integer, nullable=False, default=1)
    rule_family_id = Column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    supersedes_rule_id = Column(UUID(as_uuid=True), ForeignKey("product_tax_rules.id"), nullable=True)
    effective_from = Column(Date, nullable=True)
    effective_to = Column(Date, nullable=True)

    ncm = Column(String, nullable=True)
    cest = Column(String, nullable=True)
    origin = Column(String, nullable=True)
    cfop = Column(String, nullable=True)

    icms_group = Column(String, nullable=True)
    icms_cst = Column(String, nullable=True)
    icms_csosn = Column(String, nullable=True)
    icms_mod_bc = Column(String, nullable=True)
    icms_rate = Column(Numeric(9, 4), nullable=True)
    icms_reduction_rate = Column(Numeric(9, 4), nullable=True)
    icms_st_mod_bc = Column(String, nullable=True)
    icms_st_mva_rate = Column(Numeric(9, 4), nullable=True)
    icms_st_rate = Column(Numeric(9, 4), nullable=True)
    fcp_rate = Column(Numeric(9, 4), nullable=True)

    pis_cst = Column(String, nullable=True)
    pis_rate = Column(Numeric(9, 4), nullable=True)
    cofins_cst = Column(String, nullable=True)
    cofins_rate = Column(Numeric(9, 4), nullable=True)

    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class TaxRuleApprovalModel(Base):
    """Immutable evidence authorizing publication of one fiscal-rule version."""

    __tablename__ = "tax_rule_approvals"

    rule_id = Column(UUID(as_uuid=True), ForeignKey("product_tax_rules.id"), primary_key=True)
    accountant_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    homologation_xml_reference = Column(Text, nullable=False)
    homologation_xml_sha256 = Column(String(64), nullable=False)
    approved_at = Column(DateTime, nullable=False)


class ProductTaxRuleAssignmentModel(Base):
    """Historical product-to-rule association used to resolve offline sales safely."""

    __tablename__ = "product_tax_rule_assignments"
    __table_args__ = (
        Index("ix_ptra_product_effective", "product_id", "effective_from", "effective_to"),
        Index("ix_ptra_market_product", "market_id", "product_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    tax_rule_id = Column(UUID(as_uuid=True), ForeignKey("product_tax_rules.id"), nullable=False)
    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class FiscalDocumentModel(Base):
    """Documento fiscal de uma venda. Um por sale_id+document_type."""
    __tablename__ = "fiscal_documents"
    __table_args__ = (
        UniqueConstraint("market_id", "sale_id", "document_type", name="uq_fd_market_sale_type"),
        UniqueConstraint("provider", "provider_ref", name="uq_fd_provider_ref"),
        Index("ix_fd_market_status", "market_id", "status", "created_at"),
        Index("ix_fd_access_key", "access_key"),
        Index("ix_fd_market_created", "market_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=False)
    document_type = Column(String, default="nfce", nullable=False)
    provider = Column(String, default="focus_nfe", nullable=False)
    provider_ref = Column(String, nullable=True)
    environment = Column(String, default="homologacao", nullable=False)
    status = Column(String, default="queued", nullable=False)
    idempotency_key = Column(String, nullable=True)
    series = Column(Integer, nullable=True)
    number = Column(Integer, nullable=True)
    access_key = Column(String, nullable=True)
    protocol = Column(String, nullable=True)
    sefaz_status_code = Column(String, nullable=True)
    sefaz_message = Column(Text, nullable=True)
    provider_status = Column(String, nullable=True)
    issued_at = Column(DateTime, nullable=True)
    authorized_at = Column(DateTime, nullable=True)
    canceled_at = Column(DateTime, nullable=True)
    contingency_mode = Column(Boolean, default=False, nullable=False)
    offline_receipt_id = Column(String, nullable=True)
    last_attempt_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    attempts = relationship("FiscalAttemptModel", back_populates="document",
                            lazy="select", cascade="all, delete-orphan")
    artifacts = relationship("FiscalArtifactModel", back_populates="document",
                             lazy="select", cascade="all, delete-orphan")
    events = relationship("FiscalEventModel", back_populates="document",
                          lazy="select", cascade="all, delete-orphan")


class FiscalAttemptModel(Base):
    """Cada chamada ao provider — rastreio de tentativas."""
    __tablename__ = "fiscal_attempts"
    __table_args__ = (
        Index("ix_fa_doc", "fiscal_document_id"),
        Index("ix_fa_market_created", "market_id", "started_at"),
        Index("ix_fa_next_retry", "next_retry_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fiscal_document_id = Column(UUID(as_uuid=True), ForeignKey("fiscal_documents.id"), nullable=False)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    provider = Column(String, nullable=False)
    operation = Column(String, default="emit", nullable=False)
    attempt_number = Column(Integer, default=1, nullable=False)
    status = Column(String, default="pending", nullable=False)
    request_hash = Column(String, nullable=True)
    provider_request_id = Column(String, nullable=True)
    http_status = Column(Integer, nullable=True)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)

    document = relationship("FiscalDocumentModel", back_populates="attempts")


class FiscalArtifactModel(Base):
    """XML/PDF armazenado em storage privado."""
    __tablename__ = "fiscal_artifacts"
    __table_args__ = (
        Index("ix_fart_doc", "fiscal_document_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fiscal_document_id = Column(UUID(as_uuid=True), ForeignKey("fiscal_documents.id"), nullable=False)
    artifact_type = Column(String, nullable=False)
    storage_key = Column(String, nullable=False)
    sha256 = Column(String, nullable=True)
    content_type = Column(String, default="application/xml", nullable=False)
    size_bytes = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    document = relationship("FiscalDocumentModel", back_populates="artifacts")


class FiscalEventModel(Base):
    """Trilha fiscal auditável por documento."""
    __tablename__ = "fiscal_events"
    __table_args__ = (
        Index("ix_fevt_doc", "fiscal_document_id"),
        Index("ix_fevt_market", "market_id"),
        Index("ix_fevt_created", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fiscal_document_id = Column(UUID(as_uuid=True), ForeignKey("fiscal_documents.id"), nullable=False)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    event_type = Column(String, nullable=False)
    source = Column(String, default="marketfy", nullable=False)
    message = Column(Text, nullable=True)
    metadata_json_sanitized = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    document = relationship("FiscalDocumentModel", back_populates="events")


# =============================================================================
# BILLING FISCAL (PR9)
# =============================================================================

class FiscalUsageCounterModel(Base):
    """Contador mensal de emissões por owner (owner-scoped, não market-scoped)."""
    __tablename__ = "fiscal_usage_counters"
    __table_args__ = (
        UniqueConstraint("owner_id", "period_yyyymm", name="uq_fuc_owner_period"),
        Index("ix_fuc_owner_period", "owner_id", "period_yyyymm"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    market_id = Column(UUID(as_uuid=True), nullable=True)  # NULL = counter owner-level
    period_yyyymm = Column(String(6), nullable=False)
    included_limit = Column(Integer, default=0, nullable=False)
    addon_limit = Column(Integer, default=0, nullable=False)
    reserved_count = Column(Integer, default=0, nullable=False)
    used_count = Column(Integer, default=0, nullable=False)
    failed_billable_count = Column(Integer, default=0, nullable=False)
    released_count = Column(Integer, default=0, nullable=False)
    blocked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class FiscalUsageLedgerModel(Base):
    """Ledger imutável de eventos de consumo fiscal."""
    __tablename__ = "fiscal_usage_ledger"
    __table_args__ = (
        Index("ix_ful_owner_period", "owner_id", "period_yyyymm"),
        Index("ix_ful_doc", "fiscal_document_id"),
        UniqueConstraint("idempotency_key", name="uq_ful_idempotency"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=True)  # nullable: eventos owner-level
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=True)
    fiscal_document_id = Column(UUID(as_uuid=True), ForeignKey("fiscal_documents.id"), nullable=True)
    period_yyyymm = Column(String(6), nullable=False)
    event_type = Column(String, nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    reason = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FiscalEmissionPackageModel(Base):
    """Pacote adicional de emissões comprado pelo owner."""
    __tablename__ = "fiscal_emission_packages"
    __table_args__ = (
        UniqueConstraint("bc_idempotency_key", name="uq_fep_bc_idempotency_key"),
        Index("ix_fep_owner", "owner_id"),
        Index("ix_fep_valid", "owner_id", "valid_until"),
        Index("ix_fep_owner_status", "owner_id", "payment_status"),
        Index("ix_fep_owner_payment_status", "owner_id", "payment_status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    market_id = Column(UUID(as_uuid=True), nullable=True)  # rastreio de onde foi comprado
    package_type = Column(String, default="nfce_addon", nullable=False)
    quantity = Column(Integer, default=0, nullable=False)
    remaining = Column(Integer, default=0, nullable=False)
    valid_from = Column(DateTime, nullable=True)
    valid_until = Column(DateTime, nullable=True)
    billing_subscription_id = Column(String, nullable=True)
    payment_status = Column(String, default="pending", nullable=False)
    package_slug = Column(String, nullable=True)
    bc_job_id = Column(String, nullable=True)
    bc_payment_id = Column(String, nullable=True)
    bc_idempotency_key = Column(String, nullable=True)
    price_gross = Column(Numeric(10, 2), nullable=True)
    price_net_target = Column(Numeric(10, 2), nullable=True)
    purchased_at_market_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

# =============================================================================
# WEBHOOKS PROVIDER (PR7)
# =============================================================================

class ProviderWebhookEventModel(Base):
    """Idempotência de eventos de webhook de provider fiscal."""
    __tablename__ = "provider_webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_pwe_provider_event"),
        Index("ix_pwe_provider_ref", "provider_ref"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider = Column(String, nullable=False)
    event_id = Column(String, nullable=False)
    provider_ref = Column(String, nullable=True)
    raw_payload_hash = Column(String, nullable=True)
    processing_status = Column(String, default="pending", nullable=False)
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# =============================================================================
# NOTIFICAÇÕES FISCAIS (PR10)
# =============================================================================

class FiscalNotificationModel(Base):
    """Notificações fiscais para owner/manager."""
    __tablename__ = "fiscal_notifications"
    __table_args__ = (
        Index("ix_fn_owner", "owner_id"),
        Index("ix_fn_market", "market_id"),
        Index("ix_fn_read", "owner_id", "read_at"),
        UniqueConstraint("dedupe_key", name="uq_fn_dedupe"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=True)
    notification_type = Column(String, nullable=False)
    severity = Column(String, default="info", nullable=False)
    title = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    dedupe_key = Column(String, nullable=True)
    read_at = Column(DateTime, nullable=True)
    sent_email_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FiscalInutilizacaoModel(Base):
    """Registro de inutilização de numeração NFC-e/NF-e."""
    __tablename__ = "fiscal_inutilizacoes"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_finu_idempotency"),
        Index("ix_finu_market", "market_id"),
        Index("ix_finu_market_status", "market_id", "status"),
        Index("ix_finu_created_at", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    provider = Column(String, nullable=False)
    environment = Column(String, nullable=False)
    cnpj = Column(String, nullable=False)
    document_type = Column(String, nullable=False, default="nfce")
    series = Column(Integer, nullable=False)
    start_number = Column(Integer, nullable=False)
    end_number = Column(Integer, nullable=False)
    justification = Column(Text, nullable=False)
    requested_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status = Column(String, nullable=False, default="pending")
    protocol = Column(String, nullable=True)
    provider_message = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    authorized_at = Column(DateTime, nullable=True)
    idempotency_key = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
