"""Public import boundary for the Marketfy -> Fiscal v2 contract."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from application.services.fiscal.fiscal_contract_v2 import FiscalContractV2Serializer
from application.services.fiscal.snapshot_integrity import canonical_sha256
from application.services.fiscal.tax_rule_calculator import TaxRuleCalculator
from domain.fiscal_tax import ProductTaxRule


def test_v2_contract_public_interfaces_are_importable() -> None:
    assert FiscalContractV2Serializer.contract_version == "marketfy.fiscal-tax-snapshot.v2"
    assert callable(canonical_sha256)
    assert TaxRuleCalculator is not None
    assert ProductTaxRule is not None
