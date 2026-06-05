import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.dtos import CloseBoxDTO, DebtPaymentDTO, PlanCreateDTO, PlanUpdateDTO
from application.services.admin_service import AdminService
from application.services.finance_support import FinanceService
from application.services.sales_service import SalesService
from domain.finance import Customer, TransactionType
from domain.identity import CPF, Plan, PlanType
from domain.sales import Box, BoxStatus
from domain.shared import BusinessRuleException


class FakePlanRepo:
    def __init__(self, plan=None):
        self.plan = plan
        self.saved = []

    async def get_by_id(self, plan_id):
        return self.plan if self.plan and self.plan.id == plan_id else None

    async def save(self, plan, commit=True):
        self.plan = plan
        self.saved.append(plan)
        return plan

    async def list_all(self):
        return [self.plan] if self.plan else []


class FakeCustomerRepo:
    def __init__(self, customer):
        self.customer = customer
        self.saved = []

    async def get_by_id(self, customer_id):
        return self.customer if self.customer.id == customer_id else None

    async def save(self, customer, commit=True):
        self.saved.append(customer)
        return customer


class FakeFinancialRepo:
    def __init__(self):
        self.saved = []

    async def save(self, transaction, commit=True):
        self.saved.append(transaction)
        return transaction


class FakeBoxRepo:
    def __init__(self, open_box):
        self.open_box = open_box
        self.saved = []

    async def get_open_box_by_terminal(self, terminal_id):
        if self.open_box and self.open_box.terminal_id == terminal_id and self.open_box.status == BoxStatus.OPEN:
            return self.open_box
        return None

    async def get_by_id(self, box_id):
        return self.open_box if self.open_box and self.open_box.id == box_id else None

    async def save(self, box, commit=True):
        self.saved.append(box)
        return box


def make_paid_plan():
    return Plan(
        name="Pro",
        type=PlanType.PAID,
        max_markets=2,
        max_terminals=3,
        price_monthly=Decimal("99.90"),
        price_180days=Decimal("499.90"),
        price_annual=Decimal("899.90"),
    )


@pytest.mark.asyncio
async def test_admin_service_preserves_plan_active_flag_on_create_and_update():
    repo = FakePlanRepo()
    service = AdminService(repo)

    created = await service.create_plan(
        PlanCreateDTO(
            name="Inativo",
            type="pago",
            max_markets=1,
            max_terminals=1,
            price_monthly=Decimal("10.00"),
            price_180days=Decimal("50.00"),
            price_annual=Decimal("90.00"),
            is_active=False,
        )
    )
    assert created.is_active is False

    updated = await service.update_plan(created.id, PlanUpdateDTO(is_active=True, type="trial"))

    assert updated.is_active is True
    assert updated.type == PlanType.TRIAL


def test_debt_payment_dto_accepts_method_and_paid_at_contract():
    paid_at = datetime(2026, 5, 21, 12, 30)

    dto = DebtPaymentDTO(amount=Decimal("25.50"), payment_method="pix", paid_at=paid_at)

    assert dto.payment_method == "pix"
    assert dto.paid_at == paid_at


@pytest.mark.asyncio
async def test_debt_payment_generates_financial_transaction_with_method_in_description():
    market_id = uuid.uuid4()
    customer = Customer(
        market_id=market_id,
        name="Cliente Fiado",
        cpf=CPF("12345678900"),
        credit_limit=Decimal("200.00"),
        current_debt=Decimal("100.00"),
    )
    customer.id = uuid.uuid4()
    financial_repo = FakeFinancialRepo()
    service = FinanceService(FakeCustomerRepo(customer), financial_repo)

    paid_at = datetime(2026, 5, 21, 13, 0)
    result = await service.register_debt_payment(
        market_id,
        customer.id,
        DebtPaymentDTO(
            amount=Decimal("40.00"),
            description="Pagamento recebido",
            payment_method="pix",
            paid_at=paid_at,
        ),
    )

    assert result["new_debt"] == Decimal("60.00")
    assert len(financial_repo.saved) == 1
    transaction = financial_repo.saved[0]
    assert transaction.type == TransactionType.CREDIT
    assert transaction.amount == Decimal("40.00")
    assert transaction.paid_at == paid_at
    assert "pix" in transaction.description
    assert transaction.customer_id == customer.id


@pytest.mark.asyncio
async def test_debt_payment_strips_timezone_from_paid_at():
    """Garante que paid_at timezone-aware (vindo do frontend via ISO string com Z) é
    convertido para naive antes de gravar na tabela TIMESTAMP WITHOUT TIME ZONE."""
    market_id = uuid.uuid4()
    customer = Customer(
        market_id=market_id,
        name="Cliente TZ",
        credit_limit=Decimal("500.00"),
        current_debt=Decimal("100.00"),
    )
    customer.id = uuid.uuid4()
    financial_repo = FakeFinancialRepo()
    service = FinanceService(FakeCustomerRepo(customer), financial_repo)

    paid_at_aware = datetime(2026, 6, 4, 12, 57, 8, tzinfo=timezone.utc)
    await service.register_debt_payment(
        market_id,
        customer.id,
        DebtPaymentDTO(amount=Decimal("50.00"), payment_method="dinheiro", paid_at=paid_at_aware),
    )

    transaction = financial_repo.saved[0]
    assert transaction.paid_at.tzinfo is None, "paid_at deve ser naive para compatibilidade com TIMESTAMP WITHOUT TIME ZONE"
    assert transaction.due_date.tzinfo is None
    assert transaction.paid_at == paid_at_aware.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_close_box_with_id_rejects_wrong_box_id():
    market_id = uuid.uuid4()
    terminal_id = uuid.uuid4()
    open_box = Box(
        market_id=market_id,
        terminal_id=terminal_id,
        operator_id=uuid.uuid4(),
        initial_balance=Decimal("10.00"),
        current_balance=Decimal("10.00"),
    )
    service = SalesService(
        sale_repo=None,
        box_repo=FakeBoxRepo(open_box),
        product_repo=None,
        market_repo=None,
        terminal_repo=None,
        user_repo=None,
        plan_repo=None,
        financial_repo=None,
    )

    with pytest.raises(BusinessRuleException, match="Caixa informado"):
        await service.close_box(
            market_id,
            terminal_id,
            CloseBoxDTO(final_balance_reported=Decimal("10.00")),
            box_id=uuid.uuid4(),
        )
