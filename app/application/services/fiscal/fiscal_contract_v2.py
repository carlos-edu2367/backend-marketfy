"""Version 2 outbound NFC-e contract built exclusively from sale evidence."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from application.services.fiscal.snapshot_integrity import (
    CALCULATION_VERSION,
    canonical_json,
    canonical_sha256,
)
from domain.shared import BusinessRuleException


CONTRACT_VERSION = "marketfy.fiscal-tax-snapshot.v2"
_MONEY = Decimal("0.01")
_RATE = Decimal("0.0001")
_RETAINED_ST = frozenset({"ICMSSN500", "ICMS60"})


def canonical_contract_sha256(payload: Mapping[str, Any]) -> str:
    """Digest the transmitted contract, excluding its self-referential hash."""
    body = dict(payload)
    body.pop("snapshot_sha256", None)
    return canonical_sha256(body)


def _money(value: Any, *, field: str) -> str:
    if isinstance(value, (float, bool)):
        raise BusinessRuleException(f"Contrato fiscal v2 inválido: {field} deve ser decimal exato.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BusinessRuleException(f"Contrato fiscal v2 inválido: {field} não é decimal.") from exc
    if not result.is_finite():
        raise BusinessRuleException(f"Contrato fiscal v2 inválido: {field} não é decimal.")
    return f"{result.quantize(_MONEY, rounding=ROUND_HALF_UP):.2f}"


def _rate(value: Any, *, field: str) -> str:
    if isinstance(value, (float, bool)):
        raise BusinessRuleException(f"Contrato fiscal v2 inválido: {field} deve ser decimal exato.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BusinessRuleException(f"Contrato fiscal v2 inválido: {field} não é decimal.") from exc
    if not result.is_finite():
        raise BusinessRuleException(f"Contrato fiscal v2 inválido: {field} não é decimal.")
    return f"{result.quantize(_RATE, rounding=ROUND_HALF_UP):.4f}"


def _json_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    """Detach immutable/mapping-proxy evidence and forbid binary floats."""
    try:
        return json.loads(canonical_json(value))
    except TypeError as exc:
        raise BusinessRuleException("Contrato fiscal v2 inválido: snapshot contém float.") from exc


class FiscalContractV2Serializer:
    """Creates the exact request that is persisted before queueing it."""

    def build(self, sale, config, issuer_id: str, provider_ref: str) -> dict[str, Any]:
        items = [self._item(item) for item in sale.items]
        if not items:
            raise BusinessRuleException("Contrato fiscal v2 inválido: venda sem itens.")

        environment = getattr(config, "environment", "homologacao")
        environment = environment.value if hasattr(environment, "value") else str(environment)
        occurred_at = sale.created_at.isoformat()
        if occurred_at.endswith("+00:00"):
            occurred_at = f"{occurred_at[:-6]}Z"
        elif "T" in occurred_at and "+" not in occurred_at[10:] and not occurred_at.endswith("Z"):
            occurred_at = f"{occurred_at}Z"

        payload: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "issuer_id": issuer_id,
            "environment": "production" if environment in {"producao", "production"} else "homologation",
            "external_id": str(sale.id),
            "correlation": {
                "sale_id": str(sale.id), "market_id": str(sale.market_id), "provider_ref": provider_ref,
            },
            "sale": {
                "occurred_at": occurred_at, "model": "65", "destination_uf": "GO",
                "operation_destination": "internal", "consumer_final": True,
                "buyer_presence": "presencial", "issue_mode": "normal",
            },
            "items": items,
            "payments": self._payments(sale),
            "totals": self._totals(items),
        }
        if getattr(sale, "customer_cpf", None):
            cpf = re.sub(r"\D", "", sale.customer_cpf)
            if len(cpf) == 11:
                payload["consumer"] = {"document": cpf}
        payload["snapshot_sha256"] = canonical_contract_sha256(payload)
        return payload

    def _item(self, item) -> dict[str, Any]:
        snapshot = getattr(item, "fiscal_tax_snapshot", None)
        sku = str(getattr(item, "product_id", "unknown"))
        if not isinstance(snapshot, Mapping):
            raise BusinessRuleException(f"sale.fiscal_tax_snapshot_missing; sku={sku}")
        if getattr(item, "fiscal_calculation_version", None) != CALCULATION_VERSION:
            raise BusinessRuleException(f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=calculation_version")
        if getattr(item, "snapshot_sha256", None) != canonical_sha256(snapshot):
            raise BusinessRuleException(f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=snapshot_sha256; reason=mismatch")
        tax = _json_evidence(snapshot)
        for field in ("ncm", "origin", "cfop", "icms", "pis", "cofins", "approval_ref"):
            if tax.get(field) is None:
                raise BusinessRuleException(f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field={field}; reason=required")
        icms = tax["icms"]
        if not isinstance(icms, dict) or icms.get("group") not in {"ICMSSN102", "ICMS40", "ICMSSN500", "ICMS60"}:
            raise BusinessRuleException(f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=icms.group")
        for field in ("current_st_base", "current_st_amount"):
            icms[field] = _money(icms.get(field), field=f"icms.{field}")
        icms["current_st_rate"] = _rate(icms.get("current_st_rate"), field="icms.current_st_rate")
        if icms["group"] in _RETAINED_ST and any(Decimal(icms[field]) != 0 for field in ("current_st_base", "current_st_amount", "current_st_rate")):
            raise BusinessRuleException(f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=icms.current_st; reason=retained_not_current")
        for section in ("pis", "cofins"):
            if not isinstance(tax[section], dict):
                raise BusinessRuleException(f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field={section}")
            tax[section]["base"] = _money(tax[section].get("base"), field=f"{section}.base")
            tax[section]["amount"] = _money(tax[section].get("amount"), field=f"{section}.amount")
            tax[section]["rate"] = _rate(tax[section].get("rate"), field=f"{section}.rate")
        for field in ("own_base", "own_amount"):
            tax["icms"][field] = _money(tax["icms"].get(field), field=f"icms.{field}")
        tax["icms"]["own_rate"] = _rate(tax["icms"].get("own_rate"), field="icms.own_rate")
        return {
            "sku": sku,
            "description": (getattr(item, "product_name_snapshot", None) or getattr(item, "product_name", sku))[:120],
            "quantity": f"{Decimal(str(item.quantity)):.4f}",
            "unit": "UN", "unit_amount": _money(item.unit_price, field="item.unit_price"),
            "total_amount": _money(item.total, field="item.total"),
            "ncm": tax["ncm"], "cest": tax.get("cest"), "origin": tax["origin"], "cfop": tax["cfop"],
            "tax": tax,
        }

    @staticmethod
    def _payments(sale) -> list[dict[str, str]]:
        result = []
        for payment in sale.payments:
            method = payment.method.value if hasattr(payment.method, "value") else str(payment.method)
            result.append({"method": method, "amount": _money(payment.amount, field="payment.amount")})
        return result

    @staticmethod
    def _totals(items: list[dict[str, Any]]) -> dict[str, str]:
        def total(section: str, field: str) -> str:
            return _money(sum((Decimal(item["tax"][section][field]) for item in items), Decimal("0")), field=f"totals.{field}")
        return {
            "products_amount": _money(sum((Decimal(item["total_amount"]) for item in items), Decimal("0")), field="totals.products_amount"),
            "icms_base": total("icms", "own_base"), "icms_amount": total("icms", "own_amount"),
            "current_st_base": total("icms", "current_st_base"), "current_st_amount": total("icms", "current_st_amount"),
            "pis_amount": total("pis", "amount"), "cofins_amount": total("cofins", "amount"),
        }
