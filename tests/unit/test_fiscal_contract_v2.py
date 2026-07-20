"""Immutable outbound contract built from completed-sale fiscal evidence."""
from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from application.services.fiscal.snapshot_integrity import CALCULATION_VERSION, canonical_sha256
from application.services.fiscal.tax_rule_calculator import TaxRuleCalculator
from domain.fiscal import ProductTaxRule, ProductTaxRuleStatus
from domain.sales import SaleItem


@dataclass
class Payment:
    method: str
    amount: Decimal


@dataclass
class Item:
    product_id: uuid.UUID
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal
    fiscal_tax_snapshot: dict
    snapshot_sha256: str
    fiscal_calculation_version: str = CALCULATION_VERSION


@dataclass
class Sale:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    market_id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime(2026, 7, 15, tzinfo=UTC))
    items: list[Item] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=lambda: [Payment("pix", Decimal("30.00"))])
    customer_cpf: str | None = None


@dataclass
class Config:
    environment: str = "homologacao"
    default_cfop: str = "9999"  # serializer must never read mutable defaults


def _snapshot(*, group: str, cfop: str, cest: str | None, amount: str) -> dict:
    retained = group == "ICMSSN500"
    snapshot = {
        "rule_id": str(uuid.uuid4()), "rule_version": 1,
        "calculation_version": CALCULATION_VERSION,
        "ncm": "22021000", "cest": cest, "origin": "0", "cfop": cfop,
        "cbenef": None, "approval_ref": "go-in-042-2026",
        "icms": {
            "mode": "retained_st" if retained else "non_taxed", "group": group,
            "cst": None, "csosn": "500" if retained else "102",
            "own_base": "0.00", "own_rate": "0.0000", "own_amount": "0.00",
            "current_st_base": "0.00", "current_st_rate": "0.0000", "current_st_amount": "0.00",
            "retained_st_base": "42.00" if retained else None,
            "retained_st_rate": "18.0000" if retained else None,
            "retained_st_amount": "7.56" if retained else None,
            "retained_fcp_base": None, "retained_fcp_rate": None, "retained_fcp_amount": None,
        },
        "pis": {"group": "PIS07", "cst": "07", "base": "0.00", "rate": "0.0000", "amount": "0.00"},
        "cofins": {"group": "COFINS07", "cst": "07", "base": "0.00", "rate": "0.0000", "amount": "0.00"},
        "audit_input": amount,
    }
    if retained:
        snapshot.update(
            catalog_version="go-nfce-v2.1",
            approval_checksum="a" * 64,
        )
    return snapshot


def _item(*, group: str, cfop: str, cest: str | None, amount: str) -> Item:
    snapshot = _snapshot(group=group, cfop=cfop, cest=cest, amount=amount)
    return Item(
        product_id=uuid.uuid4(), product_name=group, quantity=Decimal("1"),
        unit_price=Decimal(amount), total=Decimal(amount),
        fiscal_tax_snapshot=snapshot, snapshot_sha256=canonical_sha256(snapshot),
    )


def test_v2_payload_uses_only_sale_item_snapshots() -> None:
    from application.services.fiscal.fiscal_contract_v2 import (
        FiscalContractV2Serializer, canonical_contract_sha256,
    )

    mixed_sale = Sale(items=[
        _item(group="ICMSSN102", cfop="5102", cest=None, amount="10.00"),
        _item(group="ICMSSN500", cfop="5405", cest="0300700", amount="20.00"),
    ])
    payload = FiscalContractV2Serializer().build(mixed_sale, Config(), "iss_1", "marketfy-ref")

    assert payload["contract_version"] == "marketfy.fiscal-tax-snapshot.v2"
    assert payload["items"][0]["tax"]["icms"]["group"] == "ICMSSN102"
    assert payload["items"][1]["tax"]["icms"]["group"] == "ICMSSN500"
    assert payload["items"][1]["cfop"] == "5405"
    assert payload["totals"]["current_st_amount"] == "0.00"
    assert payload["snapshot_sha256"] == canonical_contract_sha256(payload)
    assert all(item["cfop"] != Config().default_cfop for item in payload["items"])


def test_v2_contract_rejects_tampered_sale_snapshot() -> None:
    from domain.shared import BusinessRuleException
    from application.services.fiscal.fiscal_contract_v2 import FiscalContractV2Serializer

    item = _item(group="ICMSSN102", cfop="5102", cest=None, amount="10.00")
    item.fiscal_tax_snapshot["cfop"] = "5405"

    try:
        FiscalContractV2Serializer().build(Sale(items=[item]), Config(), "iss_1", "ref")
    except BusinessRuleException as exc:
        assert "snapshot_sha256" in str(exc)
    else:
        raise AssertionError("serializer accepted a snapshot changed after sale finalisation")


