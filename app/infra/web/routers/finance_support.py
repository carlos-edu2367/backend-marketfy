import uuid
from typing import List, Dict, Optional, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from decimal import Decimal
from datetime import datetime

from infra.database.setup import get_db
from domain.identity import User
from application.dtos import (
    CustomerCreateDTO, CustomerResponseDTO, 
    DebtPaymentDTO, LedgerEntryDTO, 
    TicketCreateDTO, TicketResponseDTO, TicketReplyDTO, TicketStatusUpdateDTO,
    TransactionCreateDTO, TicketMessageDTO
)
from domain.finance import TransactionType, Customer, FinancialTransaction, LedgerType, CustomerLedger
from domain.support import Ticket, TicketStatus
from domain.shared import BusinessRuleException, CPF

from infra.repositories.sqlalchemy_repos import (
    SQLAlchemyCustomerRepository, 
    SQLAlchemyFinancialTransactionRepository,
    SQLAlchemyTicketRepository,
    SQLAlchemyMarketRepository,
    SQLAlchemySaleRepository
)
from infra.web.dependencies import get_current_user, PermissionChecker

router_finance = APIRouter()
router_support = APIRouter()

# --- LOCAL DTOs (Garante consistência na resposta) ---
class FinancialTransactionResponseDTO(BaseModel):
    id: uuid.UUID
    type: str
    amount: Decimal
    description: str
    category: str
    created_at: datetime
    
    class Config:
        from_attributes = True

class FinanceDashboardDTO(BaseModel):
    balance: Decimal
    total_revenue: Decimal
    total_expense: Decimal
    accounts_receivable: Decimal
    recent_transactions: List[FinancialTransactionResponseDTO]

# =============================================================================
# SERVICES
# =============================================================================

