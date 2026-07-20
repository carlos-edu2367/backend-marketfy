import os
import sys
from decimal import Decimal

import pytest


current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.fiscal.snapshot_integrity import (  # noqa: E402
    CALCULATION_VERSION,
    canonical_fiscal_snapshot_json,
    canonical_json,
    canonical_sha256,
    fiscal_snapshot_sha256,
)


def test_v2_calculation_version_is_public() -> None:
    assert CALCULATION_VERSION == "marketfy-tax-calc.v2"


def test_canonical_hash_is_key_order_independent() -> None:
    assert canonical_sha256({"b": "2.00", "a": 1}) == canonical_sha256(
        {"a": 1, "b": "2.00"}
    )


def test_canonical_json_preserves_decimal_scale_and_utf8() -> None:
    assert canonical_json(
        {"rate": Decimal("18.0000"), "amount": Decimal("2.00"), "label": "ação"}
    ) == '{"amount":"2.00","label":"ação","rate":"18.0000"}'


@pytest.mark.parametrize(
    "snapshot",
    [
        {"float": 0.1},
        {"nested": {"float": 0.1}},
        {"nested": [{"float": 0.1}]},
        {"nested": ({"float": 0.1},)},
    ],
)
def test_canonical_json_rejects_binary_floats_recursively(snapshot) -> None:
    with pytest.raises(TypeError, match="float não permitido"):
        canonical_json(snapshot)


def test_legacy_decimal_and_string_snapshots_remain_verifiable() -> None:
    snapshot = {
        "icms": {"own_amount": Decimal("1.005"), "own_rate": Decimal("18.0000")},
        "persisted": "2.00",
    }

    legacy_json = canonical_fiscal_snapshot_json(snapshot)

    assert legacy_json == (
        '{"icms":{"own_amount":"1.01","own_rate":"18.0000"},'
        '"persisted":"2.00"}'
    )
    assert fiscal_snapshot_sha256(snapshot) == (
        "56ebe494284660d4d6dd9e6cadfd9dfa49b538d3df4c0aa8590c041532964550"
    )
