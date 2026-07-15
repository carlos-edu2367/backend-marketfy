"""Testes da Fase 3 (PR 9, 10, 11, 12).

Cobre:
- Política central `resolve_market_access`: owner, owner-only, membro,
  membro com role insuficiente, loja inexistente.
- Permissões por role no mapa `ROLE_PERMISSIONS`.
- Helper `require_admin` (via `require_admin_user`) já coberto na Fase 1.
- Refactor da FinanceService canônica: dashboard, list_transactions,
  register_payment (alias).
- SupportService canônica: create_ticket alias, reply_ticket com
  validação de dono/admin, get_user_tickets esconde mensagem interna.
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)


from application.dtos import (  # noqa: E402
    CustomerCreateDTO,
    DebtPaymentDTO,
    TicketCreateDTO,
    TicketReplyDTO,
    TransactionCreateDTO,
)
from application.services.finance_support import (  # noqa: E402
    FinanceService,
    SupportService,
)
from domain.finance import Customer, TransactionType  # noqa: E402
from domain.identity import CPF, Email, User, UserRole  # noqa: E402
from domain.shared import BusinessRuleException  # noqa: E402
from domain.support import Ticket  # noqa: E402
from infra.security.market_access import (  # noqa: E402
    MarketAccessDenied,
    MarketNotFoundError,
    MarketPermission,
    ROLE_PERMISSIONS,
    resolve_market_access,
    role_has_permission,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class FakeMarket:
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str = "Loja Fake"


@dataclass
class FakeUser:
    id: uuid.UUID
    role: UserRole = UserRole.OWNER


@dataclass
class FakeMember:
    market_id: uuid.UUID
    user_id: uuid.UUID
    role: UserRole
    is_active: bool = True


class FakeMarketRepo:
    def __init__(self, markets):
        self._markets = {m.id: m for m in markets}

    async def get_by_id(self, market_id):
        return self._markets.get(market_id)


class FakeMemberRepo:
    def __init__(self, members: List[FakeMember]):
        self._members = members

    async def get_active_member(self, market_id, user_id):
        for m in self._members:
            if m.market_id == market_id and m.user_id == user_id and m.is_active:
                return m
        return None


class FakeSession:
    pass


@pytest.fixture(autouse=True)
def patch_repos(monkeypatch):
    """Monkeypatch das classes Repo usadas internamente pelo policy."""
    market_repo_holder = {}
    member_repo_holder = {}

    def install(markets, members):
        market_repo_holder["repo"] = FakeMarketRepo(markets)
        member_repo_holder["repo"] = FakeMemberRepo(members)

        from infra.security import market_access as ma_mod

        def fake_market_repo_factory(session):
            return market_repo_holder["repo"]

        monkeypatch.setattr(ma_mod, "SQLAlchemyMarketRepository", fake_market_repo_factory)

        # Patch o import dinâmico de SQLAlchemyMarketMemberRepository
        import infra.repositories.market_member_repo as mm_mod

        def fake_member_repo_factory(session):
            return member_repo_holder["repo"]

        monkeypatch.setattr(
            mm_mod, "SQLAlchemyMarketMemberRepository", fake_member_repo_factory
        )

    return install


# ---------------------------------------------------------------------------
# PR 9: resolve_market_access
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_market_access_owner_passes(patch_repos):
    owner_id = uuid.uuid4()
    market_id = uuid.uuid4()
    patch_repos([FakeMarket(id=market_id, owner_id=owner_id)], [])

    user = FakeUser(id=owner_id, role=UserRole.OWNER)
    market = await resolve_market_access(
        market_id, user, FakeSession(), permission=MarketPermission.FINANCE_WRITE
    )
    assert market.id == market_id


@pytest.mark.asyncio
async def test_resolve_market_access_other_user_denied(patch_repos):
    owner_id = uuid.uuid4()
    intruder_id = uuid.uuid4()
    market_id = uuid.uuid4()
    patch_repos([FakeMarket(id=market_id, owner_id=owner_id)], [])

    user = FakeUser(id=intruder_id, role=UserRole.OWNER)
    with pytest.raises(MarketAccessDenied):
        await resolve_market_access(market_id, user, FakeSession())


@pytest.mark.asyncio
async def test_resolve_market_access_market_not_found(patch_repos):
    user_id = uuid.uuid4()
    patch_repos([], [])
    with pytest.raises(MarketNotFoundError):
        await resolve_market_access(
            uuid.uuid4(), FakeUser(id=user_id), FakeSession()
        )


@pytest.mark.asyncio
async def test_resolve_market_access_active_member_with_permission(patch_repos):
    owner_id = uuid.uuid4()
    member_id = uuid.uuid4()
    market_id = uuid.uuid4()
    patch_repos(
        [FakeMarket(id=market_id, owner_id=owner_id)],
        [FakeMember(market_id=market_id, user_id=member_id, role=UserRole.MANAGER)],
    )

    user = FakeUser(id=member_id, role=UserRole.MANAGER)
    market = await resolve_market_access(
        market_id, user, FakeSession(), permission=MarketPermission.FINANCE_READ
    )
    assert market.id == market_id


@pytest.mark.asyncio
async def test_resolve_market_access_cashier_lacks_finance_write(patch_repos):
    owner_id = uuid.uuid4()
    member_id = uuid.uuid4()
    market_id = uuid.uuid4()
    patch_repos(
        [FakeMarket(id=market_id, owner_id=owner_id)],
        [FakeMember(market_id=market_id, user_id=member_id, role=UserRole.CASHIER)],
    )

    user = FakeUser(id=member_id, role=UserRole.CASHIER)
    with pytest.raises(MarketAccessDenied):
        await resolve_market_access(
            market_id, user, FakeSession(), permission=MarketPermission.FINANCE_WRITE
        )


@pytest.mark.asyncio
async def test_resolve_market_access_inactive_member_denied(patch_repos):
    owner_id = uuid.uuid4()
    member_id = uuid.uuid4()
    market_id = uuid.uuid4()
    patch_repos(
        [FakeMarket(id=market_id, owner_id=owner_id)],
        [
            FakeMember(
                market_id=market_id,
                user_id=member_id,
                role=UserRole.MANAGER,
                is_active=False,
            )
        ],
    )

    user = FakeUser(id=member_id, role=UserRole.MANAGER)
    with pytest.raises(MarketAccessDenied):
        await resolve_market_access(
            market_id, user, FakeSession(), permission=MarketPermission.MARKET_READ
        )


@pytest.mark.asyncio
async def test_owner_only_rejects_member(patch_repos):
    owner_id = uuid.uuid4()
    member_id = uuid.uuid4()
    market_id = uuid.uuid4()
    patch_repos(
        [FakeMarket(id=market_id, owner_id=owner_id)],
        [FakeMember(market_id=market_id, user_id=member_id, role=UserRole.MANAGER)],
    )

    user = FakeUser(id=member_id, role=UserRole.MANAGER)
    with pytest.raises(MarketAccessDenied):
        await resolve_market_access(
            market_id, user, FakeSession(), owner_only=True
        )


@pytest.mark.asyncio
async def test_admin_saas_no_implicit_market_access(patch_repos):
    """Admin SaaS NÃO ganha acesso implícito a uma loja só por ser admin."""
    owner_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    market_id = uuid.uuid4()
    patch_repos([FakeMarket(id=market_id, owner_id=owner_id)], [])

    admin = FakeUser(id=admin_id, role=UserRole.ADMIN)
    with pytest.raises(MarketAccessDenied):
        await resolve_market_access(market_id, admin, FakeSession())


# ---------------------------------------------------------------------------
# Permissões por role
# ---------------------------------------------------------------------------

def test_role_permissions_owner_has_everything():
    owner_set = ROLE_PERMISSIONS[UserRole.OWNER]
    assert MarketPermission.FISCAL_WRITE in owner_set
    assert MarketPermission.ANALYTICS_READ in owner_set
    assert MarketPermission.SALES_WRITE in owner_set


def test_role_permissions_cashier_cannot_write_finance_or_fiscal():
    cashier_set = ROLE_PERMISSIONS[UserRole.CASHIER]
    assert MarketPermission.FINANCE_WRITE not in cashier_set
    assert MarketPermission.FISCAL_WRITE not in cashier_set
    assert MarketPermission.SALES_WRITE in cashier_set


def test_role_permissions_manager_can_write_fiscal():
    manager_set = ROLE_PERMISSIONS[UserRole.MANAGER]
    assert MarketPermission.FISCAL_WRITE in manager_set
    assert MarketPermission.FINANCE_WRITE in manager_set
    assert MarketPermission.ANALYTICS_READ in manager_set


def test_role_has_permission_none_means_any():
    assert role_has_permission(UserRole.CASHIER, None) is True


def test_role_has_permission_admin_has_no_market_permissions():
    """Admin SaaS não recebe permissões de loja por padrão."""
    assert role_has_permission(UserRole.ADMIN, MarketPermission.FINANCE_READ) is False


# ---------------------------------------------------------------------------
# PR 11: FinanceService canônica (refactor)
# ---------------------------------------------------------------------------

class FakeCustomerRepo:
    def __init__(self, customers=None, total_debt=Decimal("0.00")):
        self.customers = {c.id: c for c in (customers or [])}
        self.saved = []
        self.total_debt = total_debt
        self.ledger_entries = []

    async def get_by_id(self, customer_id):
        return self.customers.get(customer_id)

    async def get_by_cpf(self, market_id, cpf):
        for c in self.customers.values():
            if c.market_id == market_id and c.cpf and c.cpf.value == cpf:
                return c
        return None

    async def list_by_market(self, market_id, limit=100, offset=0):
        return [c for c in self.customers.values() if c.market_id == market_id]

    async def save(self, customer, commit=True):
        self.saved.append(customer)
        self.customers[customer.id] = customer
        return customer

    async def get_total_debt(self, market_id):
        return self.total_debt

    async def get_ledger(self, customer_id, limit=20):
        return list(self.ledger_entries)


class FakeFinancialRepo:
    def __init__(self, recent=None, summary=None):
        self.saved = []
        self._recent = recent or []
        self._summary = summary or []

    async def save(self, t, commit=True):
        self.saved.append(t)
        return t

    async def list_recent(self, market_id, limit=50):
        return list(self._recent)

    async def get_summary_by_month(self, market_id, year, month):
        return list(self._summary)


class FakeSaleRepo:
    def __init__(self, summary=None):
        self._summary = summary or []

    async def get_sales_summary_by_period(self, market_id, start, end):
        return list(self._summary)


@pytest.mark.asyncio
async def test_finance_service_register_payment_alias_routes_to_debt_payment():
    market_id = uuid.uuid4()
    customer = Customer(
        market_id=market_id,
        name="Cliente",
        cpf=CPF("12345678900"),
        credit_limit=Decimal("100.00"),
        current_debt=Decimal("60.00"),
    )
    customer.id = uuid.uuid4()
    fin = FakeFinancialRepo()
    svc = FinanceService(FakeCustomerRepo([customer]), fin)

    result = await svc.register_payment(
        market_id,
        customer.id,
        DebtPaymentDTO(amount=Decimal("20.00"), payment_method="pix"),
    )

    assert result["new_debt"] == Decimal("40.00")
    assert len(fin.saved) == 1


@pytest.mark.asyncio
async def test_finance_service_list_transactions_uses_list_recent():
    market_id = uuid.uuid4()
    repo = FakeFinancialRepo(recent=["t1", "t2"])
    svc = FinanceService(FakeCustomerRepo(), repo)
    txs = await svc.list_transactions(market_id)
    assert txs == ["t1", "t2"]


@pytest.mark.asyncio
async def test_finance_service_dashboard_consolida_receitas_e_despesas():
    market_id = uuid.uuid4()
    from domain.finance import FinancialTransaction

    revenue = FinancialTransaction(
        market_id=market_id,
        description="Receita manual",
        amount=Decimal("100.00"),
        type=TransactionType.CREDIT,
        due_date=datetime.utcnow(),
        category="Geral",
    )
    revenue.id = uuid.uuid4()
    revenue.created_at = datetime.utcnow()
    expense = FinancialTransaction(
        market_id=market_id,
        description="Despesa manual",
        amount=Decimal("30.00"),
        type=TransactionType.DEBIT,
        due_date=datetime.utcnow(),
        category="Geral",
    )
    expense.id = uuid.uuid4()
    expense.created_at = datetime.utcnow()

    fin = FakeFinancialRepo(recent=[revenue, expense], summary=[revenue, expense])
    sale_repo = FakeSaleRepo(summary=[{"total": Decimal("200.00"), "count": 5}])
    cust_repo = FakeCustomerRepo(total_debt=Decimal("75.00"))

    svc = FinanceService(cust_repo, fin, sale_repo)
    dash = await svc.get_dashboard(market_id)

    # 100 (manual) + 200 (vendas) = 300 receita; 30 despesa; saldo = 270
    assert dash.total_revenue == Decimal("300.00")
    assert dash.total_expense == Decimal("30.00")
    assert dash.balance == Decimal("270.00")
    assert dash.accounts_receivable == Decimal("75.00")


@pytest.mark.asyncio
async def test_finance_service_dashboard_requires_sale_repo():
    svc = FinanceService(FakeCustomerRepo(), FakeFinancialRepo())
    with pytest.raises(BusinessRuleException):
        await svc.get_dashboard(uuid.uuid4())


# ---------------------------------------------------------------------------
# SupportService canônica
# ---------------------------------------------------------------------------

class FakeTicketRepo:
    def __init__(self, tickets=None):
        self.tickets = {t.id: t for t in (tickets or [])}
        self.saved = []

    async def list_by_user(self, user_id):
        return [t for t in self.tickets.values() if t.requester_id == user_id]

    async def list_all_enriched(self):
        return []

    async def get_by_id(self, ticket_id):
        return self.tickets.get(ticket_id)

    async def save(self, ticket, commit=True):
        self.tickets[ticket.id] = ticket
        self.saved.append(ticket)
        return ticket


def make_user(role=UserRole.OWNER):
    return User(
        name="User",
        email=Email(f"{uuid.uuid4()}@marketfy.test"),
        cpf=CPF("12345678900"),
        password_hash="hash",
        role=role,
    )


@pytest.mark.asyncio
async def test_support_service_create_ticket_alias_calls_open_ticket():
    repo = FakeTicketRepo()
    svc = SupportService(repo)
    user_id = uuid.uuid4()
    ticket = await svc.create_ticket(user_id, TicketCreateDTO(subject="abc", message="Olá"))
    assert ticket.requester_id == user_id
    assert len(repo.saved) == 1


@pytest.mark.asyncio
async def test_support_service_reply_rejects_unrelated_user():
    requester = make_user(UserRole.OWNER)
    intruder = make_user(UserRole.OWNER)

    ticket = Ticket(requester_id=requester.id, market_id=None, subject="Bug")
    repo = FakeTicketRepo([ticket])
    svc = SupportService(repo)

    with pytest.raises(PermissionError):
        await svc.reply_ticket(
            ticket.id, TicketReplyDTO(content="hack", is_internal=False), intruder
        )


@pytest.mark.asyncio
async def test_support_service_owner_view_filters_internal_messages():
    requester = make_user(UserRole.OWNER)
    admin = make_user(UserRole.ADMIN)
    ticket = Ticket(requester_id=requester.id, market_id=None, subject="Bug")
    ticket.add_message(requester.id, "Mensagem publica")
    ticket.add_message(admin.id, "Nota interna", is_internal=True)
    repo = FakeTicketRepo([ticket])
    svc = SupportService(repo)

    dtos = await svc.get_user_tickets(requester)
    contents = [m.content for m in dtos[0].messages]
    assert contents == ["Mensagem publica"]


@pytest.mark.asyncio
async def test_support_service_admin_internal_flag_only_for_admin():
    requester = make_user(UserRole.OWNER)
    admin = make_user(UserRole.ADMIN)
    ticket = Ticket(requester_id=requester.id, market_id=None, subject="Bug")
    repo = FakeTicketRepo([ticket])
    svc = SupportService(repo)

    # Owner não pode escrever mensagem interna mesmo se enviar a flag.
    await svc.reply_ticket(
        ticket.id, TicketReplyDTO(content="oi", is_internal=True), requester
    )
    assert ticket.messages[-1].is_internal is False

    # Admin pode marcar internal=True.
    await svc.reply_ticket(
        ticket.id, TicketReplyDTO(content="nota", is_internal=True), admin
    )
    assert ticket.messages[-1].is_internal is True


# ---------------------------------------------------------------------------
# PR 10: market_member repo (entidade)
# ---------------------------------------------------------------------------

def test_market_member_dataclass_roundtrip():
    from infra.repositories.market_member_repo import MarketMember

    member = MarketMember(
        id=uuid.uuid4(),
        market_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        role=UserRole.MANAGER,
        is_active=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    assert member.role == UserRole.MANAGER
    assert member.is_active is True
