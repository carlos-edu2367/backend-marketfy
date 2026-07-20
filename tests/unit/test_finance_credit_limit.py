"""Contrato de atualização do limite de crédito de clientes."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.dtos import CustomerCreditLimitUpdateDTO  # noqa: E402
from application.services.finance_support import FinanceService  # noqa: E402
from domain.finance import Customer  # noqa: E402
from domain.shared import BusinessRuleException  # noqa: E402


class FakeCustomerRepo:
    def __init__(self, customers):
        self.customers = {customer.id: customer for customer in customers}
        self.saved = []

    async def get_by_id(self, customer_id):
        return self.customers.get(customer_id)

    async def save(self, customer, commit=True):
        self.saved.append(customer)
        self.customers[customer.id] = customer
        return customer


def test_update_credit_limit_allows_limit_below_current_debt_without_ledger_entry():
    market_id = uuid.uuid4()
    customer = Customer(
        id=uuid.uuid4(),
        market_id=market_id,
        name="Cliente Fiado",
        credit_limit=Decimal("100.00"),
        current_debt=Decimal("80.00"),
    )
    repo = FakeCustomerRepo([customer])
    service = FinanceService(repo)

    updated = asyncio.run(
        service.update_customer_credit_limit(
            market_id,
            customer.id,
            CustomerCreditLimitUpdateDTO(credit_limit=Decimal("50.00")),
        )
    )

    assert updated.credit_limit == Decimal("50.00")
    assert updated.current_debt == Decimal("80.00")
    assert repo.saved == [customer]
    assert customer._pending_ledger == []
    with pytest.raises(BusinessRuleException, match="Limite de crédito excedido"):
        customer.add_debt(Decimal("0.01"))


def test_update_credit_limit_rejects_customer_from_another_market():
    customer = Customer(
        id=uuid.uuid4(),
        market_id=uuid.uuid4(),
        name="Cliente de outro mercado",
        credit_limit=Decimal("100.00"),
    )
    service = FinanceService(FakeCustomerRepo([customer]))

    with pytest.raises(BusinessRuleException, match="Cliente não encontrado"):
        asyncio.run(
            service.update_customer_credit_limit(
                uuid.uuid4(),
                customer.id,
                CustomerCreditLimitUpdateDTO(credit_limit=Decimal("50.00")),
            )
        )


def test_credit_limit_update_dto_rejects_negative_limit():
    with pytest.raises(ValidationError):
        CustomerCreditLimitUpdateDTO(credit_limit=Decimal("-0.01"))
