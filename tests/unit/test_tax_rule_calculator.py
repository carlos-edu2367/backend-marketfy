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

from application.services.fiscal.snapshot_integrity import (  # noqa: E402
    CALCULATION_VERSION,
)
from application.services.fiscal.tax_rule_calculator import (  # noqa: E402
    TaxRuleCalculator,
)
from domain.fiscal import ProductTaxRule, ProductTaxRuleStatus  # noqa: E402
from domain.sales import SaleItem  # noqa: E402
from domain.shared import BusinessRuleException  # noqa: E402


def make_contribution(group: str, cst: str) -> dict[str, str]:
    return {
        "group": group,
        "cst": cst,
        "base": "0.00",
        "rate": "0.0000",
        "amount": "0.00",
    }


def make_retained_st_rule(**overrides) -> ProductTaxRule:
    values = {
        "market_id": uuid.uuid4(),
        "name": "Refrigerante ST",
        "status": ProductTaxRuleStatus.PUBLISHED,
        "effective_from": date(2026, 7, 1),
        "ncm": "22021000",
        "cest": "0300700",
        "origin": "0",
        "cfop": "5405",
        "cbenef": None,
        "icms_group": "ICMSSN500",
        "icms_cst": None,
        "icms_csosn": "500",
        # Deliberately populated legacy columns must not create retained or current ST.
        "icms_rate": Decimal("18.00"),
        "icms_st_mva_rate": Decimal("40.00"),
        "icms_st_rate": Decimal("18.00"),
        "fcp_rate": Decimal("2.00"),
        "pis_cst": "07",
        "pis_rate": Decimal("0.00"),
        "cofins_cst": "07",
        "cofins_rate": Decimal("0.00"),
        "tax_parameters": {
            "icms_mode": "retained_st",
            "retained_st_base": "140.00",
            "retained_st_rate": "18.0000",
            "retained_st_amount": "25.20",
            "pis": make_contribution("PIS07", "07"),
            "cofins": make_contribution("COFINS07", "07"),
        },
        "approval": {
            "reference": "CRC-GO-2026-0001",
            "checksum": "a" * 64,
            "catalog_version": "go-nfce-v2.1",
        },
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


def test_icmssn500_retained_values_do_not_become_current_st_totals() -> None:
    snapshot = TaxRuleCalculator().calculate(
        item=make_item(),
        rule=make_retained_st_rule(),
    )

    # Compatibility aliases also expose only current-operation ST, never MVA-derived ST.
    assert snapshot.icms.st_amount == Decimal("0.00")
    assert snapshot["icms"]["retained_st_base"] == "140.00"
    assert snapshot["icms"]["retained_st_rate"] == "18.0000"
    assert snapshot["icms"]["retained_st_amount"] == "25.20"
    assert snapshot["icms"]["current_st_base"] == "0.00"
    assert snapshot["icms"]["current_st_rate"] == "0.0000"
    assert snapshot["icms"]["current_st_amount"] == "0.00"


@pytest.mark.parametrize(
    ("group", "cst", "csosn"),
    [("ICMSSN500", None, "500"), ("ICMS60", "60", None)],
)
def test_retained_st_groups_always_have_zero_current_operation_st(
    group: str, cst: str | None, csosn: str | None
) -> None:
    snapshot = TaxRuleCalculator().calculate(
        item=make_item("999.99"),
        rule=make_retained_st_rule(
            icms_group=group,
            icms_cst=cst,
            icms_csosn=csosn,
        ),
    )

    assert snapshot["icms"]["group"] == group
    assert snapshot["icms"]["current_st_base"] == "0.00"
    assert snapshot["icms"]["current_st_rate"] == "0.0000"
    assert snapshot["icms"]["current_st_amount"] == "0.00"


def test_missing_retained_evidence_remains_null_despite_legacy_st_columns() -> None:
    tax_parameters = {
        "icms_mode": "retained_st",
        "pis": make_contribution("PIS07", "07"),
        "cofins": make_contribution("COFINS07", "07"),
    }

    snapshot = TaxRuleCalculator().calculate(
        item=make_item("250.00"),
        rule=make_retained_st_rule(tax_parameters=tax_parameters),
    )

    for field in (
        "retained_st_base",
        "retained_st_rate",
        "retained_st_amount",
        "retained_fcp_base",
        "retained_fcp_rate",
        "retained_fcp_amount",
    ):
        assert snapshot["icms"][field] is None


def test_explicit_decimal_evidence_rounds_half_up_at_contract_scale() -> None:
    tax_parameters = {
        "icms_mode": "retained_st",
        "retained_st_base": Decimal("140.005"),
        "retained_st_rate": Decimal("18.00005"),
        "retained_st_amount": Decimal("25.205"),
        "retained_fcp_base": Decimal("140.005"),
        "retained_fcp_rate": Decimal("2.00005"),
        "retained_fcp_amount": Decimal("2.805"),
        "pis": make_contribution("PIS07", "07"),
        "cofins": make_contribution("COFINS07", "07"),
    }

    snapshot = TaxRuleCalculator().calculate(
        item=make_item(),
        rule=make_retained_st_rule(tax_parameters=tax_parameters),
    )

    assert snapshot["icms"]["retained_st_base"] == "140.01"
    assert snapshot["icms"]["retained_st_rate"] == "18.0001"
    assert snapshot["icms"]["retained_st_amount"] == "25.21"
    assert snapshot["icms"]["retained_fcp_base"] == "140.01"
    assert snapshot["icms"]["retained_fcp_rate"] == "2.0001"
    assert snapshot["icms"]["retained_fcp_amount"] == "2.81"


@pytest.mark.parametrize(
    "group",
    ["ICMSSN201", "ICMS10", "ICMS30", "ICMS70", "ICMS90"],
)
def test_current_operation_st_groups_fail_closed(group: str) -> None:
    with pytest.raises(BusinessRuleException, match="ST da operação atual"):
        TaxRuleCalculator().calculate(
            item=make_item(),
            rule=make_retained_st_rule(icms_group=group),
        )


@pytest.mark.parametrize(
    ("group", "cst", "csosn"),
    [("ICMSSN102", None, "102"), ("ICMS40", "40", None)],
)
def test_non_taxed_groups_keep_all_current_values_zero(
    group: str, cst: str | None, csosn: str | None
) -> None:
    tax_parameters = {
        "icms_mode": "non_taxed",
        "pis": make_contribution("PIS07", "07"),
        "cofins": make_contribution("COFINS07", "07"),
    }
    snapshot = TaxRuleCalculator().calculate(
        item=make_item(),
        rule=make_retained_st_rule(
            icms_group=group,
            icms_cst=cst,
            icms_csosn=csosn,
            tax_parameters=tax_parameters,
        ),
    )

    assert snapshot["icms"]["own_base"] == "0.00"
    assert snapshot["icms"]["own_rate"] == "0.0000"
    assert snapshot["icms"]["own_amount"] == "0.00"
    assert snapshot["icms"]["current_st_base"] == "0.00"
    assert snapshot["icms"]["current_st_amount"] == "0.00"


def test_snapshot_copies_only_explicit_rule_classification_and_evidence() -> None:
    rule = make_retained_st_rule(cest=None, cbenef="GO123")

    snapshot = TaxRuleCalculator().calculate(item=make_item(), rule=rule)

    assert snapshot["rule_id"] == str(rule.id)
    assert snapshot["rule_version"] == rule.version
    assert snapshot["calculation_version"] == CALCULATION_VERSION
    assert snapshot["ncm"] == "22021000"
    assert snapshot["cest"] is None
    assert snapshot["origin"] == "0"
    assert snapshot["cfop"] == "5405"
    assert snapshot["cbenef"] == "GO123"
    assert snapshot["approval_ref"] == "CRC-GO-2026-0001"
    assert snapshot["catalog_version"] == "go-nfce-v2.1"
    assert snapshot["approval_checksum"] == "a" * 64
    assert snapshot["pis"] == make_contribution("PIS07", "07")
    assert snapshot["cofins"] == make_contribution("COFINS07", "07")


def test_persistence_compatibility_aliases_cannot_restore_old_st_semantics() -> None:
    persisted = TaxRuleCalculator().calculate(
        item=make_item(), rule=make_retained_st_rule()
    ).as_persistence_dict()

    assert persisted["icms"]["st_base"] == Decimal("0.00")
    assert persisted["icms"]["st_mva_rate"] == Decimal("0.0000")
    assert persisted["icms"]["st_rate"] == Decimal("0.0000")
    assert persisted["icms"]["st_amount"] == Decimal("0.00")
    assert persisted["icms"]["current_st_amount"] == "0.00"
    assert persisted["icms"]["retained_st_amount"] == "25.20"