class FinanceService:
    def __init__(self, 
                 customer_repo: SQLAlchemyCustomerRepository,
                 transaction_repo: SQLAlchemyFinancialTransactionRepository,
                 sale_repo: SQLAlchemySaleRepository):
        self.customer_repo = customer_repo
        self.transaction_repo = transaction_repo
        self.sale_repo = sale_repo

    # --- CLIENTES ---

    async def register_customer(self, market_id: uuid.UUID, dto: CustomerCreateDTO) -> Customer:
        if dto.cpf:
            existing = await self.customer_repo.get_by_cpf(market_id, CPF(dto.cpf).value)
            if existing:
                raise BusinessRuleException("Cliente já cadastrado com este CPF.")

        customer = Customer(
            market_id=market_id,
            name=dto.name,
            cpf=CPF(dto.cpf) if dto.cpf else None,
            phone=dto.phone,
            credit_limit=dto.credit_limit or Decimal("0.00")
        )
        return await self.customer_repo.save(customer)

    async def list_customers(self, market_id: uuid.UUID, search: str = None) -> List[Customer]:
        return await self.customer_repo.list_by_market(market_id, search)

    async def get_customer_statement(self, market_id: uuid.UUID, customer_id: uuid.UUID) -> Customer:
        customer = await self.customer_repo.get_by_id(customer_id)
        if not customer or customer.market_id != market_id:
            raise BusinessRuleException("Cliente não encontrado.")
        return customer
    
    async def get_customer_ledger(self, market_id: uuid.UUID, customer_id: uuid.UUID) -> List[LedgerEntryDTO]:
        """Retorna o histórico de movimentações (fiado/pagamentos) do cliente."""
        customer = await self.customer_repo.get_by_id(customer_id)
        if not customer or customer.market_id != market_id:
            raise BusinessRuleException("Cliente não encontrado.")
            
        entries = await self.customer_repo.get_ledger(customer_id)
        
        return [
            LedgerEntryDTO(
                id=e.id,
                amount=e.amount,
                type=e.type.value if hasattr(e.type, 'value') else str(e.type),
                description=e.description,
                created_at=e.created_at,
                sale_id=e.sale_id
            ) for e in entries
        ]

    async def add_debt(self, market_id: uuid.UUID, customer_id: uuid.UUID, amount: Decimal, description: str):
        customer = await self.customer_repo.get_by_id(customer_id)
        if not customer or customer.market_id != market_id:
            raise BusinessRuleException("Cliente não encontrado.")
        
        customer.add_debt(amount, None, description)
        return await self.customer_repo.save(customer)

    async def register_payment(self, market_id: uuid.UUID, customer_id: uuid.UUID, dto: DebtPaymentDTO):
        """Registra o pagamento de uma dívida de cliente."""
        customer = await self.customer_repo.get_by_id(customer_id)
        if not customer or customer.market_id != market_id:
            raise BusinessRuleException("Cliente não encontrado.")
        
        # Chama o método pay_debt do domínio
        customer.pay_debt(dto.amount, dto.description)
        
        return await self.customer_repo.save(customer)

    # --- TRANSAÇÕES & DASHBOARD ---

    async def add_transaction(self, market_id: uuid.UUID, dto: TransactionCreateDTO) -> FinancialTransaction:
        try:
            ttype = TransactionType(dto.type)
        except ValueError:
            raise BusinessRuleException("Tipo de transação inválido.")

        # Remove timezone info para compatibilidade com PostgreSQL TIMESTAMP WITHOUT TIME ZONE
        due_date_naive = dto.due_date.replace(tzinfo=None) if dto.due_date else None
        paid_at_naive = dto.paid_at.replace(tzinfo=None) if dto.paid_at else None

        transaction = FinancialTransaction(
            market_id=market_id,
            description=dto.description,
            amount=dto.amount,
            type=ttype,
            due_date=due_date_naive, 
            paid_at=paid_at_naive,
            category=dto.category
        )

        return await self.transaction_repo.save(transaction)
    
    async def list_transactions(self, market_id: uuid.UUID, limit: int = 50) -> List[FinancialTransaction]:
        return await self.transaction_repo.list_recent(market_id, limit)

    async def get_dashboard(self, market_id: uuid.UUID) -> FinanceDashboardDTO:
        """
        Consolida Vendas, Transações Manuais e Dívidas para o Dashboard Financeiro.
        """
        # 1. Total Recebíveis (Total que os clientes devem - Fiado)
        receivables = await self.customer_repo.get_total_debt(market_id)
        
        # 2. Transações Manuais (Mês Atual)
        now = datetime.utcnow()
        transactions = await self.transaction_repo.get_summary_by_month(market_id, now.year, now.month)
        
        manual_income = sum(t.amount for t in transactions if t.type == TransactionType.CREDIT or getattr(t.type, 'value', '') == 'receita')
        manual_expense = sum(t.amount for t in transactions if t.type == TransactionType.DEBIT or getattr(t.type, 'value', '') == 'despesa')
        
        # 3. Vendas (Mês Atual) - Adiciona ao Revenue
        start_date = datetime(now.year, now.month, 1).date()
        import calendar
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_date = datetime(now.year, now.month, last_day).date()

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())

        sales_summary = await self.sale_repo.get_sales_summary_by_period(market_id, start_dt, end_dt)
        sales_income = sum(s['total'] for s in sales_summary)
        
        total_revenue = manual_income + sales_income
        balance = total_revenue - manual_expense

        # 4. Transações Recentes
        recent = await self.transaction_repo.list_recent(market_id, 10)
        recent_dtos = [FinancialTransactionResponseDTO.model_validate(r) for r in recent]

        return FinanceDashboardDTO(
            balance=balance,
            total_revenue=total_revenue,
            total_expense=manual_expense,
            accounts_receivable=receivables,
            recent_transactions=recent_dtos
        )

