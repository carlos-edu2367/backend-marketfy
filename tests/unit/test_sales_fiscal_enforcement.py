import os
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from application.dtos import PaymentDTO, SaleCreateDTO, SaleItemDTO
from application.services.fiscal.tax_rule_service import (
    FiscalRuleAmbiguousError,
    TaxRuleNotFoundError,
)
from application.services.fiscal.snapshot_integrity import canonical_sha256
from application.services.sales_service import SalesService
from domain.fiscal import (
    FiscalRuleEnforcement,
    FiscalRuleError,
    ProductTaxRule,
    ProductTaxRuleStatus,
)
from domain.inventory import Product
from domain.sales import (
    Box,
    BoxStatus,
    Sale,
    SaleItemFiscalEvidence,
    SaleStatus,
)
from infra.database.models import SaleItemModel, SaleModel
from infra.repositories.sqlalchemy_repos import SQLAlchemySaleRepository


MARKET_ID = uuid.uuid4()


def _rule(*, version: int = 2, tax_parameters=None) -> ProductTaxRule:
    if tax_parameters is None:
        tax_parameters = {
            "icms_mode": "retained_st",
            "retained_st_base": "140.00",
            "retained_st_rate": "18.0000",
            "retained_st_amount": "25.20",
            "pis": {
                "group": "PIS07",
                "cst": "07",
                "base": "0.00",
                "rate": "0.0000",
                "amount": "0.00",
            },
            "cofins": {
                "group": "COFINS07",
                "cst": "07",
                "base": "0.00",
                "rate": "0.0000",
                "amount": "0.00",
            },
        }
    return ProductTaxRule(
        market_id=MARKET_ID,
        name="Bebidas ST",
        version=version,
        status=ProductTaxRuleStatus.PUBLISHED,
        effective_from=date(2026, 7, 1),
        ncm="22021000",
        cest="0300700",
        origin="0",
        cfop="5405",
        icms_group="ICMSSN500",
        icms_csosn="500",
        pis_cst="07",
        cofins_cst="07",
        tax_parameters=tax_parameters,
        approval={"reference": "CRC-GO-2026-0001"},
    )


def _product(name: str = "Produto") -> Product:
    return Product(
        market_id=MARKET_ID,
        name=name,
        code=name.upper(),
        barcode=None,
        price=Decimal("10.00"),
        current_stock=Decimal("5.000"),
        ncm="22021000",
    )


def _sale_request(*, products, offline_id=None) -> SaleCreateDTO:
    return SaleCreateDTO(
        id=uuid.uuid4(),
        box_id=uuid.uuid4(),
        operator_id=uuid.uuid4(),
        total_amount=Decimal("10.00") * len(products),
        created_at=datetime.now(timezone.utc),
        offline_id=offline_id,
        items=[
            SaleItemDTO(
                product_id=product.id,
                product_name=product.name,
                quantity=Decimal("1"),
                unit_price=product.price,
                total=product.price,
            )
            for product in products
        ],
        payments=[
            PaymentDTO(
                method="dinheiro",
                amount=Decimal("10.00") * len(products),
            )
        ],
    )


def _service(*, products, mode, resolver) -> tuple[SalesService, SimpleNamespace]:
    box = Box(
        market_id=MARKET_ID,
        terminal_id=uuid.uuid4(),
        operator_id=uuid.uuid4(),
        status=BoxStatus.OPEN,
        current_balance=Decimal("20.00"),
    )
    sale_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=None),
        get_by_offline_id=AsyncMock(return_value=None),
        save=AsyncMock(side_effect=lambda sale, commit=True: sale),
    )
    box_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=box),
        save=AsyncMock(),
    )
    product_by_id = {product.id: product for product in products}
    product_repo = SimpleNamespace(
        get_by_id=AsyncMock(side_effect=product_by_id.get),
        save=AsyncMock(side_effect=lambda product, commit=True: product),
    )
    financial_repo = SimpleNamespace(
        save=AsyncMock(side_effect=lambda transaction, commit=True: transaction)
    )
    customer_repo = SimpleNamespace(
        get_by_cpf=AsyncMock(),
        save=AsyncMock(),
    )
    fiscal_config_repo = SimpleNamespace(
        get_by_market=AsyncMock(
            return_value=SimpleNamespace(fiscal_rule_enforcement=mode)
        )
    )
    tax_rule_service = SimpleNamespace(resolve_for_sale_item=resolver)
    service = SalesService(
        sale_repo=sale_repo,
        box_repo=box_repo,
        product_repo=product_repo,
        market_repo=None,
        terminal_repo=None,
        user_repo=None,
        plan_repo=None,
        financial_repo=financial_repo,
        customer_repo=customer_repo,
        fiscal_config_repo=fiscal_config_repo,
        tax_rule_service=tax_rule_service,
    )
    collaborators = SimpleNamespace(
        sale_repo=sale_repo,
        box_repo=box_repo,
        product_repo=product_repo,
        financial_repo=financial_repo,
        customer_repo=customer_repo,
        fiscal_config_repo=fiscal_config_repo,
        tax_rule_service=tax_rule_service,
        box=box,
    )
    return service, collaborators


