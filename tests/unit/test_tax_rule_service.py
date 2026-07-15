import os
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

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
    TaxRuleNotFoundError,
    TaxRuleService,
)
from application.services.sales_service import SalesService
from domain.fiscal import (
    FiscalEnvironment,
    FiscalRuleEnforcement,
    FiscalRuleError,
    FiscalTenantConfig,
    ProductTaxRule,
    ProductTaxRuleStatus,
    TaxRegime,
    TaxRuleApproval,
)
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
        tax_parameters={
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
        },
        approval={"reference": "CRC-GO-2026-0001"},
    )


MARKET_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()


def publication_rule(**overrides) -> ProductTaxRule:
    values = {
        "market_id": MARKET_ID,
        "name": "Regra oficial GO",
        "effective_from": date(2026, 7, 15),
        "issuer_regime": TaxRegime.SIMPLES_NACIONAL,
        "destination_uf": "GO",
        "document_model": "65",
        "ncm": "22021000",
        "origin": "0",
        "cfop": "5102",
        "icms_group": "ICMSSN102",
        "icms_csosn": "102",
        "pis_cst": "07",
        "cofins_cst": "07",
        "tax_parameters": {
            "icms_mode": "non_taxed",
            "pis": {"group": "PIS07", "cst": "07"},
            "cofins": {"group": "COFINS07", "cst": "07"},
        },
        "approval": {
            "reference": "Decreto GO 10.734/2025, Anexo V-B",
            "checksum": "a" * 64,
        },
    }
    values.update(overrides)
    return ProductTaxRule(**values)


class LifecycleRuleRepository:
    def __init__(self, rule: ProductTaxRule):
        self.rule = rule
        self.lookups = []
        self.published = []

    async def get_rule(self, market_id, rule_id):
        self.lookups.append((market_id, rule_id))
        if self.rule.market_id == market_id and self.rule.id == rule_id:
            return self.rule
        return None

    async def publish_rule_with_approval(self, rule, approval):
        rule.approved_by = approval.accountant_user_id
        rule.approved_at = approval.approved_at
        rule.status = ProductTaxRuleStatus.PUBLISHED
        self.published.append((rule, approval))
        return rule


class VerifiedEvidenceService:
    async def capture_approval(
        self, *, rule_id, market_id, accountant_user_id, source_storage_key
    ):
        return TaxRuleApproval.from_verified_artifact(
            rule_id=rule_id,
            accountant_user_id=accountant_user_id,
            homologation_xml_storage_key=source_storage_key,
            canonical_xml=b"<NFe><protNFe/></NFe>",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("group", "regime", "cst", "csosn", "mode", "cest"),
    [
        ("ICMSSN102", TaxRegime.SIMPLES_NACIONAL, None, "102", "non_taxed", None),
        ("ICMS40", TaxRegime.LUCRO_PRESUMIDO, "40", None, "non_taxed", None),
        ("ICMSSN500", TaxRegime.SIMPLES_NACIONAL, None, "500", "retained_st", "0300700"),
        ("ICMS60", TaxRegime.LUCRO_REAL, "60", None, "retained_st", "0300700"),
    ],
)
async def test_publish_accepts_only_the_release_compatibility_matrix(
    group, regime, cst, csosn, mode, cest
) -> None:
    rule = publication_rule(
        issuer_regime=regime,
        icms_group=group,
        icms_cst=cst,
        icms_csosn=csosn,
        cest=cest,
        tax_parameters={
            "icms_mode": mode,
            "pis": {"group": "PIS07", "cst": "07"},
            "cofins": {"group": "COFINS07", "cst": "07"},
        },
    )
    repository = LifecycleRuleRepository(rule)
    service = TaxRuleService(repository, evidence_service=VerifiedEvidenceService())

    published = await service.publish(
        market_id=MARKET_ID,
        rule_id=rule.id,
        approved_by=uuid.uuid4(),
        source_storage_key=f"fiscal/homologacao/{MARKET_ID}/authorized.xml",
    )

    assert published.status is ProductTaxRuleStatus.PUBLISHED


@pytest.mark.asyncio
async def test_publish_requires_verifiable_official_reference_and_checksum() -> None:
    rule = publication_rule(approval={"reference": "Decreto GO 10.734/2025"})
    service = TaxRuleService(
        LifecycleRuleRepository(rule), evidence_service=VerifiedEvidenceService()
    )

    with pytest.raises(FiscalRuleError) as error:
        await service.publish(
            market_id=MARKET_ID,
            rule_id=rule.id,
            approved_by=uuid.uuid4(),
            source_storage_key=f"fiscal/homologacao/{MARKET_ID}/authorized.xml",
        )

    assert error.value.code == "tax_rule.evidence_required"


