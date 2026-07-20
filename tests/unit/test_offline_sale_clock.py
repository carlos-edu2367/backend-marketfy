import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest


current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.dtos import PaymentDTO, SaleCreateDTO, SaleItemDTO
from application.services.sales_service import SalesService
from domain.fiscal import FiscalEnvironment, FiscalTenantConfig
from domain.inventory import Product
from domain.sales import Box, BoxStatus
from domain.shared import BusinessRuleException


MARKET_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()


class SaleRepository:
    def __init__(self):
        self.saved = []

    async def get_by_id(self, _sale_id): return None
    async def get_by_offline_id(self, _market_id, _offline_id): return None
    async def save(self, sale, commit=True):
        self.saved.append(sale)
        return sale


class BoxRepository:
    def __init__(self, box):
        self.box, self.saved = box, []

    async def get_by_id(self, _box_id): return self.box
    async def save(self, box, commit=True):
        self.saved.append(box)
        return box


class ProductRepository:
    def __init__(self, product):
        self.product, self.saved = product, []

    async def get_by_id(self, _product_id): return self.product
    async def save(self, product, commit=True):
        self.saved.append(product)
        return product


class FiscalConfigRepository:
    async def get_by_market(self, _market_id):
        return FiscalTenantConfig(market_id=MARKET_ID, enabled=True, environment=FiscalEnvironment.PRODUCTION)


class FinancialRepository:
    def __init__(self): self.saved = []
    async def save(self, transaction, commit=True):
        self.saved.append(transaction)
        return transaction


def make_service():
    sale_repo = SaleRepository()
    product_repo = ProductRepository(Product(
        market_id=MARKET_ID, name="Bebida", code="BEB-01", barcode=None,
        price=Decimal("10.00"), ncm="22021000",
    ))
    box_repo = BoxRepository(Box(
        market_id=MARKET_ID, terminal_id=uuid.uuid4(), operator_id=uuid.uuid4(), status=BoxStatus.OPEN,
    ))
    financial_repo = FinancialRepository()
    return SalesService(
        sale_repo=sale_repo, box_repo=box_repo, product_repo=product_repo,
        market_repo=None, terminal_repo=None, user_repo=None, plan_repo=None,
        financial_repo=financial_repo, fiscal_config_repo=FiscalConfigRepository(),
        tax_rule_service=None, environment="production", fiscal_offline_max_age_minutes=15,
    ), sale_repo, product_repo, box_repo, financial_repo


def sale_request(created_at):
    return SaleCreateDTO(
        box_id=uuid.uuid4(), operator_id=uuid.uuid4(), total_amount=Decimal("10.00"), created_at=created_at,
        items=[SaleItemDTO(product_id=PRODUCT_ID, product_name="Bebida", quantity=Decimal("1"), unit_price=Decimal("10.00"), total=Decimal("10.00"))],
        payments=[PaymentDTO(method="dinheiro", amount=Decimal("10.00"))],
    )


@pytest.mark.asyncio
async def test_sale_older_than_offline_window_requires_fiscal_review():
    service, sale_repo, product_repo, box_repo, financial_repo = make_service()

    with pytest.raises(BusinessRuleException, match=r"sale\.offline_clock_out_of_window"):
        await service.process_sync(MARKET_ID, [sale_request(datetime.now(timezone.utc) - timedelta(minutes=16))])

    assert sale_repo.saved == []
    assert product_repo.saved == []
    assert box_repo.saved == []
    assert financial_repo.saved == []


@pytest.mark.asyncio
async def test_future_client_clock_requires_fiscal_review():
    service, sale_repo, product_repo, box_repo, financial_repo = make_service()

    with pytest.raises(BusinessRuleException, match=r"sale\.offline_clock_out_of_window"):
        await service.process_sync(MARKET_ID, [sale_request(datetime.now(timezone.utc) + timedelta(minutes=16))])

    assert sale_repo.saved == []
    assert product_repo.saved == []
    assert box_repo.saved == []
    assert financial_repo.saved == []


@pytest.mark.asyncio
async def test_sale_inside_offline_window_uses_client_time_for_fiscal_resolution():
    service, *_ = make_service()
    captured = {}

    async def resolve(*, market_id, sale, prepared_items):
        captured["created_at"] = sale.created_at
        captured["received_at"] = sale.received_at
        return [None] * len(prepared_items)

    service._resolve_fiscal_snapshots = resolve
    claimed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await service.process_sync(MARKET_ID, [sale_request(claimed_at)])

    assert captured["created_at"] == claimed_at
    assert captured["received_at"].tzinfo is timezone.utc