@pytest.mark.asyncio
async def test_block_mode_missing_rules_are_aggregated_before_commercial_mutation() -> None:
    products = [_product("Refrigerante"), _product("Biscoito")]
    resolver = AsyncMock(side_effect=TaxRuleNotFoundError("Regra ausente"))
    service, deps = _service(
        products=products,
        mode=FiscalRuleEnforcement.BLOCK,
        resolver=resolver,
    )
    request = _sale_request(products=products)

    with pytest.raises(FiscalRuleError) as error:
        await service.process_sync(MARKET_ID, [request])

    assert error.value.code == "sale.fiscal_rule_missing"
    assert error.value.items == [
        {
            "code": "sale.fiscal_rule_missing",
            "product_id": str(products[0].id),
            "product_name": "Refrigerante",
        },
        {
            "code": "sale.fiscal_rule_missing",
            "product_id": str(products[1].id),
            "product_name": "Biscoito",
        },
    ]
    assert resolver.await_count == 2
    deps.box_repo.get_by_id.assert_not_awaited()
    deps.product_repo.save.assert_not_awaited()
    deps.box_repo.save.assert_not_awaited()
    deps.customer_repo.save.assert_not_awaited()
    deps.financial_repo.save.assert_not_awaited()
    deps.sale_repo.save.assert_not_awaited()
    assert [product.current_stock for product in products] == [
        Decimal("5.000"),
        Decimal("5.000"),
    ]
    assert deps.box.current_balance == Decimal("20.00")


@pytest.mark.asyncio
async def test_block_mode_rejects_offline_id_before_product_or_commercial_mutation() -> None:
    product = _product()
    resolver = AsyncMock()
    service, deps = _service(
        products=[product],
        mode=FiscalRuleEnforcement.BLOCK,
        resolver=resolver,
    )
    request = _sale_request(products=[product], offline_id="offline-queued-1")

    with pytest.raises(FiscalRuleError) as error:
        await service.process_sync(MARKET_ID, [request])

    assert error.value.code == "sale.fiscal_connection_required"
    assert error.value.items == [
        {
            "code": "sale.fiscal_connection_required",
            "offline_id": "offline-queued-1",
        }
    ]
    deps.product_repo.get_by_id.assert_not_awaited()
    resolver.assert_not_awaited()
    deps.box_repo.get_by_id.assert_not_awaited()
    deps.product_repo.save.assert_not_awaited()
    deps.box_repo.save.assert_not_awaited()
    deps.customer_repo.save.assert_not_awaited()
    deps.financial_repo.save.assert_not_awaited()
    deps.sale_repo.save.assert_not_awaited()
    assert product.current_stock == Decimal("5.000")
    assert deps.box.current_balance == Decimal("20.00")


@pytest.mark.asyncio
async def test_warn_mode_persists_available_v2_snapshot_and_allows_missing_rule() -> None:
    configured = _product("Configurado")
    missing = _product("Pendente")
    rule = _rule(version=2)
    resolver = AsyncMock(
        side_effect=[rule, TaxRuleNotFoundError("Regra ausente")]
    )
    service, deps = _service(
        products=[configured, missing],
        mode=FiscalRuleEnforcement.WARN,
        resolver=resolver,
    )

    result = await service.process_sync(
        MARKET_ID, [_sale_request(products=[configured, missing])]
    )

    saved_sale = deps.sale_repo.save.await_args_list[-1].args[0]
    configured_item, missing_item = saved_sale.items
    assert result[0].items[0].fiscal_tax_snapshot["rule_version"] == 2
    assert configured_item.tax_rule_id_snapshot == rule.id
    assert configured_item.tax_rule_version_snapshot == 2
    assert configured_item.fiscal_calculation_version == "marketfy-tax-calc.v2"
    assert configured_item.snapshot_sha256 == canonical_sha256(
        configured_item.fiscal_tax_snapshot
    )
    assert missing_item.fiscal_tax_snapshot is None
    assert missing_item.tax_rule_id_snapshot is None
    assert deps.product_repo.save.await_count == 2
    deps.financial_repo.save.assert_awaited_once()
    deps.sale_repo.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_block_mode_aggregates_missing_ambiguous_and_unsnapshottable_items() -> None:
    missing = _product("Sem regra")
    ambiguous = _product("Ambiguo")
    invalid = _product("Invalido")
    invalid_rule = _rule(tax_parameters={})
    resolver = AsyncMock(side_effect=[
        TaxRuleNotFoundError("Regra ausente"),
        FiscalRuleAmbiguousError(ambiguous.id, date(2026, 7, 15)),
        invalid_rule,
    ])
    service, deps = _service(
        products=[missing, ambiguous, invalid],
        mode=FiscalRuleEnforcement.BLOCK,
        resolver=resolver,
    )

    with pytest.raises(FiscalRuleError) as error:
        await service.process_sync(
            MARKET_ID, [_sale_request(products=[missing, ambiguous, invalid])]
        )

    assert error.value.code == "sale.fiscal_rule_invalid"
    assert [item["code"] for item in error.value.items] == [
        "sale.fiscal_rule_missing",
        "sale.fiscal_rule_ambiguous",
        "sale.fiscal_snapshot_invalid",
    ]
    assert [item["product_id"] for item in error.value.items] == [
        str(missing.id),
        str(ambiguous.id),
        str(invalid.id),
    ]
    deps.product_repo.save.assert_not_awaited()
    deps.box_repo.save.assert_not_awaited()
    deps.financial_repo.save.assert_not_awaited()
    deps.sale_repo.save.assert_not_awaited()