@pytest.mark.asyncio
async def test_publish_never_loads_a_rule_from_another_tenant() -> None:
    rule = publication_rule()
    repository = LifecycleRuleRepository(rule)
    foreign_market_id = uuid.uuid4()
    service = TaxRuleService(repository, evidence_service=VerifiedEvidenceService())

    with pytest.raises(FiscalRuleError) as error:
        await service.publish(
            market_id=foreign_market_id,
            rule_id=rule.id,
            approved_by=uuid.uuid4(),
            source_storage_key=f"fiscal/homologacao/{foreign_market_id}/authorized.xml",
        )

    assert error.value.code == "tax_rule.not_found"
    assert repository.lookups == [(foreign_market_id, rule.id)]


class AssignmentRuleRepository(LifecycleRuleRepository):
    def __init__(self, rule, *, response):
        super().__init__(rule)
        self.response = response
        self.assignment_calls = []

    async def assign_published_rule(self, **kwargs):
        self.assignment_calls.append(kwargs)
        return self.response


@pytest.mark.asyncio
async def test_assign_products_returns_previous_rule_mapping_for_audit_history() -> None:
    actor_id = uuid.uuid4()
    prior_rule_id = uuid.uuid4()
    rule = publication_rule(status=ProductTaxRuleStatus.PUBLISHED)
    repository = AssignmentRuleRepository(
        rule,
        response=(
            [PRODUCT_ID],
            [],
            [
                {
                    "product_id": str(PRODUCT_ID),
                    "before_rule_id": str(prior_rule_id),
                    "after_rule_id": str(rule.id),
                }
            ],
        ),
    )

    result = await TaxRuleService(repository).assign_products(
        market_id=MARKET_ID,
        rule_id=rule.id,
        product_ids=[PRODUCT_ID],
        effective_from=date.today(),
        actor_id=actor_id,
        reason="Reclassificação fiscal oficial",
    )

    assert result.updated_product_ids == [PRODUCT_ID]
    assert result.skipped == []
    assert result.previous_rule_ids == {str(PRODUCT_ID): str(prior_rule_id)}
    assert repository.assignment_calls[0]["actor_id"] == actor_id
    assert repository.assignment_calls[0]["reason"] == "Reclassificação fiscal oficial"


@pytest.mark.asyncio
async def test_assignment_rejects_missing_or_foreign_market_product_ids() -> None:
    foreign_product_id = uuid.uuid4()
    rule = publication_rule(status=ProductTaxRuleStatus.PUBLISHED)
    repository = AssignmentRuleRepository(
        rule,
        response=(
            [],
            [{"product_id": str(foreign_product_id), "reason": "product_not_found"}],
            [],
        ),
    )

    with pytest.raises(FiscalRuleError) as error:
        await TaxRuleService(repository).assign_products(
            market_id=MARKET_ID,
            rule_id=rule.id,
            product_ids=[foreign_product_id],
            effective_from=date.today(),
            actor_id=uuid.uuid4(),
            reason="Reclassificação fiscal oficial",
        )

    assert error.value.code == "tax_rule.product_market_mismatch"
    assert error.value.items == [{"product_id": str(foreign_product_id)}]


def pendency_product(name: str, *, legacy_rule_id=None) -> Product:
    return Product(
        market_id=MARKET_ID,
        name=name,
        code=name.upper().replace(" ", "-"),
        barcode=None,
        price=Decimal("10.00"),
        tax_rule_id=legacy_rule_id,
    )


class PendencyRepository:
    def __init__(self, associations):
        self.associations = associations
        self.requests = []

    async def list_product_rule_associations(self, market_id, product_ids):
        self.requests.append((market_id, product_ids))
        return {product_id: self.associations.get(product_id, []) for product_id in product_ids}


def association(rule, effective_from, effective_to=None):
    return SimpleNamespace(
        association_id=uuid.uuid4(),
        effective_from=effective_from,
        effective_to=effective_to,
        rule=rule,
    )


