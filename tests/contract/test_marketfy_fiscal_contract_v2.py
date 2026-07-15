"""Public import boundary for the Marketfy -> Fiscal v2 contract."""

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))


@pytest.mark.parametrize(
    ("module_name", "symbol_name"),
    [
        pytest.param(
            "application.services.fiscal.fiscal_contract_v2",
            "FiscalContractV2Serializer",
            id="fiscal-contract-v2-serializer",
        ),
        pytest.param(
            "application.services.fiscal.snapshot_integrity",
            "canonical_sha256",
            id="canonical-sha256",
        ),
        pytest.param(
            "application.services.fiscal.tax_rule_calculator",
            "TaxRuleCalculator",
            id="tax-rule-calculator",
        ),
        pytest.param(
            "domain.fiscal_tax",
            "ProductTaxRule",
            id="product-tax-rule",
        ),
    ],
)
def test_v2_contract_public_interface_is_importable(
    module_name: str, symbol_name: str
) -> None:
    module = importlib.import_module(module_name)
    symbol = getattr(module, symbol_name)

    if symbol_name == "FiscalContractV2Serializer":
        assert symbol.contract_version == "marketfy.fiscal-tax-snapshot.v2"
    elif symbol_name == "canonical_sha256":
        assert callable(symbol)
    else:
        assert symbol is not None
