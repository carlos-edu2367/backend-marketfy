"""Serviços canônicos de Finance e Support (Fase 3 / PR 11).

Esta é a única fonte de regras de negócio de Finance/Support. O router
em `infra/web/routers/finance_support.py` deve apenas tratar HTTP,
autorização e serialização — toda lógica de domínio fica aqui.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from domain.finance import (
    Customer,
    CustomerLedger,
    CustomerStatus,
    FinancialTransaction,
    TransactionType,
)
from domain.interfaces import (
    CustomerRepositoryInterface,
    FinancialTransactionRepositoryInterface,
    SaleRepositoryInterface,
    TicketRepositoryInterface,
)
from domain.shared import BusinessRuleException, CPF
from domain.support import Ticket, TicketPriority, TicketStatus

from application.dtos import (
    CustomerCreateDTO,
    CustomerResponseDTO,
    DebtPaymentDTO,
    LedgerEntryDTO,
    TicketCreateDTO,
    TicketMessageDTO,
    TicketReplyDTO,
    TicketResponseDTO,
    TransactionCreateDTO,
)
from application.dtos_finance import FinanceDashboardDTO, FinancialTransactionResponseDTO
from infra.security.authorization import (
    can_reply_to_ticket,
    filter_ticket_messages_for_user,
    is_admin_user,
)


class FinanceService:
    def __init__(
        self,
        customer_repo: CustomerRepositoryInterface,
        transaction_repo: Optional[FinancialTransactionRepositoryInterface] = None,
        sale_repo: Optional[SaleRepositoryInterface] = None,
    ):
        self.customer_repo = customer_repo
        self.transaction_repo = transaction_repo
        self.sale_repo = sale_repo

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
            credit_limit=dto.credit_limit or Decimal("0.00"),
        )
        return await self.customer_repo.save(customer)

    async def list_customers(
        self, market_id: uuid.UUID, search: Optional[str] = None
    ) -> List[Customer]:
        # Mantém retorno em entidades de domínio; o router serializa em CustomerResponseDTO.
        customers = await self.customer_repo.list_by_market(market_id)
        if not search:
            return customers
        term = search.lower()
        return [
            c
            for c in customers
            if term in (c.name or "").lower()
            or (c.cpf and term in c.cpf.value)
        ]

    async def register_debt_payment(
        self,
        market_id: uuid.UUID,
        customer_id: uuid.UUID,
        dto: DebtPaymentDTO,
    ):
        customer = await self.customer_repo.get_by_id(customer_id)
        if not customer or customer.market_id != market_id:
            raise BusinessRuleException("Cliente não encontrado.")

        customer.pay_debt(dto.amount, dto.description)
        await self.customer_repo.save(customer, commit=self.transaction_repo is None)

        if self.transaction_repo:
            payment_method = dto.payment_method or "nao_informado"
            paid_at = dto.paid_at or datetime.utcnow()
            if getattr(paid_at, "tzinfo", None):
                paid_at = paid_at.replace(tzinfo=None)
            transaction = FinancialTransaction(
                market_id=market_id,
                description=f"{dto.description} ({payment_method})",
                amount=dto.amount,
                type=TransactionType.CREDIT,
                due_date=paid_at,
                paid_at=paid_at,
                category="Fiado",
                customer_id=customer.id,
            )
            await self.transaction_repo.save(transaction)

        return {"message": "Pagamento registrado com sucesso.", "new_debt": customer.current_debt}

    # Alias compatível com o nome usado pelo router atual.
    async def register_payment(
        self,
        market_id: uuid.UUID,
        customer_id: uuid.UUID,
        dto: DebtPaymentDTO,
    ):
        return await self.register_debt_payment(market_id, customer_id, dto)

    async def get_customer_ledger(
        self, market_id: uuid.UUID, customer_id: uuid.UUID
    ) -> List[LedgerEntryDTO]:
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
                sale_id=entry.sale_id,
            )
            for entry in ledger
        ]

    # --- TRANSAÇÕES (DESPESAS/RECEITAS) ---

    async def add_transaction(
        self, market_id: uuid.UUID, dto: TransactionCreateDTO
    ) -> FinancialTransaction:
        """Lança uma despesa ou receita manual."""
        if not self.transaction_repo:
            raise BusinessRuleException("Repositório financeiro não configurado.")

        try:
            t_type = TransactionType(dto.type)
        except ValueError:
            raise BusinessRuleException("Tipo de transação inválido. Use 'receita' ou 'despesa'.")

        due_date_naive = dto.due_date.replace(tzinfo=None) if getattr(dto.due_date, "tzinfo", None) else dto.due_date
        paid_at_naive = dto.paid_at.replace(tzinfo=None) if dto.paid_at and getattr(dto.paid_at, "tzinfo", None) else dto.paid_at

        transaction = FinancialTransaction(
            market_id=market_id,
            description=dto.description,
            amount=dto.amount,
            type=t_type,
            due_date=due_date_naive,
            paid_at=paid_at_naive,
            category=dto.category,
        )

        return await self.transaction_repo.save(transaction)

    async def list_transactions(
        self, market_id: uuid.UUID, limit: int = 50
    ) -> List[FinancialTransaction]:
        if not self.transaction_repo:
            raise BusinessRuleException("Repositório financeiro não configurado.")
        if hasattr(self.transaction_repo, "list_recent"):
            return await self.transaction_repo.list_recent(market_id, limit)
        return await self.transaction_repo.list_by_market(market_id)

    async def get_dashboard(self, market_id: uuid.UUID) -> FinanceDashboardDTO:
        """Consolida vendas + transações manuais + dívidas para o dashboard."""
        if not self.transaction_repo or not self.sale_repo:
            raise BusinessRuleException("Dependências financeiras não configuradas.")

        receivables = await self.customer_repo.get_total_debt(market_id)

        now = datetime.utcnow()
        transactions = await self.transaction_repo.get_summary_by_month(
            market_id, now.year, now.month
        )

        manual_income = sum(
            t.amount
            for t in transactions
            if t.type == TransactionType.CREDIT or getattr(t.type, "value", "") == "receita"
        )
        manual_expense = sum(
            t.amount
            for t in transactions
            if t.type == TransactionType.DEBIT or getattr(t.type, "value", "") == "despesa"
        )

        start_date = datetime(now.year, now.month, 1).date()
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_date = datetime(now.year, now.month, last_day).date()

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())

        sales_summary = await self.sale_repo.get_sales_summary_by_period(
            market_id, start_dt, end_dt
        )
        sales_income = sum(s["total"] for s in sales_summary)

        total_revenue = manual_income + sales_income
        balance = total_revenue - manual_expense

        recent = await self.transaction_repo.list_recent(market_id, 10)
        recent_dtos = [FinancialTransactionResponseDTO.model_validate(r) for r in recent]

        return FinanceDashboardDTO(
            balance=balance,
            total_revenue=total_revenue,
            total_expense=manual_expense,
            accounts_receivable=receivables,
            recent_transactions=recent_dtos,
        )


class SupportService:
    def __init__(self, ticket_repo: TicketRepositoryInterface):
        self.ticket_repo = ticket_repo

    async def open_ticket(self, user_id: uuid.UUID, dto: TicketCreateDTO) -> Ticket:
        ticket = Ticket(
            requester_id=user_id,
            market_id=None,
            subject=dto.subject,
        )
        ticket.add_message(user_id, dto.message, is_internal=False)
        return await self.ticket_repo.save(ticket)

    # Alias para compatibilidade com router antigo.
    async def create_ticket(self, user_id: uuid.UUID, dto: TicketCreateDTO) -> Ticket:
        return await self.open_ticket(user_id, dto)

    async def get_user_tickets(self, user) -> List[TicketResponseDTO]:
        """Aceita o objeto `User` ou apenas o `user_id` (compat)."""
        user_id = getattr(user, "id", user)
        tickets = await self.ticket_repo.list_by_user(user_id)
        viewer = user if hasattr(user, "id") else None
        return [self._map_ticket_dto(t, viewer=viewer) for t in tickets]

    async def get_admin_tickets(self) -> List[TicketResponseDTO]:
        data = await self.ticket_repo.list_all_enriched()
        dtos = []
        for ticket, user in data:
            dto = self._map_ticket_dto(ticket)
            dto.user_email = user.email.value
            dto.user_name = user.name
            dtos.append(dto)
        return dtos

    def _map_ticket_dto(self, ticket: Ticket, viewer=None) -> TicketResponseDTO:
        messages = (
            filter_ticket_messages_for_user(ticket, viewer)
            if viewer is not None
            else (ticket.messages or [])
        )
        response = TicketResponseDTO(
            id=ticket.id,
            subject=ticket.subject,
            status=ticket.status.value,
            priority=ticket.priority.value,
            created_at=ticket.created_at,
            user_email="",
            user_name="",
            messages=[],
        )

        if messages:
            response.messages = sorted(
                [
                    TicketMessageDTO(
                        id=m.id,
                        content=m.content,
                        created_at=m.created_at,
                        sender_id=m.sender_id,
                        is_internal=m.is_internal,
                    )
                    for m in messages
                ],
                key=lambda x: x.created_at,
            )
        return response

    async def reply_ticket(self, ticket_id: uuid.UUID, dto: TicketReplyDTO, user) -> Ticket:
        """Resposta de usuário ou admin. Valida posse via authorization helper."""
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise BusinessRuleException("Ticket não encontrado.")
        if not can_reply_to_ticket(ticket, user):
            raise PermissionError("Acesso negado ao ticket.")

        is_internal = bool(getattr(dto, "is_internal", False)) if is_admin_user(user) else False
        ticket.add_message(user.id, dto.content, is_internal=is_internal)
        return await self.ticket_repo.save(ticket)

    async def add_admin_message(
        self, ticket_id: uuid.UUID, admin_id: uuid.UUID, content: str, is_internal: bool
    ):
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise BusinessRuleException("Ticket não encontrado.")
        ticket.add_message(admin_id, content, is_internal)
        return await self.ticket_repo.save(ticket)

    async def change_ticket_status(
        self,
        ticket_id: uuid.UUID,
        new_status_str: str,
        user_id: Optional[uuid.UUID] = None,
    ):
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise BusinessRuleException("Ticket não encontrado.")
        try:
            status_enum = TicketStatus(new_status_str)
        except ValueError:
            raise BusinessRuleException(f"Status inválido: {new_status_str}")

        if user_id is not None and hasattr(ticket, "change_status"):
            ticket.change_status(status_enum, user_id)
        else:
            ticket.status = status_enum
        await self.ticket_repo.save(ticket)
