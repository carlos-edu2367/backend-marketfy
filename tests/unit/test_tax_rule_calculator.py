import os
import sys
import uuid
from decimal import Decimal

import pytest


current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.fiscal.tax_rule_calculator import TaxRuleCalculator  # noqa: E402
from domain.fiscal import ProductTaxRule, ProductTaxRuleStatus  # noqa: E402
from domain.sales import SaleItem  # noqa: E402


def make_st_rule(**overrides) -> ProductTaxRule:
    values = {
        "market_id": uuid.uuid4(),
        "name": "Refrigerante ST",
        "status": ProductTaxRuleStatus.PUBLISHED,
        "effective_from": __import__("datetime").date(2026, 7, 1),
        "ncm": "22021000",
        "cest": "0300700",
        "origin": "0",
        "cfop": "5405",
        "icms_group": "ICMSSN500",
        "icms_csosn": "500",
        "icms_rate": Decimal("18.00"),
        "icms_st_mva_rate": Decimal("40.00"),
        "icms_st_rate": Decimal("18.00"),
        "pis_cst": "07",
        "pis_rate": Decimal("0.00"),
        "cofins_cst": "07",
        "cofins_rate": Decimal("0.00"),
    }
    values.update(overrides)
    return ProductTaxRule(**values)


def make_item(total: str = "100.00") -> SaleItem:
    return SaleItem(
        sale_id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        product_name="Refrigerante",
        quantity=Decimal("1"),
        unit_price=Decimal(total),
        total=Decimal(total),
    )


def test_st_total_uses_base_mva_and_rate_from_approved_rule() -> None:
    snapshot = TaxRuleCalculator().calculate(
        item=make_item(),
        rule=make_st_rule(),
    )

    assert snapshot.icms.own_base == Decimal("100.00")
    assert snapshot.icms.own_amount == Decimal("18.00")
    assert snapshot.icms.st_base == Decimal("140.00")
    assert snapshot.icms.st_amount == Decimal("7.20")
    assert snapshot.cfop == "5405"
    assert snapshot.cest == "0300700"


def test_calculation_rounds_money_half_up_at_two_decimal_places() -> None:
    snapshot = TaxRuleCalculator().calculate(
        item=make_item("10.01"),
        rule=make_st_rule(
            icms_rate=Decimal("17.00"),
            icms_st_mva_rate=Decimal("33.33"),
            icms_st_rate=Decimal("18.00"),
        ),
    )

    assert snapshot.icms.own_amount == Decimal("1.70")
    assert snapshot.icms.st_base == Decimal("13.35")
    assert snapshot.icms.st_amount == Decimal("0.70")


def test_calculation_applies_the_approved_icms_base_reduction_before_rates() -> None:
    snapshot = TaxRuleCalculator().calculate(
        item=make_item("100.00"),
        rule=make_st_rule(icms_reduction_rate=Decimal("20.00")),
    )

    assert snapshot.icms.own_base == Decimal("80.00")
    assert snapshot.icms.own_amount == Decimal("14.40")
    assert snapshot.icms.st_base == Decimal("112.00")
    assert snapshot.icms.st_amount == Decimal("5.76")


@pytest.mark.parametrize(
    ("group", "cst", "csosn"),
    [("ICMSSN102", None, "102"), ("ICMS40", "40", None)],
)
def test_non_taxed_groups_calculate_only_zero_values(
    group: str, cst: str | None, csosn: str | None
) -> None:
    snapshot = TaxRuleCalculator().calculate(
        item=make_item(),
        rule=make_st_rule(
            icms_group=group,
            icms_cst=cst,
            icms_csosn=csosn,
            icms_rate=Decimal("0.00"),
            icms_st_mva_rate=None,
            icms_st_rate=None,
            fcp_rate=Decimal("0.00"),
        ),
    )

    assert (
        snapshot.icms.own_base,
        snapshot.icms.own_amount,
        snapshot.icms.st_base,
        snapshot.icms.st_amount,
        snapshot.icms.fcp_amount,
    ) == (Decimal("0.00"),) * 5
    assert (snapshot.pis.base, snapshot.pis.rate, snapshot.pis.amount) == (
        Decimal("0.00"),
    ) * 3
    assert (snapshot.cofins.base, snapshot.cofins.rate, snapshot.cofins.amount) == (
        Decimal("0.00"),
    ) * 3
