"""Marketfy's strict, versioned item-tax contract sent to Fiscal."""
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

import pytest


current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.fiscal.fiscal_pre_validator import FiscalPreValidator
from application.services.fiscal.snapshot_integrity import (
    CALCULATION_VERSION,
    fiscal_snapshot_sha256,
)
from domain.shared import BusinessRuleException


@dataclass
class FakePayment:
    method: str = "pix"
    amount: Decimal = Decimal("110.00")


@dataclass
class FakeConfig:
    environment: str = "producao"
    crt: str = "1"
    # These deliberately unsafe legacy values must never be read by the
    # item-tax contract builder.
    default_ncm: str = "00000000"
    default_cfop: str = "5102"
    default_csosn: str = "102"
    default_cst: str = "40"


@dataclass
class FakeItem:
    product_id: uuid.UUID = field(default_factory=uuid.uuid4)
    product_name: str = "Refrigerante ST"
    quantity: Decimal = Decimal("2.5000")
    unit_price: Decimal = Decimal("40.00")
    total: Decimal = Decimal("100.00")
    tax_rule_version_snapshot: int | None = 7
    fiscal_tax_snapshot: dict | None = field(default_factory=lambda: {
        "rule_id": "db5a759c-13b2-4ca6-92a5-7b7eeaebfcfd",
        "rule_version": 7,
        "ncm": "22021000",
        "cest": "0300700",
        "cfop": "5405",
        "origin": "0",
        "icms": {
            "group": "ICMSSN500",
            "cst": None,
            "csosn": "500",
            "own_base": Decimal("100.00"),
            "reduction_rate": Decimal("0.00"),
            "own_rate": Decimal("18.00"),
            "own_amount": Decimal("18.00"),
            "st_base": Decimal("140.00"),
            "st_mva_rate": Decimal("40.00"),
            "st_rate": Decimal("18.00"),
            "st_amount": Decimal("7.20"),
            "fcp_rate": Decimal("0.00"),
            "fcp_amount": Decimal("0.00"),
        },
        "pis": {
            "group": "PIS07", "cst": "07", "base": Decimal("100.00"),
            "rate": Decimal("0.00"), "amount": Decimal("0.00"),
        },
        "cofins": {
            "group": "COFINS07", "cst": "07", "base": Decimal("100.00"),
            "rate": Decimal("0.00"), "amount": Decimal("0.00"),
        },
    })


@dataclass
class FakeSale:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    market_id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime(2026, 7, 15, 16, 30, tzinfo=timezone.utc))
    items: list[FakeItem] = field(default_factory=lambda: [FakeItem()])
    payments: list[FakePayment] = field(default_factory=lambda: [FakePayment()])
    customer_cpf: str | None = None


def _build(sale: FakeSale) -> dict:
    return FiscalPreValidator().build_neectify_payload(
        sale=sale,
        fiscal_config=FakeConfig(),
        issuer_id="iss_1",
        provider_ref="marketfy-provider-ref",
    )


def test_neectify_payload_contains_immutable_st_item_tax_snapshot() -> None:
    payload = _build(FakeSale())

    item = payload["items"][0]
    assert payload["contract_version"] == "marketfy.fiscal-tax-snapshot.v1"
    assert payload["correlation"]["sale_id"] == payload["external_id"]
    assert payload["correlation"]["provider_ref"] == "marketfy-provider-ref"
    assert item["cest"] == "0300700"
    assert item["tax"]["cest"] == "0300700"
    assert item["tax"]["rule_version"] == 7
    assert item["tax"]["icms"]["group"] == "ICMSSN500"
    assert item["tax"]["icms"]["st_amount"] == "7.20"


def test_neectify_payload_serializes_non_st_tax_decimals_canonically() -> None:
    sale = FakeSale()
    item = sale.items[0]
    item.product_name = "Produto normal"
    item.fiscal_tax_snapshot["cest"] = None
    item.fiscal_tax_snapshot["cfop"] = "5102"
    item.fiscal_tax_snapshot["icms"].update({
            "group": "ICMSSN102", "csosn": "102", "own_base": Decimal("100"), "own_rate": Decimal("0"),
        "own_amount": Decimal("0"), "st_base": Decimal("0"), "st_rate": Decimal("0"),
        "st_amount": Decimal("0"),
    })

    payload = _build(sale)
    tax = payload["items"][0]["tax"]

    assert tax["cest"] is None
    assert tax["icms"]["own_base"] == "100.00"
    assert tax["icms"]["st_amount"] == "0.00"
    assert tax["pis"]["rate"] == "0.00"
    assert all(not isinstance(value, Decimal) for value in tax["icms"].values())


def test_neectify_payload_never_falls_back_to_global_defaults() -> None:
    sale = FakeSale()
    sale.items[0].fiscal_tax_snapshot["ncm"] = "27101932"
    sale.items[0].fiscal_tax_snapshot["cfop"] = "5405"

    payload = _build(sale)

    assert payload["items"][0]["ncm"] == "27101932"
    assert payload["items"][0]["cfop"] == "5405"
    assert "00000000" not in repr(payload)
    assert "5102" not in repr(payload)


def test_neectify_payload_blocks_missing_snapshot_with_sku_error() -> None:
    sale = FakeSale()
    sale.items[0].fiscal_tax_snapshot = None

    with pytest.raises(BusinessRuleException, match=r"sale\.fiscal_tax_snapshot_missing") as exc_info:
        _build(sale)

    assert str(sale.items[0].product_id) in str(exc_info.value)


def test_neectify_payload_rejects_unreconciled_item_totals() -> None:
    sale = FakeSale()
    sale.items[0].fiscal_tax_snapshot["icms"]["own_base"] = Decimal("99.99")

    with pytest.raises(BusinessRuleException, match=r"fiscal\.snapshot_amount_mismatch"):
        _build(sale)


@pytest.mark.parametrize(
    ("section", "field"),
    [("icms", "own_base"), ("pis", "base"), ("cofins", "base")],
)
def test_v1_payload_rejects_nonzero_values_for_non_taxed_groups(
    section: str, field: str
) -> None:
    sale = FakeSale()
    item = sale.items[0]
    assert item.fiscal_tax_snapshot is not None
    item.product_name = "Produto normal"
    item.fiscal_tax_snapshot["cest"] = None
    item.fiscal_tax_snapshot["cfop"] = "5102"
    item.fiscal_tax_snapshot["icms"].update(
        {
            "group": "ICMSSN102",
            "csosn": "102",
            "own_base": Decimal("100.00"),
            "own_rate": Decimal("0.00"),
            "own_amount": Decimal("0.00"),
            "st_base": Decimal("0.00"),
            "st_rate": Decimal("0.00"),
            "st_amount": Decimal("0.00"),
            "fcp_amount": Decimal("0.00"),
        }
    )
    item.fiscal_tax_snapshot["pis"].update(
        {"base": Decimal("100.00"), "rate": Decimal("0.00"), "amount": Decimal("0.00")}
    )
    item.fiscal_tax_snapshot["cofins"].update(
        {"base": Decimal("100.00"), "rate": Decimal("0.00"), "amount": Decimal("0.00")}
    )
    item.fiscal_tax_snapshot[section][field] = Decimal("100.00")
    item.fiscal_calculation_version = CALCULATION_VERSION
    item.snapshot_sha256 = fiscal_snapshot_sha256(item.fiscal_tax_snapshot)

    with pytest.raises(BusinessRuleException, match=r"fiscal\.snapshot_amount_mismatch"):
        _build(sale)
