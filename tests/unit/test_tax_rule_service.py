import os
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest


current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.dtos import PaymentDTO, SaleCreateDTO, SaleItemDTO
from application.services.fiscal.tax_rule_service import (
    FiscalRuleAmbiguousError,
    FiscalRuleMissingError,
    ProductTaxRuleCandidate,
    TaxContext,
    TaxRuleService,
)
from application.services.sales_service import SalesService
from domain.fiscal import FiscalEnvironment, FiscalTenantConfig, ProductTaxRule, ProductTaxRuleStatus
from domain.inventory import Product
from domain.sales import Box, BoxStatus


def make_rule(*, version: int, status: ProductTaxRuleStatus, effective_from: date, effective_to=None) -> ProductTaxRule:
    return ProductTaxRule(
        market_id=MARKET_ID,
        name="Bebidas ST",
        version=version,
        status=status,
        effective_from=effective_from,
        effective_to=effective_to,
        ncm="22021000",
        cest="0300700",
        origin="0",
        cfop="5405",
        icms_group="ICMSSN500",
        icms_csosn="500",
        icms_rate=Decimal("18.00"),
        icms_st_mva_rate=Decimal("40.00"),
        icms_st_rate=Decimal("18.00"),
        pis_cst="07",
        pis_rate=Decimal("0.00"),
        cofins_cst="07",
        cofins_rate=Decimal("0.00"),
    )


MARKET_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()


class FakeRuleRepository:
    def __init__(self, rules, association_ids=None):
        self.rules = rules
        self.association_ids = association_ids or [uuid.uuid4()] * len(rules)
        self.requests = []

    async def list_effective_linked_rules(self, market_id, product_id, occurred_on):
        self.requests.append((market_id, product_id, occurred_on))
        return [
            ProductTaxRuleCandidate(association_id, rule)
            for association_id, rule in zip(self.association_ids, self.rules)
        ]


@pytest.mark.asyncio
async def test_selects_latest_published_rule_effective_on_sale_date() -> None:
    v1 = make_rule(version=1, status=ProductTaxRuleStatus.PUBLISHED, effective_from=date(2026, 7, 1))
    v2 = make_rule(version=2, status=ProductTaxRuleStatus.PUBLISHED, effective_from=date(2026, 7, 15))
    draft = make_rule(version=3, status=ProductTaxRuleStatus.DRAFT, effective_from=date(2026, 7, 15))
    service = TaxRuleService(FakeRuleRepository([v1, v2, draft]))

    selected = await service.resolve_for_sale_item(
        market_id=MARKET_ID,
        product_id=PRODUCT_ID,
        occurred_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        context=TaxContext.go_nfce_consumer_final(),
    )

    assert selected.version == 2
    assert selected.status is ProductTaxRuleStatus.PUBLISHED


class FakeHistoricalRuleRepository:
    def __init__(self, associations):
        self.associations = [
            (uuid.uuid4(), starts_on, ends_on, rule)
            for starts_on, ends_on, rule in associations
        ]
        self.requests = []

    async def list_effective_linked_rules(self, market_id, product_id, occurred_on):
        self.requests.append((market_id, product_id, occurred_on))
        return [
            rule
            for association_id, starts_on, ends_on, rule in self.associations
            if starts_on <= occurred_on and (ends_on is None or occurred_on <= ends_on)
            for rule in [ProductTaxRuleCandidate(association_id, rule)]
        ]


@pytest.mark.asyncio
async def test_sale_before_reassignment_uses_prior_historical_association() -> None:
    prior_rule = make_rule(version=1, status=ProductTaxRuleStatus.PUBLISHED, effective_from=date(2026, 7, 1))
    current_rule = make_rule(version=2, status=ProductTaxRuleStatus.PUBLISHED, effective_from=date(2026, 7, 20))
    repository = FakeHistoricalRuleRepository([
        (date(2026, 7, 1), date(2026, 7, 19), prior_rule),
        (date(2026, 7, 20), None, current_rule),
    ])
    service = TaxRuleService(repository)

    selected = await service.resolve_for_sale_item(
        market_id=MARKET_ID,
        product_id=PRODUCT_ID,
        occurred_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        context=TaxContext.go_nfce_consumer_final(),
    )

    assert selected.id == prior_rule.id
    assert repository.requests == [(MARKET_ID, PRODUCT_ID, date(2026, 7, 15))]


@pytest.mark.asyncio
async def test_effective_date_takes_priority_over_numeric_rule_version() -> None:
    newer_numeric_version = make_rule(version=9, status=ProductTaxRuleStatus.PUBLISHED, effective_from=date(2026, 7, 1))
    newer_effective_rule = make_rule(version=2, status=ProductTaxRuleStatus.PUBLISHED, effective_from=date(2026, 7, 15))
    service = TaxRuleService(FakeRuleRepository([newer_numeric_version, newer_effective_rule]))

    selected = await service.resolve_for_sale_item(
        market_id=MARKET_ID,
        product_id=PRODUCT_ID,
        occurred_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        context=TaxContext.go_nfce_consumer_final(),
    )

    assert selected.id == newer_effective_rule.id


@pytest.mark.asyncio
async def test_current_historical_association_continues_to_resolve() -> None:
    current_rule = make_rule(version=2, status=ProductTaxRuleStatus.PUBLISHED, effective_from=date(2026, 7, 20))
    service = TaxRuleService(FakeHistoricalRuleRepository([
        (date(2026, 7, 20), None, current_rule),
    ]))

    selected = await service.resolve_for_sale_item(
        market_id=MARKET_ID,
        product_id=PRODUCT_ID,
        occurred_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        context=TaxContext.go_nfce_consumer_final(),
    )

    assert selected.id == current_rule.id


