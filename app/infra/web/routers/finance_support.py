"""Router de Finance e Support.

Após a Fase 3 / PR 11, este arquivo é fino: só HTTP, autorização e
serialização. A regra de negócio vive em
`application/services/finance_support.py`. A política de autorização
vive em `infra/security/market_access.py` e é aplicada via
`require_market_access` / `require_admin`.
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from application.dtos import (
    CustomerCreateDTO,
    CustomerCreditLimitUpdateDTO,
    CustomerResponseDTO,
    DebtPaymentDTO,
    LedgerEntryDTO,
    TicketCreateDTO,
    TicketReplyDTO,
    TicketResponseDTO,
    TicketStatusUpdateDTO,
    TransactionCreateDTO,
)
from application.dtos_finance import FinanceDashboardDTO, FinancialTransactionResponseDTO
from application.services.finance_support import FinanceService, SupportService
from application.services.audit_service import AuditService
from domain.identity import User
from domain.shared import BusinessRuleException
from infra.security.market_access import MarketPermission
from infra.web.dependencies import (
    get_current_user,
    get_audit_service,
    get_finance_service,
    get_support_service,
    require_admin,
    require_market_access,
)
from infra.observability.audit import record_audit_event

router_finance = APIRouter()
router_support = APIRouter()


def _customer_to_response(c) -> CustomerResponseDTO:
    return CustomerResponseDTO(
        id=c.id,
        name=c.name,
        cpf=c.cpf.value if c.cpf else None,
        phone=c.phone,
        credit_limit=c.credit_limit,
        current_debt=c.current_debt,
        status=c.status.value,
    )


# =============================================================================
# FINANCE ROUTER
# =============================================================================

@router_finance.get("/{market_id}/dashboard", response_model=FinanceDashboardDTO)
async def get_financial_dashboard(
    market_id: uuid.UUID,
    service: FinanceService = Depends(get_finance_service),
    market=Depends(require_market_access(MarketPermission.FINANCE_READ)),
):
    return await service.get_dashboard(market_id)


@router_finance.post("/{market_id}/customers", response_model=CustomerResponseDTO)
async def create_customer(
    market_id: uuid.UUID,
    dto: CustomerCreateDTO,
    service: FinanceService = Depends(get_finance_service),
    market=Depends(require_market_access(MarketPermission.FINANCE_WRITE)),
):
    try:
        c = await service.register_customer(market_id, dto)
        return _customer_to_response(c)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))


@router_finance.get("/{market_id}/customers", response_model=List[CustomerResponseDTO])
async def list_customers(
    market_id: uuid.UUID,
    search: Optional[str] = Query(None, description="Nome ou CPF"),
    service: FinanceService = Depends(get_finance_service),
    market=Depends(require_market_access(MarketPermission.FINANCE_READ)),
):
    customers = await service.list_customers(market_id, search)
    return [_customer_to_response(c) for c in customers]


@router_finance.patch("/{market_id}/customers/{customer_id}", response_model=CustomerResponseDTO)
async def update_customer_credit_limit(
    market_id: uuid.UUID,
    customer_id: uuid.UUID,
    dto: CustomerCreditLimitUpdateDTO,
    service: FinanceService = Depends(get_finance_service),
    market=Depends(require_market_access(MarketPermission.FINANCE_WRITE)),
):
    try:
        customer = await service.update_customer_credit_limit(market_id, customer_id, dto)
        return _customer_to_response(customer)
    except BusinessRuleException as e:
        raise HTTPException(status_code=404, detail=str(e))


@router_finance.get(
    "/{market_id}/customers/{customer_id}/ledger",
    response_model=List[LedgerEntryDTO],
)
async def get_customer_ledger(
    market_id: uuid.UUID,
    customer_id: uuid.UUID,
    service: FinanceService = Depends(get_finance_service),
    market=Depends(require_market_access(MarketPermission.FINANCE_READ)),
):
    try:
        return await service.get_customer_ledger(market_id, customer_id)
    except BusinessRuleException as e:
        raise HTTPException(status_code=404, detail=str(e))


@router_finance.post("/{market_id}/customers/{customer_id}/payment")
async def pay_debt(
    market_id: uuid.UUID,
    customer_id: uuid.UUID,
    dto: DebtPaymentDTO,
    service: FinanceService = Depends(get_finance_service),
    market=Depends(require_market_access(MarketPermission.FINANCE_WRITE)),
):
    try:
        return await service.register_debt_payment(market_id, customer_id, dto)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))


@router_finance.post(
    "/{market_id}/transactions", response_model=FinancialTransactionResponseDTO
)
async def add_transaction(
    market_id: uuid.UUID,
    dto: TransactionCreateDTO,
    service: FinanceService = Depends(get_finance_service),
    market=Depends(require_market_access(MarketPermission.FINANCE_WRITE)),
):
    try:
        t = await service.add_transaction(market_id, dto)
        return FinancialTransactionResponseDTO.model_validate(t)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))


@router_finance.get(
    "/{market_id}/transactions",
    response_model=List[FinancialTransactionResponseDTO],
)
async def list_transactions(
    market_id: uuid.UUID,
    service: FinanceService = Depends(get_finance_service),
    market=Depends(require_market_access(MarketPermission.FINANCE_READ)),
):
    return [
        FinancialTransactionResponseDTO.model_validate(t)
        for t in await service.list_transactions(market_id)
    ]


# =============================================================================
# SUPPORT ROUTER
# =============================================================================

@router_support.post("/tickets", status_code=status.HTTP_201_CREATED)
async def open_ticket(
    dto: TicketCreateDTO,
    service: SupportService = Depends(get_support_service),
    current_user: User = Depends(get_current_user),
):
    return await service.open_ticket(current_user.id, dto)


@router_support.get("/tickets")
async def my_tickets(
    service: SupportService = Depends(get_support_service),
    current_user: User = Depends(get_current_user),
):
    return await service.get_user_tickets(current_user)


@router_support.get("/tickets/all", response_model=List[TicketResponseDTO])
async def list_all_tickets_admin(
    service: SupportService = Depends(get_support_service),
    current_user: User = Depends(require_admin),
):
    """Lista todos os tickets para o painel Administrativo (SaaS)."""
    return await service.get_admin_tickets()


@router_support.post("/tickets/{ticket_id}/reply")
async def reply_ticket(
    request: Request,
    ticket_id: uuid.UUID,
    dto: TicketReplyDTO,
    service: SupportService = Depends(get_support_service),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
):
    """Responde a um ticket (Usuário dono ou Admin)."""
    try:
        await service.reply_ticket(ticket_id, dto, current_user)
        role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
        if role == "admin":
            await record_audit_event(
                audit,
                request,
                actor=current_user,
                action="support.ticket.admin_replied",
                resource_type="ticket",
                resource_id=str(ticket_id),
                result="success",
                metadata={"is_internal": dto.is_internal},
            )
        return {"message": "Resposta enviada com sucesso."}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))


@router_support.patch("/tickets/{ticket_id}/status")
async def change_ticket_status(
    request: Request,
    ticket_id: uuid.UUID,
    dto: TicketStatusUpdateDTO,
    service: SupportService = Depends(get_support_service),
    current_user: User = Depends(require_admin),
    audit: AuditService = Depends(get_audit_service),
):
    """Altera o status do ticket. Apenas admin."""
    try:
        await service.change_ticket_status(ticket_id, dto.status, current_user.id)
        await record_audit_event(
            audit,
            request,
            actor=current_user,
            action="support.ticket.status_changed",
            resource_type="ticket",
            resource_id=str(ticket_id),
            result="success",
            metadata={"status": dto.status},
        )
        return {"message": "Status atualizado com sucesso."}
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))