class _EmptyScalars:
    def first(self):
        return None


class _EmptyResult:
    def scalars(self):
        return _EmptyScalars()


class _RecordingSession:
    def __init__(self):
        self.added = []

    async def execute(self, _statement):
        return _EmptyResult()

    def add(self, model):
        self.added.append(model)


@pytest.mark.asyncio
async def test_sale_repository_round_trip_preserves_complete_v2_evidence() -> None:
    product = _product("Persistido")
    rule = _rule(version=3)
    resolver = AsyncMock(return_value=rule)
    service, deps = _service(
        products=[product],
        mode=FiscalRuleEnforcement.BLOCK,
        resolver=resolver,
    )
    await service.process_sync(MARKET_ID, [_sale_request(products=[product])])
    source_sale = deps.sale_repo.save.await_args.args[0]
    source_item = source_sale.items[0]

    session = _RecordingSession()
    repository = SQLAlchemySaleRepository(session)
    await repository.save(source_sale, commit=False)

    sale_model = next(model for model in session.added if isinstance(model, SaleModel))
    item_model = next(
        model for model in session.added if isinstance(model, SaleItemModel)
    )
    sale_model.items = [item_model]
    sale_model.payments = []
    sale_model.fiscal_documents = []
    reloaded = repository._to_entity(sale_model)
    reloaded_item = reloaded.items[0]

    assert reloaded_item.tax_rule_id_snapshot == source_item.tax_rule_id_snapshot
    assert reloaded_item.tax_rule_version_snapshot == 3
    assert reloaded_item.fiscal_calculation_version == "marketfy-tax-calc.v2"
    assert reloaded_item.fiscal_tax_snapshot == source_item.fiscal_tax_snapshot
    assert reloaded_item.snapshot_sha256 == source_item.snapshot_sha256
    assert reloaded_item.snapshot_sha256 == canonical_sha256(
        reloaded_item.fiscal_tax_snapshot
    )


@pytest.mark.asyncio
async def test_off_mode_preserves_commercial_flow_without_rule_lookup() -> None:
    product = _product("Legado")
    resolver = AsyncMock(side_effect=AssertionError("não deve resolver regra"))
    service, deps = _service(
        products=[product],
        mode=FiscalRuleEnforcement.OFF,
        resolver=resolver,
    )

    result = await service.process_sync(
        MARKET_ID, [_sale_request(products=[product])]
    )

    resolver.assert_not_awaited()
    assert result[0].items[0].fiscal_tax_snapshot is None
    deps.product_repo.save.assert_awaited_once()
    deps.financial_repo.save.assert_awaited_once()
    deps.sale_repo.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_retry_returns_frozen_sale_before_block_offline_gate() -> None:
    product = _product("Congelado")
    request = _sale_request(products=[product], offline_id="offline-retry-1")
    existing = Sale(
        market_id=MARKET_ID,
        box_id=request.box_id,
        operator_id=request.operator_id,
        status=SaleStatus.COMPLETED,
        offline_id=request.offline_id,
        created_at=request.created_at,
    )
    existing.id = request.id
    resolver = AsyncMock(side_effect=AssertionError("retry não deve resolver regra"))
    service, deps = _service(
        products=[product],
        mode=FiscalRuleEnforcement.BLOCK,
        resolver=resolver,
    )
    deps.sale_repo.get_by_id.return_value = existing

    result = await service.process_sync(MARKET_ID, [request])

    assert result[0].id == existing.id
    deps.fiscal_config_repo.get_by_market.assert_not_awaited()
    deps.product_repo.get_by_id.assert_not_awaited()
    resolver.assert_not_awaited()
    deps.product_repo.save.assert_not_awaited()
    deps.box_repo.save.assert_not_awaited()
    deps.financial_repo.save.assert_not_awaited()
    deps.sale_repo.save.assert_not_awaited()