@pytest.mark.asyncio
async def test_pendencies_use_only_the_seven_stable_states_and_deterministic_summary() -> None:
    configured = pendency_product("Configurado")
    missing = pendency_product("Ausente")
    draft = pendency_product("Rascunho")
    expired = pendency_product("Expirado")
    future = pendency_product("Futuro")
    mismatch = pendency_product("Contexto divergente")
    legacy = pendency_product("Legado", legacy_rule_id=uuid.uuid4())
    when = date(2026, 7, 15)

    published_rule = publication_rule(status=ProductTaxRuleStatus.PUBLISHED)
    associations = {
        configured.id: [association(published_rule, date(2026, 7, 1))],
        draft.id: [
            association(
                publication_rule(status=ProductTaxRuleStatus.DRAFT), date(2026, 7, 1)
            )
        ],
        expired.id: [association(published_rule, date(2026, 6, 1), date(2026, 6, 30))],
        future.id: [association(published_rule, date(2026, 8, 1))],
        mismatch.id: [
            association(
                publication_rule(
                    status=ProductTaxRuleStatus.PUBLISHED, destination_uf="SP"
                ),
                date(2026, 7, 1),
            )
        ],
    }
    products = [configured, missing, draft, expired, future, mismatch, legacy]

    report = await TaxRuleService(PendencyRepository(associations)).list_pendencies(
        market_id=MARKET_ID,
        products=products,
        when=when,
        issuer_regime=TaxRegime.SIMPLES_NACIONAL,
        destination_uf="GO",
        document_model="65",
    )

    assert {str(item.product_id): item.status for item in report.items} == {
        str(configured.id): "configured",
        str(missing.id): "missing",
        str(draft.id): "draft",
        str(expired.id): "expired",
        str(future.id): "not_yet_effective",
        str(mismatch.id): "context_mismatch",
        str(legacy.id): "legacy_only",
    }
    assert report.summary == {
        "configured": 1,
        "missing": 1,
        "draft": 1,
        "expired": 1,
        "not_yet_effective": 1,
        "context_mismatch": 1,
        "legacy_only": 1,
        "total": 7,
    }


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
async def test_sale_before_rollout_created_association_is_not_resolved() -> None:
    current_rule = make_rule(version=2, status=ProductTaxRuleStatus.PUBLISHED, effective_from=date(2026, 7, 1))
    service = TaxRuleService(FakeHistoricalRuleRepository([
        (date(2026, 7, 21), None, current_rule),
    ]))

    with pytest.raises(TaxRuleNotFoundError, match="sem regra fiscal"):
        await service.resolve_for_sale_item(
            market_id=MARKET_ID,
            product_id=PRODUCT_ID,
            occurred_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            context=TaxContext.go_nfce_consumer_final(),
        )


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
    def __init__(self, mode=FiscalRuleEnforcement.BLOCK):
        self.mode = mode

    async def get_by_market(self, market_id):
        return FiscalTenantConfig(
            market_id=market_id,
            enabled=True,
            environment=FiscalEnvironment.PRODUCTION,
            fiscal_rule_enforcement=self.mode,
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
        created_at=datetime.now(timezone.utc),
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
    assert item.fiscal_tax_snapshot["icms"]["st_amount"] == Decimal("0.00")
    assert item.fiscal_tax_snapshot["icms"]["current_st_amount"] == "0.00"
    assert item.fiscal_tax_snapshot["icms"]["retained_st_amount"] == "25.20"
    assert item.snapshot_sha256
    assert item.fiscal_calculation_version == "marketfy-tax-calc.v2"


@pytest.mark.asyncio
async def test_missing_rule_returns_structured_error_without_persisting_partial_sale() -> None:
    service, sale_repository, product_repository = make_sale_service(FakeRuleRepository([]))

    with pytest.raises(FiscalRuleMissingError) as exc_info:
        await service.process_sync(MARKET_ID, [make_sale_request()])

    assert exc_info.value.code == "sale.fiscal_rule_missing"
    assert exc_info.value.affected_products == [{"id": str(product_repository.product.id), "name": "Refrigerante"}]
    assert sale_repository.saved == []
    assert product_repository.saved == []


@pytest.mark.asyncio
async def test_off_mode_preserves_legacy_sale_without_a_v1_snapshot() -> None:
    service, sale_repository, _ = make_sale_service(FakeRuleRepository([]))
    service.fiscal_config_repo = FakeFiscalConfigRepository(FiscalRuleEnforcement.OFF)

    await service.process_sync(MARKET_ID, [make_sale_request()])

    assert sale_repository.saved[0].items[0].fiscal_tax_snapshot is None