def test_retained_st_contract_carries_immutable_catalog_and_approval_evidence() -> None:
    from application.services.fiscal.fiscal_contract_v2 import FiscalContractV2Serializer

    item = _item(group="ICMSSN500", cfop="5405", cest="0300700", amount="20.00")
    item.fiscal_tax_snapshot.update(
        catalog_version="go-nfce-v2.1",
        approval_checksum="a" * 64,
    )
    item.snapshot_sha256 = canonical_sha256(item.fiscal_tax_snapshot)

    payload = FiscalContractV2Serializer().build(Sale(items=[item]), Config(), "iss_1", "ref")

    tax = payload["items"][0]["tax"]
    assert tax["rule_id"] == item.fiscal_tax_snapshot["rule_id"]
    assert tax["rule_version"] == 1
    assert tax["catalog_version"] == "go-nfce-v2.1"
    assert tax["approval_ref"] == "go-in-042-2026"
    assert tax["approval_checksum"] == "a" * 64


@pytest.mark.parametrize("missing_field", ["catalog_version", "approval_checksum"])
def test_retained_st_contract_rejects_missing_catalog_evidence(missing_field: str) -> None:
    from domain.shared import BusinessRuleException
    from application.services.fiscal.fiscal_contract_v2 import FiscalContractV2Serializer

    item = _item(group="ICMSSN500", cfop="5405", cest="0300700", amount="20.00")
    item.fiscal_tax_snapshot.update(
        catalog_version="go-nfce-v2.1",
        approval_checksum="a" * 64,
    )
    item.fiscal_tax_snapshot.pop(missing_field)
    item.snapshot_sha256 = canonical_sha256(item.fiscal_tax_snapshot)

    with pytest.raises(BusinessRuleException, match=missing_field):
        FiscalContractV2Serializer().build(Sale(items=[item]), Config(), "iss_1", "ref")


def test_retained_st_sale_keeps_published_catalog_evidence_after_successor_change() -> None:
    from application.services.fiscal.fiscal_contract_v2 import FiscalContractV2Serializer

    rule = ProductTaxRule(
        market_id=uuid.uuid4(), name="Bebida ST", status=ProductTaxRuleStatus.PUBLISHED,
        effective_from=date(2026, 7, 1), ncm="22021000", cest="0300700",
        origin="0", cfop="5405", icms_group="ICMSSN500", icms_csosn="500",
        tax_parameters={
            "icms_mode": "retained_st", "retained_st_base": "42.00",
            "retained_st_rate": "18.0000", "retained_st_amount": "7.56",
            "pis": {"group": "PIS07", "cst": "07", "base": "0.00", "rate": "0.0000", "amount": "0.00"},
            "cofins": {"group": "COFINS07", "cst": "07", "base": "0.00", "rate": "0.0000", "amount": "0.00"},
        },
        approval={
            "reference": "Decreto GO 10.734/2025, Anexo V-B",
            "checksum": "a" * 64,
            "catalog_version": "go-nfce-v2.1",
        },
    )
    fiscal_snapshot = TaxRuleCalculator().calculate(
        item=SaleItem(
            sale_id=uuid.uuid4(), product_id=uuid.uuid4(), product_name="Bebida ST",
            quantity=Decimal("1"), unit_price=Decimal("20.00"), total=Decimal("20.00"),
        ),
        rule=rule,
    ).as_persistence_dict()
    sale_item = Item(
        product_id=uuid.uuid4(), product_name="Bebida ST", quantity=Decimal("1"),
        unit_price=Decimal("20.00"), total=Decimal("20.00"),
        fiscal_tax_snapshot=fiscal_snapshot,
        snapshot_sha256=canonical_sha256(fiscal_snapshot),
    )

    successor = rule.create_successor(effective_from=date(2026, 8, 1))
    successor.approval["catalog_version"] = "go-nfce-v2.2"
    successor.approval["checksum"] = "b" * 64

    tax = FiscalContractV2Serializer().build(
        Sale(items=[sale_item]), Config(), "iss_1", "ref"
    )["items"][0]["tax"]

    assert tax["rule_id"] == str(rule.id)
    assert tax["rule_version"] == rule.version
    assert tax["catalog_version"] == "go-nfce-v2.1"
    assert tax["approval_ref"] == "Decreto GO 10.734/2025, Anexo V-B"
    assert tax["approval_checksum"] == "a" * 64
