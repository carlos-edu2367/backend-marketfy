"""Canonical immutable fiscal snapshot representation and integrity helpers."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping


CALCULATION_VERSION = "marketfy.fiscal-tax-calculation.v1"
_MONEY = Decimal("0.01")


def _normalise(value: Any) -> Any:
    if isinstance(value, Decimal):
        return f"{value.quantize(_MONEY, rounding=ROUND_HALF_UP):.2f}"
    if isinstance(value, Mapping):
        return {str(key): _normalise(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    return value


def canonical_fiscal_snapshot_json(snapshot: Mapping[str, Any]) -> str:
    """JSON bytes stable across Decimal persistence/reload boundaries."""
    return json.dumps(_normalise(snapshot), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def fiscal_snapshot_sha256(snapshot: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_fiscal_snapshot_json(snapshot).encode("utf-8")).hexdigest()
