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

from domain.fiscal import ProductTaxRule, ProductTaxRuleStatus
from domain.shared import BusinessRuleException


def make_rule(**overrides) -> ProductTaxRule:
    values = {
        "market_id": uuid.uuid4(),
        "name": "Bebidas ST",
        "status": ProductTaxRuleStatus.DRAFT,
        "effective_from": date(2026, 7, 1),
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
