"""Resolution of explicitly linked, accountant-approved fiscal rules."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence

from domain.fiscal import ProductTaxRule, ProductTaxRuleStatus
from domain.shared import BusinessRuleException


@dataclass(frozen=True)
class TaxContext:
    state: str
    document_type: str
    consumer_final: bool
    presence: str

    @classmethod
    def go_nfce_consumer_final(cls) -> "TaxContext":
        return cls(state="GO", document_type="nfce", consumer_final=True, presence="presencial")

    def is_supported(self) -> bool:
        return (
            self.state.upper() == "GO"
            and self.document_type.lower() == "nfce"
            and self.consumer_final
            and self.presence.lower() == "presencial"
        )


class ProductTaxRuleRepository(Protocol):
    async def list_linked_rules(
        self, market_id: uuid.UUID, product_id: uuid.UUID
    ) -> Sequence[ProductTaxRule]: ...


class TaxRuleNotFoundError(BusinessRuleException):
    """No published, effective rule exists for a linked product."""


class FiscalRuleMissingError(BusinessRuleException):
    code = "sale.fiscal_rule_missing"

    def __init__(self, affected_products: list[dict[str, str]]):
        self.affected_products = affected_products
        super().__init__("Há produtos sem regra fiscal publicada e vigente para emissão NFC-e.")

    def details(self) -> dict:
        return {"affected_products": self.affected_products}


class TaxRuleService:
    """Resolves rules solely through their explicit product association.

    There is intentionally no fallback to legacy profiles, NCM, barcode or
    product name. Context is restricted to the first supported rollout:
    Goiás NFC-e, consumer final, presencial.
    """

    def __init__(self, repository: ProductTaxRuleRepository):
        self._repository = repository

    async def resolve_for_sale_item(
        self,
        *,
        market_id: uuid.UUID,
        product_id: uuid.UUID,
        occurred_at: datetime,
        context: TaxContext,
    ) -> ProductTaxRule:
        if not context.is_supported():
            raise TaxRuleNotFoundError("Contexto fiscal ainda não homologado para esta regra de produto.")

        candidates = await self._repository.list_linked_rules(market_id, product_id)
        valid = [
            rule
            for rule in candidates
            if rule.market_id == market_id
            and rule.status is ProductTaxRuleStatus.PUBLISHED
            and rule.is_effective_on(occurred_at.date())
        ]
        if not valid:
            raise TaxRuleNotFoundError("Produto sem regra fiscal publicada e vigente.")

        return max(valid, key=lambda rule: rule.version)
