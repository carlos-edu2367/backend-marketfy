"""Characterization of the pre-contract payload required by off/warn rollout modes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from application.services.fiscal.fiscal_pre_validator import FiscalPreValidator


@dataclass
class LegacyPayment:
    method: str = "pix"
    amount: Decimal = Decimal("10.00")


@dataclass
class LegacyItem:
    product_id: uuid.UUID = field(default_factory=uuid.uuid4)
    product_name: str = "Agua mineral"
    ncm_snapshot: str = "22021000"
    unit_price: Decimal = Decimal("10.00")
    quantity: Decimal = Decimal("1.00")
    total: Decimal = Decimal("10.00")
    tax_rule_version_snapshot: int = 1
    fiscal_tax_snapshot: dict = field(default_factory=dict)


@dataclass
class LegacySale:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime(2026, 7, 15, tzinfo=UTC))
    items: list[LegacyItem] = field(default_factory=list)
    payments: list[LegacyPayment] = field(default_factory=lambda: [LegacyPayment()])
    customer_cpf: str | None = None


@dataclass
class LegacyFiscalConfig:
    default_cfop: str = "5102"
    environment: str = "homologacao"
    crt: str = "1"


def _legacy_snapshot(*, ncm: str, cfop: str) -> dict:
    return {
        "rule_id": str(uuid.uuid4()),
        "rule_version": 1,
        "ncm": ncm,
        "cest": None,
        "cfop": cfop,
        "origin": "0",
        "icms": {
            "group": "ICMSSN102",
            "cst": None,
            "csosn": "102",
            "own_base": Decimal("10.00"),
            "reduction_rate": Decimal("0.00"),
            "own_rate": Decimal("0.00"),
            "own_amount": Decimal("0.00"),
            "st_base": Decimal("0.00"),
            "st_mva_rate": Decimal("0.00"),
            "st_rate": Decimal("0.00"),
            "st_amount": Decimal("0.00"),
            "fcp_rate": Decimal("0.00"),
            "fcp_amount": Decimal("0.00"),
        },
        "pis": {
            "group": "PIS07",
            "cst": "07",
            "base": Decimal("10.00"),
            "rate": Decimal("0.00"),
            "amount": Decimal("0.00"),
        },
        "cofins": {
            "group": "COFINS07",
            "cst": "07",
            "base": Decimal("10.00"),
            "rate": Decimal("0.00"),
            "amount": Decimal("0.00"),
        },
    }


@pytest.fixture
def make_sale():
    def factory(*, ncm: str) -> LegacySale:
        item = LegacyItem(
            ncm_snapshot=ncm,
            fiscal_tax_snapshot=_legacy_snapshot(ncm=ncm, cfop="5102"),
        )
        return LegacySale(items=[item])

    return factory


@pytest.fixture
def make_fiscal_config():
    def factory(*, default_cfop: str) -> LegacyFiscalConfig:
        return LegacyFiscalConfig(default_cfop=default_cfop)

    return factory


def test_off_warn_legacy_neectify_payload_keeps_current_shape(
    make_sale, make_fiscal_config
) -> None:
    payload = FiscalPreValidator().build_legacy_neectify_payload(
        make_sale(ncm="22021000"), make_fiscal_config(default_cfop="5102"), "iss_1"
    )
    assert "contract_version" not in payload
    assert payload["items"][0]["ncm"] == "22021000"
    assert payload["items"][0]["cfop"] == "5102"