class SupportService:
    def __init__(self, ticket_repo: SQLAlchemyTicketRepository):
        self.ticket_repo = ticket_repo

    async def create_ticket(self, user_id: uuid.UUID, dto: TicketCreateDTO) -> Ticket:
        ticket = Ticket(
            requester_id=user_id,
            market_id=None,
            subject=dto.subject
        )
        ticket.add_message(user_id, dto.message, is_internal=False)
        return await self.ticket_repo.save(ticket)

    async def get_user_tickets(self, user_id: uuid.UUID) -> List[TicketResponseDTO]:
        tickets = await self.ticket_repo.list_by_user(user_id)
        return [self._map_ticket(t) for t in tickets]

    async def get_admin_tickets(self) -> List[TicketResponseDTO]:
        enriched = await self.ticket_repo.list_all_enriched()
        result = []
        for ticket, user in enriched:
            dto = self._map_ticket(ticket)
            # Enriquecendo DTO com dados do usuário (se o DTO suportar, ou adaptação)
            # O front espera user_name e user_email para a tabela de admin
            if hasattr(dto, 'user_name'): dto.user_name = user.name
            if hasattr(dto, 'user_email'): dto.user_email = user.email.value
            result.append(dto)
        return result
        
    async def change_ticket_status(self, ticket_id: uuid.UUID, status_str: str, user_id: uuid.UUID):
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise BusinessRuleException("Ticket não encontrado.")
        try:
            new_status = TicketStatus(status_str)
            ticket.status = new_status
            await self.ticket_repo.save(ticket)
        except ValueError:
            raise BusinessRuleException("Status inválido.")

    async def reply_ticket(self, ticket_id: uuid.UUID, dto: TicketReplyDTO, user_id: uuid.UUID):
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise BusinessRuleException("Ticket não encontrado.")
        
        ticket.add_message(user_id, dto.content, is_internal=dto.is_internal)
        await self.ticket_repo.save(ticket)

    def _map_ticket(self, t: Ticket) -> TicketResponseDTO:
        messages_dto = [
            TicketMessageDTO(
                id=m.id,
                content=m.content,
                created_at=m.created_at,
                sender_id=m.sender_id,
                is_internal=m.is_internal
            ) for m in t.messages
        ]
        
        # Criação do DTO base. Campos extras (user_name) são injetados no service se necessário.
        return TicketResponseDTO(
            id=t.id,
            subject=t.subject,
            status=t.status.value,
            priority=t.priority.value,
            created_at=t.created_at,
            messages=messages_dto
        )

# =============================================================================
# DEPENDENCIES
# =============================================================================

async def get_finance_service(db: AsyncSession = Depends(get_db)):
    customer_repo = SQLAlchemyCustomerRepository(db)
    transaction_repo = SQLAlchemyFinancialTransactionRepository(db)
    sale_repo = SQLAlchemySaleRepository(db) 
    return FinanceService(customer_repo, transaction_repo, sale_repo)

async def get_support_service(db: AsyncSession = Depends(get_db)):
    ticket_repo = SQLAlchemyTicketRepository(db)
    return SupportService(ticket_repo)

# =============================================================================
# ROUTERS
# =============================================================================

# --- FINANCE ROUTER ---

