"""Canonical immutable fiscal snapshot representation and integrity helpers."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping


CALCULATION_VERSION = "marketfy-tax-calc.v2"
LEGACY_CALCULATION_VERSION = "marketfy.fiscal-tax-calculation.v1"
_MONEY = Decimal("0.01")
_LEGACY_RATE_FIELDS = {
    "reduction_rate",
    "own_rate",
    "st_mva_rate",
    "st_rate",
    "fcp_rate",
    "rate",
}


def _normalize_canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        raise TypeError("float não permitido em snapshot fiscal")
    if isinstance(value, Mapping):
        normalized = {}
        for key in sorted(value, key=str):
            if isinstance(key, float):
                raise TypeError("float não permitido em snapshot fiscal")
            normalized[str(key)] = _normalize_canonical(value[key])
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_canonical(item) for item in value]
    return value


def canonical_json(value: Mapping[str, Any]) -> str:
    """Serialize v2 evidence deterministically without accepting binary floats."""
    return json.dumps(
        _normalize_canonical(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _normalize_legacy(value: Any, path: tuple[str, ...] = ()) -> Any:
    """Preserve the v1 normalization used by persisted retry evidence."""
    if isinstance(value, Decimal):
        if path and path[-1] in _LEGACY_RATE_FIELDS:
            return format(value, "f")
        return f"{value.quantize(_MONEY, rounding=ROUND_HALF_UP):.2f}"
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_legacy(value[key], path + (str(key),))
            for key in sorted(value, key=str)
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_legacy(item, path) for item in value]
    return value


def canonical_fiscal_snapshot_json(snapshot: Mapping[str, Any]) -> str:
    """Serialize valid v1 Decimal/string snapshots exactly as before v2."""
    return json.dumps(
        _normalize_legacy(snapshot),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def fiscal_snapshot_sha256(snapshot: Mapping[str, Any]) -> str:
    """Return the legacy v1 digest used by retries and historical validation."""
    return hashlib.sha256(
        canonical_fiscal_snapshot_json(snapshot).encode("utf-8")
    ).hexdigest()
