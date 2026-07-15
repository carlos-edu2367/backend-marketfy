import os
import sys
import uuid
from datetime import date
from decimal import Decimal

import pytest


current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.fiscal import ProductTaxRule as CanonicalProductTaxRule
from domain.fiscal_tax import (
    EnforcementMode,
    FiscalRuleError,
    IcmsMode,
    ProductTaxRule,
    TaxRegime,
    TaxRuleStatus,
)
from domain.shared import BusinessRuleException


ProductTaxRuleStatus = TaxRuleStatus


def make_rule(**overrides) -> ProductTaxRule:
    values = {
        "market_id": uuid.uuid4(),
        "name": "Bebidas ST",
        "status": ProductTaxRuleStatus.DRAFT,
        "effective_from": date(2026, 7, 1),
        "issuer_regime": TaxRegime.SIMPLES_NACIONAL,
        "destination_uf": "GO",
        "document_model": "65",
        "ncm": "22021000",
        "cest": "0300700",
        "origin": "0",
        "cfop": "5405",
        "icms_group": "ICMSSN500",
        "icms_csosn": "500",
        "pis_cst": "07",
        "cofins_cst": "07",
    }
    values.update(overrides)
    return ProductTaxRule(**values)


def test_published_rule_cannot_be_mutated() -> None:
    rule = make_rule(status=ProductTaxRuleStatus.PUBLISHED)

    with pytest.raises(BusinessRuleException, match="imutável"):
        rule.rename("Bebidas corrigido")


def test_published_rule_rejects_direct_tax_field_mutation() -> None:
    rule = make_rule(
        status=ProductTaxRuleStatus.PUBLISHED,
        icms_st_rate=Decimal("18.00"),
    )

    with pytest.raises(BusinessRuleException, match="imutável"):
        rule.icms_st_rate = Decimal("12.00")


def test_rule_is_effective_on_inclusive_validity_boundaries() -> None:
    rule = make_rule(
        effective_from=date(2026, 7, 1),
        effective_to=date(2026, 7, 31),
    )

    assert rule.is_effective_on(date(2026, 7, 1))
    assert rule.is_effective_on(date(2026, 7, 31))
    assert not rule.is_effective_on(date(2026, 6, 30))
    assert not rule.is_effective_on(date(2026, 8, 1))


def test_v2_module_reexports_the_single_canonical_rule_and_compatibility_enums() -> None:
    assert ProductTaxRule is CanonicalProductTaxRule
    assert isinstance(TaxRuleStatus.PUBLISHED, str)
    assert TaxRuleStatus.PUBLISHED.value == "published"
    assert EnforcementMode.BLOCK.value == "block"
    assert IcmsMode.RETAINED_ST.value == "retained_st"


def test_public_mutability_guard_rejects_a_published_rule() -> None:
    with pytest.raises(BusinessRuleException, match="imutável"):
        make_rule(status=TaxRuleStatus.PUBLISHED).assert_mutable()


def test_rule_matches_only_its_complete_approved_context() -> None:
    rule = make_rule()

    assert rule.matches_context(destination_uf="GO", document_model="65")
    assert not rule.matches_context(destination_uf="SP", document_model="65")
    assert not rule.matches_context(destination_uf="GO", document_model="55")


def test_legacy_nullable_context_never_matches_a_v2_sale() -> None:
    assert not make_rule(issuer_regime=None).matches_context(
        destination_uf="GO", document_model="65"
    )
    assert not make_rule(destination_uf=None).matches_context(
        destination_uf="GO", document_model="65"
    )
    assert not make_rule(document_model=None).matches_context(
        destination_uf="GO", document_model="65"
    )


def test_published_rule_context_and_evidence_are_immutable() -> None:
    rule = make_rule(
        status=TaxRuleStatus.PUBLISHED,
        tax_parameters={"icms_mode": IcmsMode.RETAINED_ST.value},
        approval={"source": "accountant"},
    )

    with pytest.raises(BusinessRuleException, match="imutável"):
        rule.destination_uf = "SP"
    with pytest.raises(BusinessRuleException, match="imutável"):
        rule.tax_parameters = {}
    with pytest.raises(BusinessRuleException, match="imutável"):
        rule.approval = None


def test_structured_fiscal_rule_error_exposes_code_and_items() -> None:
    items = [{"field": "ncm", "reason": "required"}]
    error = FiscalRuleError("fiscal.rule_invalid", "Regra inválida", items)

    assert str(error) == "Regra inválida"
    assert error.code == "fiscal.rule_invalid"
    assert error.items == items


def test_sale_item_exposes_rule_id_and_sha256_evidence_aliases() -> None:
    from domain.sales import SaleItem

    rule_id = uuid.uuid4()
    item = SaleItem(
        sale_id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        product_name="Água",
        quantity=Decimal("1"),
        unit_price=Decimal("3.00"),
        total=Decimal("3.00"),
        tax_rule_id_snapshot=rule_id,
    )

    item.fiscal_snapshot_sha256 = "a" * 64

    assert item.tax_rule_id_snapshot == rule_id
    assert item.snapshot_sha256 == "a" * 64
    assert item.fiscal_snapshot_sha256 == "a" * 64
