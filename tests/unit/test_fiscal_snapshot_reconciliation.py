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
from domain.shared import BusinessRuleException


def tax_snapshot():
    return {
        "rule_id": str(uuid.uuid4()), "rule_version": 1, "ncm": "22021000", "cest": "0300700", "cfop": "5405", "origin": "0",
        "icms": {"group": "ICMSSN500", "cst": None, "csosn": "500", "own_base": "10.00", "reduction_rate": "0.00", "own_rate": "18.00", "own_amount": "1.80", "st_base": "14.00", "st_mva_rate": "40.00", "st_rate": "18.00", "st_amount": "0.72", "fcp_rate": "2.00", "fcp_amount": "0.28"},
        "pis": {"group": "PIS01", "cst": "01", "base": "10.00", "rate": "1.65", "amount": "0.17"},
        "cofins": {"group": "COFINS01", "cst": "01", "base": "10.00", "rate": "7.60", "amount": "0.76"},
    }


@dataclass
class Item:
    product_id: uuid.UUID = field(default_factory=uuid.uuid4)
    quantity: Decimal = Decimal("1")
    unit_price: Decimal = Decimal("10.00")
    total: Decimal = Decimal("10.00")
    tax_rule_version_snapshot: int = 1
    fiscal_tax_snapshot: dict = field(default_factory=tax_snapshot)
    snapshot_sha256: str | None = None
    fiscal_calculation_version: str | None = "marketfy.fiscal-tax-calculation.v1"


@dataclass
class Sale:
    items: list
    payments: list = field(default_factory=list)
    total_amount: Decimal = Decimal("10.00")
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class Config:
    crt = "1"


def signed_item():
    item = Item()
    item.snapshot_sha256 = FiscalPreValidator.fiscal_snapshot_sha256(item.fiscal_tax_snapshot)
    return item


def test_snapshot_hash_tampering_blocks_emission():
    item = signed_item()
    item.fiscal_tax_snapshot["icms"]["st_amount"] = "0.71"

    with pytest.raises(BusinessRuleException, match=r"sale\.fiscal_tax_snapshot_invalid.*snapshot_sha256"):
        FiscalPreValidator().build_neectify_payload(Sale([item]), Config(), "issuer")


def test_st_fcp_pis_and_cofins_amount_mismatch_blocks_emission():
    for section, field in (("icms", "st_amount"), ("icms", "fcp_amount"), ("pis", "amount"), ("cofins", "amount")):
        item = signed_item()
        item.fiscal_tax_snapshot[section][field] = "0.01"
        item.snapshot_sha256 = FiscalPreValidator.fiscal_snapshot_sha256(item.fiscal_tax_snapshot)

        with pytest.raises(BusinessRuleException, match=r"fiscal\.snapshot_amount_mismatch"):
            FiscalPreValidator().build_neectify_payload(Sale([item]), Config(), "issuer")
