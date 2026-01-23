import uuid
from typing import Optional, List, Dict
from decimal import Decimal
from datetime import datetime
from domain.finance import Customer, CustomerStatus, FinancialTransaction, TransactionType, CustomerLedger
from domain.support import Ticket, TicketPriority, TicketStatus
from domain.interfaces import CustomerRepositoryInterface, TicketRepositoryInterface, FinancialTransactionRepositoryInterface
from domain.shared import BusinessRuleException, CPF
from application.dtos import (
    CustomerCreateDTO, TicketCreateDTO, DebtPaymentDTO, CustomerResponseDTO, 
    LedgerEntryDTO, TicketResponseDTO, TicketMessageDTO, TransactionCreateDTO
)

class FinanceService:
    def __init__(self, 
                 customer_repo: CustomerRepositoryInterface,
                 transaction_repo: FinancialTransactionRepositoryInterface = None):
        self.customer_repo = customer_repo
        self.transaction_repo = transaction_repo

    # --- GESTÃO DE CLIENTES ---

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

    async def list_customers(self, market_id: uuid.UUID) -> List[CustomerResponseDTO]:
        customers = await self.customer_repo.list_by_market(market_id)
        return [
            CustomerResponseDTO(
                id=c.id,
                name=c.name,
                cpf=str(c.cpf) if c.cpf else None,
                phone=c.phone,
                credit_limit=c.credit_limit,
                current_debt=c.current_debt,
                status=c.status.value
            ) for c in customers
        ]

    async def register_debt_payment(self, market_id: uuid.UUID, customer_id: uuid.UUID, dto: DebtPaymentDTO):
        customer = await self.customer_repo.get_by_id(customer_id)
        if not customer or customer.market_id != market_id:
            raise BusinessRuleException("Cliente não encontrado.")
            
        customer.pay_debt(dto.amount, dto.description)
        await self.customer_repo.save(customer)
        
        # Opcional: Gerar uma FinancialTransaction de RECEITA no caixa geral automaticamente?
        # Para simplificar, mantemos separado, mas seria um bom ponto de integração.
        
        return {"message": "Pagamento registrado com sucesso.", "new_debt": customer.current_debt}

    async def get_customer_ledger(self, market_id: uuid.UUID, customer_id: uuid.UUID) -> List[LedgerEntryDTO]:
        customer = await self.customer_repo.get_by_id(customer_id)
        if not customer or customer.market_id != market_id:
            raise BusinessRuleException("Cliente não encontrado.")
            
        ledger = await self.customer_repo.get_ledger(customer_id)
        return [
            LedgerEntryDTO(
                id=entry.id,
                amount=entry.amount,
                type=entry.type.value,
                description=entry.description,
                created_at=entry.created_at,
                sale_id=entry.sale_id
            ) for entry in ledger
        ]

    # --- GESTÃO DE TRANSAÇÕES (DESPESAS/RECEITAS) ---

    async def add_transaction(self, market_id: uuid.UUID, dto: TransactionCreateDTO) -> FinancialTransaction:
        """Lança uma despesa ou receita manual."""
        if not self.transaction_repo:
            raise BusinessRuleException("Repositório financeiro não configurado.")

        try:
            t_type = TransactionType(dto.type)
        except ValueError:
            raise BusinessRuleException("Tipo de transação inválido. Use 'receita' ou 'despesa'.")

        transaction = FinancialTransaction(
            market_id=market_id,
            description=dto.description,
            amount=dto.amount,
            type=t_type,
            due_date=dto.due_date, # Pode ser data futura (contas a pagar)
            paid_at=dto.paid_at,   # Se vier preenchido, já entra como liquidado
            category=dto.category
        )

        return await self.transaction_repo.save(transaction)

class SupportService:
    def __init__(self, ticket_repo: TicketRepositoryInterface):
        self.ticket_repo = ticket_repo

    async def open_ticket(self, user_id: uuid.UUID, dto: TicketCreateDTO) -> Ticket:
        ticket = Ticket(
            requester_id=user_id,
            market_id=None, # Teria que pegar do contexto do user se fosse relevante
            subject=dto.subject
        )
        # Adiciona a primeira mensagem
        ticket.add_message(user_id, dto.message, is_internal=False)
        return await self.ticket_repo.save(ticket)

    async def get_user_tickets(self, user_id: uuid.UUID) -> List[TicketResponseDTO]:
        tickets = await self.ticket_repo.list_by_user(user_id)
        return [self._map_ticket_dto(t) for t in tickets]

    async def get_admin_tickets(self) -> List[TicketResponseDTO]:
        # Traz tickets enriquecidos com dados do usuário (Tupla Ticket, User)
        data = await self.ticket_repo.list_all_enriched()
        
        dtos = []
        for ticket, user in data:
            dto = self._map_ticket_dto(ticket)
            # Enriquece com dados do usuário para o admin saber quem pediu
            dto.user_email = user.email.value
            dto.user_name = user.name
            dtos.append(dto)
        return dtos

    def _map_ticket_dto(self, ticket: Ticket) -> TicketResponseDTO:
        response_dtos = TicketResponseDTO(
            id=ticket.id,
            subject=ticket.subject,
            status=ticket.status.value,
            priority=ticket.priority.value,
            created_at=ticket.created_at,
            user_email="", # Preenchido depois se for admin view
            user_name="",
            messages=[]
        )
        
        if ticket.messages:
            response_dtos.messages = sorted(
                [
                    TicketMessageDTO(
                        id=m.id,
                        content=m.content,
                        created_at=m.created_at,
                        sender_id=m.sender_id,
                        is_internal=m.is_internal
                    ) for m in ticket.messages
                ],
                key=lambda x: x.created_at
            )
            
        return response_dtos

    async def add_admin_message(self, ticket_id: uuid.UUID, admin_id: uuid.UUID, content: str, is_internal: bool):
        """Admin responde ao ticket."""
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise BusinessRuleException("Ticket não encontrado.")
            
        ticket.add_message(admin_id, content, is_internal)
        
        # Se for mensagem externa, muda status para aguardando usuario (opcional)
        if not is_internal and ticket.status != TicketStatus.CLOSED:
             # from domain.support import TicketStatus
             # ticket.status = TicketStatus.WAITING_USER
             pass

        return await self.ticket_repo.save(ticket)

    async def change_ticket_status(self, ticket_id: uuid.UUID, new_status_str: str, user_id: uuid.UUID):
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise BusinessRuleException("Ticket não encontrado.")
            
        try:
            status_enum = TicketStatus(new_status_str)
        except ValueError:
            raise BusinessRuleException(f"Status inválido: {new_status_str}")

        ticket.change_status(status_enum, user_id)
        await self.ticket_repo.save(ticket)