"""Read-only inventory of product fiscal-rule rollout pendencies."""
from __future__ import annotations

import csv
import io
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from typing import Callable

from domain.fiscal import TaxRegime


@dataclass(frozen=True)
class FiscalTaxMigrationRow:
    product_id: str
    code: str
    name: str
    ncm: str | None
    current_rule_id: str | None
    rule_status: str | None
    effective_from: str | None
    effective_to: str | None
    pendency_code: str


@dataclass(frozen=True)
class FiscalTaxMigrationReport:
    rows: tuple[FiscalTaxMigrationRow, ...]
    counts: dict[str, int]

    def to_json(self) -> str:
        return json.dumps(
            {"counts": self.counts, "products": [asdict(row) for row in self.rows]},
            ensure_ascii=False,
            sort_keys=True,
        )

    def to_csv(self) -> str:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=FiscalTaxMigrationRow.__dataclass_fields__)
        writer.writeheader()
        writer.writerows(asdict(row) for row in self.rows)
        return output.getvalue()


class FiscalTaxMigrationJob:
    """Produces report data only; it deliberately has no save or commit dependency."""

    def __init__(
        self,
        *,
        product_repository,
        tax_rule_repository,
        tax_rule_service,
        config_repository,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._product_repository = product_repository
        self._tax_rule_repository = tax_rule_repository
        self._tax_rule_service = tax_rule_service
        self._config_repository = config_repository
        self._today = today

    async def run(self, market_id) -> FiscalTaxMigrationReport:
        products = await self._product_repository.list_by_market(market_id)
        active_products = tuple(
            product
            for product in products
            if getattr(product, "active", True) and getattr(product, "deleted_at", None) is None
        )
        when = self._today()
        config = await self._config_repository.get_by_market(market_id)
        pendencies = await self._pendencies(
            market_id=market_id,
            products=active_products,
            config=config,
            when=when,
        )
        associations = await self._tax_rule_repository.list_product_rule_associations(
            market_id, [product.id for product in active_products]
        )
        rows = tuple(
            self._row_for(
                product=product,
                associations=associations.get(product.id, ()),
                pendency_code=pendencies.get(product.id, "context_mismatch"),
                when=when,
            )
            for product in active_products
        )
        counts = dict(Counter(row.pendency_code for row in rows))
        counts["total"] = len(rows)
        return FiscalTaxMigrationReport(rows=rows, counts=counts)

    async def _pendencies(self, *, market_id, products, config, when: date) -> dict:
        context = _rollout_context(config)
        regime = _tax_regime(config)
        if context is None or regime is None:
            return {product.id: "context_mismatch" for product in products}
        report = await self._tax_rule_service.list_pendencies(
            market_id=market_id,
            products=products,
            when=when,
            issuer_regime=regime,
            destination_uf=context[0],
            document_model=context[1],
        )
        return {item.product_id: item.status for item in report.items}

    @staticmethod
    def _row_for(*, product, associations, pendency_code: str, when: date) -> FiscalTaxMigrationRow:
        active = [
            association
            for association in associations
            if association.effective_from <= when
            and (association.effective_to is None or association.effective_to >= when)
        ]
        association = active[0] if len(active) == 1 else None
        rule = association.rule if association is not None else None
        return FiscalTaxMigrationRow(
            product_id=str(product.id),
            code=str(getattr(product, "code", "") or ""),
            name=str(getattr(product, "name", "") or ""),
            ncm=getattr(product, "ncm", None),
            current_rule_id=(
                str(rule.id)
                if rule is not None
                else str(getattr(product, "tax_rule_id", None))
                if getattr(product, "tax_rule_id", None) is not None
                else None
            ),
            rule_status=rule.status.value if rule is not None else None,
            effective_from=association.effective_from.isoformat() if association else None,
            effective_to=association.effective_to.isoformat() if association and association.effective_to else None,
            pendency_code=pendency_code,
        )


def _tax_regime(config) -> TaxRegime | None:
    value = getattr(config, "tax_regime", None) if config is not None else None
    if isinstance(value, TaxRegime):
        return value
    try:
        return TaxRegime(value)
    except (TypeError, ValueError):
        return None


def _rollout_context(config) -> tuple[str, str] | None:
    if config is None:
        return None
    address = getattr(config, "address_json", None)
    if isinstance(address, str):
        try:
            address = json.loads(address)
        except (TypeError, ValueError):
            address = None
    address = address if isinstance(address, dict) else {}
    destination_uf = address.get("uf")
    document_model = getattr(config, "document_model", None)
    if document_model is None and getattr(config, "nfce_series", None):
        document_model = "65"
    if destination_uf != "GO" or document_model != "65":
        return None
    return destination_uf, document_model