@pytest.mark.asyncio
async def test_concurrent_effective_associations_are_rejected_without_tie_breaking() -> None:
    first_rule = make_rule(version=1, status=ProductTaxRuleStatus.PUBLISHED, effective_from=date(2026, 7, 1))
    conflicting_rule = make_rule(version=2, status=ProductTaxRuleStatus.PUBLISHED, effective_from=date(2026, 7, 1))
    service = TaxRuleService(FakeRuleRepository(
        [first_rule, conflicting_rule],
        association_ids=[uuid.uuid4(), uuid.uuid4()],
    ))

    with pytest.raises(FiscalRuleAmbiguousError) as exc_info:
        await service.resolve_for_sale_item(
            market_id=MARKET_ID,
            product_id=PRODUCT_ID,
            occurred_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            context=TaxContext.go_nfce_consumer_final(),
        )

    assert exc_info.value.code == "sale.fiscal_rule_ambiguous"
    assert exc_info.value.details() == {
        "product_id": str(PRODUCT_ID),
        "occurred_on": "2026-07-20",
    }


class FakeSaleRepository:
    def __init__(self):
        self.saved = []

    async def get_by_id(self, sale_id):
        return None

    async def get_by_offline_id(self, market_id, offline_id):
        return None

    async def save(self, sale, commit=True):
        self.saved.append(sale)
        return sale


class FakeBoxRepository:
    def __init__(self, box):
        self.box = box
        self.saved = []

    async def get_by_id(self, box_id):
        return self.box

    async def save(self, box, commit=True):
        self.saved.append(box)
        return box


class FakeProductRepository:
    def __init__(self, product):
        self.product = product
        self.saved = []

    async def get_by_id(self, product_id):
        return self.product

    async def save(self, product, commit=True):
        self.saved.append(product)
        return product


class FakeFiscalConfigRepository:
    async def get_by_market(self, market_id):
        return FiscalTenantConfig(
            market_id=market_id,
            enabled=True,
            environment=FiscalEnvironment.PRODUCTION,
        )


class FakeFinancialRepository:
    async def save(self, transaction, commit=True):
        return transaction


def make_sale_service(rule_repository) -> tuple[SalesService, FakeSaleRepository, FakeProductRepository]:
    box = Box(
        market_id=MARKET_ID,
        terminal_id=uuid.uuid4(),
        operator_id=uuid.uuid4(),
        status=BoxStatus.OPEN,
    )
    product = Product(
        market_id=MARKET_ID,
        name="Refrigerante",
        code="REF-001",
        barcode=None,
        price=Decimal("100.00"),
        ncm="99999999",
        tax_rule_id=uuid.uuid4(),
    )
    sale_repository = FakeSaleRepository()
    product_repository = FakeProductRepository(product)
    service = SalesService(
        sale_repo=sale_repository,
        box_repo=FakeBoxRepository(box),
        product_repo=product_repository,
        market_repo=None,
        terminal_repo=None,
        user_repo=None,
        plan_repo=None,
        financial_repo=FakeFinancialRepository(),
        fiscal_config_repo=FakeFiscalConfigRepository(),
        tax_rule_service=TaxRuleService(rule_repository),
        environment="production",
    )
    return service, sale_repository, product_repository


def make_sale_request() -> SaleCreateDTO:
    return SaleCreateDTO(
        box_id=uuid.uuid4(),
        operator_id=uuid.uuid4(),
        total_amount=Decimal("100.00"),
        created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        items=[SaleItemDTO(product_id=PRODUCT_ID, product_name="Refrigerante", quantity=Decimal("1"), unit_price=Decimal("100.00"), total=Decimal("100.00"))],
        payments=[PaymentDTO(method="dinheiro", amount=Decimal("100.00"))],
    )


@pytest.mark.asyncio
async def test_sale_persists_rule_version_and_tax_snapshot() -> None:
    rule = make_rule(version=2, status=ProductTaxRuleStatus.PUBLISHED, effective_from=date(2026, 7, 1))
    service, sale_repository, _ = make_sale_service(FakeRuleRepository([rule]))

    response = await service.process_sync(MARKET_ID, [make_sale_request()])

    item = sale_repository.saved[0].items[0]
    assert response[0].id == sale_repository.saved[0].id
    assert item.tax_rule_version_snapshot == 2
    assert item.fiscal_tax_snapshot["rule_id"] == str(rule.id)
    assert item.fiscal_tax_snapshot["cfop"] == "5405"
    assert item.fiscal_tax_snapshot["icms"]["st_amount"] == Decimal("7.20")


@pytest.mark.asyncio
async def test_missing_rule_returns_structured_error_without_persisting_partial_sale() -> None:
    service, sale_repository, product_repository = make_sale_service(FakeRuleRepository([]))

    with pytest.raises(FiscalRuleMissingError) as exc_info:
        await service.process_sync(MARKET_ID, [make_sale_request()])

    assert exc_info.value.code == "sale.fiscal_rule_missing"
    assert exc_info.value.affected_products == [{"id": str(product_repository.product.id), "name": "Refrigerante"}]
    assert sale_repository.saved == []
    assert product_repository.saved == []