@router_finance.get("/{market_id}/dashboard", response_model=FinanceDashboardDTO)
async def get_financial_dashboard(
    market_id: uuid.UUID,
    service: FinanceService = Depends(get_finance_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna o resumo financeiro (Saldo, Receitas, Despesas, Fiado) para o dashboard.
    """
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    return await service.get_dashboard(market_id)

@router_finance.post("/{market_id}/customers", response_model=CustomerResponseDTO)
async def create_customer(
    market_id: uuid.UUID,
    dto: CustomerCreateDTO,
    service: FinanceService = Depends(get_finance_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    try:
        c = await service.register_customer(market_id, dto)
        return CustomerResponseDTO(
            id=c.id,
            name=c.name,
            cpf=c.cpf.value if c.cpf else None,
            phone=c.phone,
            credit_limit=c.credit_limit,
            current_debt=c.current_debt,
            status=c.status.value
        )
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))

@router_finance.get("/{market_id}/customers", response_model=List[CustomerResponseDTO])
async def list_customers(
    market_id: uuid.UUID,
    search: str = None,
    service: FinanceService = Depends(get_finance_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    customers = await service.list_customers(market_id, search)
    
    return [
        CustomerResponseDTO(
            id=c.id,
            name=c.name,
            cpf=c.cpf.value if c.cpf else None,
            phone=c.phone,
            credit_limit=c.credit_limit,
            current_debt=c.current_debt,
            status=c.status.value
        ) for c in customers
    ]

@router_finance.get("/{market_id}/customers/{customer_id}/ledger", response_model=List[LedgerEntryDTO])
async def get_customer_ledger(
    market_id: uuid.UUID,
    customer_id: uuid.UUID,
    service: FinanceService = Depends(get_finance_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna o extrato de movimentações (débitos e pagamentos) de um cliente.
    """
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    return await service.get_customer_ledger(market_id, customer_id)

@router_finance.post("/{market_id}/customers/{customer_id}/payment")
async def pay_debt(
    market_id: uuid.UUID,
    customer_id: uuid.UUID,
    dto: DebtPaymentDTO,
    service: FinanceService = Depends(get_finance_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    try:
        await service.register_payment(market_id, customer_id, dto)
        return {"message": "Pagamento registrado com sucesso."}
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))

@router_finance.post("/{market_id}/transactions", response_model=FinancialTransactionResponseDTO)
async def add_transaction(
    market_id: uuid.UUID,
    dto: TransactionCreateDTO,
    service: FinanceService = Depends(get_finance_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    try:
        t = await service.add_transaction(market_id, dto)
        return FinancialTransactionResponseDTO.model_validate(t)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))

@router_finance.get("/{market_id}/transactions", response_model=List[FinancialTransactionResponseDTO])
async def list_transactions(
    market_id: uuid.UUID,
    service: FinanceService = Depends(get_finance_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    return [FinancialTransactionResponseDTO.model_validate(t) for t in await service.list_transactions(market_id)]

# --- SUPPORT ROUTER ---

@router_support.post("/tickets", status_code=status.HTTP_201_CREATED)
async def open_ticket(
    dto: TicketCreateDTO,
    service: SupportService = Depends(get_support_service),
    current_user: User = Depends(get_current_user)
):
    return await service.create_ticket(current_user.id, dto)

@router_support.get("/tickets")
async def my_tickets(
    service: SupportService = Depends(get_support_service),
    current_user: User = Depends(get_current_user)
):
    return await service.get_user_tickets(current_user.id)

# --- ROTAS DE ADMINISTRAÇÃO DE SUPORTE (Faltantes) ---

@router_support.get("/tickets/all", response_model=List[TicketResponseDTO])
async def list_all_tickets_admin(
    service: SupportService = Depends(get_support_service),
    current_user: User = Depends(get_current_user)
):
    """
    Lista todos os tickets para o painel Administrativo (SaaS).
    """
    # TODO: Validar se current_user é ADMIN (role check)
    return await service.get_admin_tickets()

@router_support.post("/tickets/{ticket_id}/reply")
async def reply_ticket(
    ticket_id: uuid.UUID,
    dto: TicketReplyDTO,
    service: SupportService = Depends(get_support_service),
    current_user: User = Depends(get_current_user)
):
    """Responde a um ticket (Usuário ou Admin)."""
    try:
        await service.reply_ticket(ticket_id, dto, current_user.id)
        return {"message": "Resposta enviada com sucesso."}
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))

@router_support.patch("/tickets/{ticket_id}/status")
async def change_ticket_status(
    ticket_id: uuid.UUID,
    dto: TicketStatusUpdateDTO,
    service: SupportService = Depends(get_support_service),
    current_user: User = Depends(get_current_user)
):
    """Altera o status do ticket (Ex: Fechar, Em andamento)."""
    try:
        await service.change_ticket_status(ticket_id, dto.status, current_user.id)
        return {"message": "Status atualizado com sucesso."}
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))