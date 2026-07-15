"""Lifecycle and resolution of explicit, evidence-backed fiscal rules."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, Sequence

from domain.fiscal import (
    FiscalRuleError,
    ProductTaxRule,
    ProductTaxRuleStatus,
    TaxRegime,
    TaxRuleApproval,
)
from domain.shared import BusinessRuleException


@dataclass(frozen=True)
class TaxContext:
    state: str
    document_type: str
    consumer_final: bool
    presence: str

    @classmethod
    def go_nfce_consumer_final(cls) -> "TaxContext":
        return cls(
            state="GO", document_type="nfce", consumer_final=True, presence="presencial"
        )

    def is_supported(self) -> bool:
        return (
            self.state.upper() == "GO"
            and self.document_type.lower() == "nfce"
            and self.consumer_final
            and self.presence.lower() == "presencial"
        )


@dataclass(frozen=True)
class ProductTaxRuleCandidate:
    association_id: uuid.UUID
    rule: ProductTaxRule


@dataclass(frozen=True)
class BulkAssignmentResult:
    updated_product_ids: list[uuid.UUID]
    skipped: list[dict]
    previous_rule_ids: dict[str, str | None]


@dataclass(frozen=True)
class ProductTaxRuleAssociation:
    association_id: uuid.UUID
    effective_from: date
    effective_to: date | None
    rule: ProductTaxRule


@dataclass(frozen=True)
class TaxRulePendency:
    product_id: uuid.UUID
    product_name: str
    status: str


@dataclass(frozen=True)
class TaxRulePendencyReport:
    items: list[TaxRulePendency]
    summary: dict[str, int]


class ProductTaxRuleRepository(Protocol):
    async def get_rule(
        self, market_id: uuid.UUID, rule_id: uuid.UUID
    ) -> ProductTaxRule | None: ...

    async def publish_rule_with_approval(
        self, rule: ProductTaxRule, approval: TaxRuleApproval
    ) -> ProductTaxRule: ...

    async def assign_published_rule(
        self,
        *,
        market_id: uuid.UUID,
        product_ids: list[uuid.UUID],
        rule: ProductTaxRule,
        effective_from: date,
        actor_id: uuid.UUID,
        reason: str,
    ) -> tuple[list[uuid.UUID], list[dict], list[dict]]: ...

    async def list_effective_linked_rules(
        self, market_id: uuid.UUID, product_id: uuid.UUID, occurred_on: date
    ) -> Sequence[ProductTaxRuleCandidate]: ...

    async def list_product_rule_associations(
        self, market_id: uuid.UUID, product_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[ProductTaxRuleAssociation]]: ...


class TaxRuleEvidenceService(Protocol):
    async def capture_approval(
        self,
        *,
        rule_id: uuid.UUID,
        market_id: uuid.UUID,
        accountant_user_id: uuid.UUID,
        source_storage_key: str,
    ) -> TaxRuleApproval: ...


APPROVED_GROUP_CODES = {
    "ICMSSN102": {
        "regimes": frozenset({TaxRegime.SIMPLES_NACIONAL}),
        "cst": None,
        "csosn": "102",
        "mode": "non_taxed",
        "requires_cest": False,
    },
    "ICMS40": {
        "regimes": frozenset({TaxRegime.LUCRO_PRESUMIDO, TaxRegime.LUCRO_REAL}),
        "cst": "40",
        "csosn": None,
        "mode": "non_taxed",
        "requires_cest": False,
    },
    "ICMSSN500": {
        "regimes": frozenset({TaxRegime.SIMPLES_NACIONAL}),
        "cst": None,
        "csosn": "500",
        "mode": "retained_st",
        "requires_cest": True,
    },
    "ICMS60": {
        "regimes": frozenset({TaxRegime.LUCRO_PRESUMIDO, TaxRegime.LUCRO_REAL}),
        "cst": "60",
        "csosn": None,
        "mode": "retained_st",
        "requires_cest": True,
    },
}

PENDENCY_STATUSES = (
    "configured",
    "missing",
    "draft",
    "expired",
    "not_yet_effective",
    "context_mismatch",
    "legacy_only",
)


class TaxRuleNotFoundError(BusinessRuleException):
    """No published, effective rule exists for a linked product."""


class FiscalRuleAmbiguousError(BusinessRuleException):
    code = "sale.fiscal_rule_ambiguous"

    def __init__(self, product_id: uuid.UUID, occurred_on: date):
        self.product_id = product_id
        self.occurred_on = occurred_on
        super().__init__(
            "Produto possui mais de uma regra fiscal vigente para a data da venda."
        )

    def details(self) -> dict:
        return {
            "product_id": str(self.product_id),
            "occurred_on": self.occurred_on.isoformat(),
        }


class FiscalRuleMissingError(BusinessRuleException):
    code = "sale.fiscal_rule_missing"

    def __init__(self, affected_products: list[dict[str, str]]):
        self.affected_products = affected_products
        super().__init__(
            "Há produtos sem regra fiscal publicada e vigente para emissão NFC-e."
        )

    def details(self) -> dict:
        return {"affected_products": self.affected_products}


class TaxRuleService:
    """Resolves rules solely through their explicit product association.

    There is intentionally no fallback to legacy profiles, NCM, barcode or
    product name. Context is restricted to the first supported rollout:
    Goiás NFC-e, consumer final, presencial.
    """

    def __init__(
        self,
        repository: ProductTaxRuleRepository,
        *,
        evidence_service: TaxRuleEvidenceService | None = None,
    ):
        self._repository = repository
        self._evidence_service = evidence_service

    async def publish(
        self,
        *,
        market_id: uuid.UUID,
        rule_id: uuid.UUID,
        approved_by: uuid.UUID,
        source_storage_key: str,
    ) -> ProductTaxRule:
        rule = await self._repository.get_rule(market_id, rule_id)
        if rule is None:
            raise FiscalRuleError("tax_rule.not_found", "Regra fiscal não encontrada.")
        if rule.status is not ProductTaxRuleStatus.DRAFT:
            raise FiscalRuleError(
                "tax_rule.not_draft", "Somente rascunhos fiscais podem ser publicados."
            )

        self._validate_for_publication(rule)
        if self._evidence_service is None or not source_storage_key.strip():
            raise FiscalRuleError(
                "tax_rule.evidence_required",
                "A publicação exige evidência oficial verificável e seu checksum.",
            )
        try:
            approval = await self._evidence_service.capture_approval(
                rule_id=rule.id,
                market_id=market_id,
                accountant_user_id=approved_by,
                source_storage_key=source_storage_key,
            )
        except BusinessRuleException as exc:
            raise FiscalRuleError("tax_rule.evidence_required", str(exc)) from exc
        if approval.rule_id != rule.id or not approval.homologation_xml_sha256:
            raise FiscalRuleError(
                "tax_rule.evidence_required",
                "A evidência fiscal não pertence à regra ou não possui "
                "checksum verificável.",
            )

        rule.approved_by = approved_by
        rule.approved_at = approval.approved_at
        return await self._repository.publish_rule_with_approval(rule, approval)

    async def assign_products(
        self,
        *,
        market_id: uuid.UUID,
        rule_id: uuid.UUID,
        product_ids: list[uuid.UUID],
        effective_from: date,
        actor_id: uuid.UUID,
        reason: str,
    ) -> BulkAssignmentResult:
        if not product_ids:
            raise FiscalRuleError(
                "tax_rule.products_required",
                "Informe ao menos um produto para associação.",
            )
        if len(set(product_ids)) != len(product_ids):
            raise FiscalRuleError(
                "tax_rule.duplicate_product",
                "A associação em massa contém produto duplicado.",
            )
        if not reason.strip():
            raise FiscalRuleError(
                "tax_rule.assignment_reason_required",
                "A associação fiscal exige uma justificativa de auditoria.",
            )

        rule = await self._repository.get_rule(market_id, rule_id)
        if rule is None:
            raise FiscalRuleError("tax_rule.not_found", "Regra fiscal não encontrada.")
        if rule.status is not ProductTaxRuleStatus.PUBLISHED:
            raise FiscalRuleError(
                "tax_rule.not_published",
                "Somente regras fiscais publicadas podem ser atribuídas.",
            )

        try:
            (
                updated,
                skipped,
                audit_changes,
            ) = await self._repository.assign_published_rule(
                market_id=market_id,
                product_ids=product_ids,
                rule=rule,
                effective_from=effective_from,
                actor_id=actor_id,
                reason=reason.strip(),
            )
        except FiscalRuleError:
            raise
        except BusinessRuleException as exc:
            raise FiscalRuleError("tax_rule.assignment_invalid", str(exc)) from exc

        mismatches = [
            {"product_id": item["product_id"]}
            for item in skipped
            if item.get("reason") in {"product_not_found", "product_market_mismatch"}
        ]
        if mismatches:
            raise FiscalRuleError(
                "tax_rule.product_market_mismatch",
                "Um ou mais produtos não existem no mercado informado.",
                mismatches,
            )
        previous_rule_ids = {
            item["product_id"]: item.get("before_rule_id") for item in audit_changes
        }
        return BulkAssignmentResult(updated, skipped, previous_rule_ids)

    async def list_pendencies(
        self,
        *,
        market_id: uuid.UUID,
        products: Sequence,
        when: date,
        issuer_regime: TaxRegime,
        destination_uf: str,
        document_model: str,
    ) -> TaxRulePendencyReport:
        active_products = [
            product
            for product in products
            if getattr(product, "active", True)
            and getattr(product, "deleted_at", None) is None
        ]
        product_ids = [product.id for product in active_products]
        associations = await self._repository.list_product_rule_associations(
            market_id, product_ids
        )
        items = [
            TaxRulePendency(
                product_id=product.id,
                product_name=product.name,
                status=self._classify_pendency(
                    market_id=market_id,
                    legacy_rule_id=getattr(product, "tax_rule_id", None),
                    associations=associations.get(product.id, []),
                    when=when,
                    issuer_regime=issuer_regime,
                    destination_uf=destination_uf,
                    document_model=document_model,
                ),
            )
            for product in active_products
        ]
        summary = {status: 0 for status in PENDENCY_STATUSES}
        for item in items:
            summary[item.status] += 1
        summary["total"] = len(items)
        return TaxRulePendencyReport(items, summary)

    @staticmethod
    def _classify_pendency(
        *,
        market_id: uuid.UUID,
        legacy_rule_id: uuid.UUID | None,
        associations: Sequence[ProductTaxRuleAssociation],
        when: date,
        issuer_regime: TaxRegime,
        destination_uf: str,
        document_model: str,
    ) -> str:
        if not associations:
            return "legacy_only" if legacy_rule_id else "missing"

        active = [
            association
            for association in associations
            if association.effective_from <= when
            and (association.effective_to is None or association.effective_to >= when)
        ]
        if not active:
            if any(association.effective_from > when for association in associations):
                return "not_yet_effective"
            return "expired"
        if len(active) != 1:
            return "context_mismatch"

        rule = active[0].rule
        if rule.status is ProductTaxRuleStatus.DRAFT:
            return "draft"
        if rule.status is not ProductTaxRuleStatus.PUBLISHED:
            return "expired"
        if rule.effective_from is None or rule.effective_from > when:
            return "not_yet_effective"
        if rule.effective_to is not None and rule.effective_to < when:
            return "expired"
        if (
            rule.market_id != market_id
            or rule.issuer_regime is not issuer_regime
            or not rule.matches_context(
                destination_uf=destination_uf, document_model=document_model
            )
        ):
            return "context_mismatch"
        return "configured"

    @staticmethod
    def _validate_for_publication(rule: ProductTaxRule) -> None:
        required = {
            "name": rule.name,
            "effective_from": rule.effective_from,
            "issuer_regime": rule.issuer_regime,
            "destination_uf": rule.destination_uf,
            "document_model": rule.document_model,
            "ncm": rule.ncm,
            "origin": rule.origin,
            "cfop": rule.cfop,
            "icms_group": rule.icms_group,
            "pis_cst": rule.pis_cst,
            "cofins_cst": rule.cofins_cst,
        }
        missing = [
            name for name, value in required.items() if value is None or value == ""
        ]
        if missing:
            raise FiscalRuleError(
                "tax_rule.invalid",
                "Regra fiscal sem campos obrigatórios para publicação.",
                [{"field": field, "reason": "required"} for field in sorted(missing)],
            )

        matrix = APPROVED_GROUP_CODES.get(rule.icms_group or "")
        parameters = rule.tax_parameters
        mode = parameters.get("icms_mode") if isinstance(parameters, Mapping) else None
        if (
            matrix is None
            or rule.issuer_regime not in matrix["regimes"]
            or rule.icms_cst != matrix["cst"]
            or rule.icms_csosn != matrix["csosn"]
            or mode != matrix["mode"]
        ):
            raise FiscalRuleError(
                "tax_rule.group_incompatible",
                "Grupo, regime, código ICMS e modo fiscal não pertencem "
                "à matriz homologada.",
            )
        if matrix["requires_cest"] and not rule.cest:
            raise FiscalRuleError(
                "tax_rule.invalid",
                "Grupos de ICMS retido anteriormente exigem CEST.",
                [{"field": "cest", "reason": "required_for_retained_st"}],
            )

        if not isinstance(parameters, Mapping) or not all(
            isinstance(parameters.get(name), Mapping) for name in ("pis", "cofins")
        ):
            raise FiscalRuleError(
                "tax_rule.invalid",
                "A publicação exige parâmetros explícitos de PIS e COFINS.",
                [
                    {"field": name, "reason": "required"}
                    for name in ("tax_parameters.pis", "tax_parameters.cofins")
                    if not isinstance(
                        parameters.get(name)
                        if isinstance(parameters, Mapping)
                        else None,
                        Mapping,
                    )
                ],
            )

        evidence = rule.approval
        reference = evidence.get("reference") if isinstance(evidence, Mapping) else None
        checksum = evidence.get("checksum") if isinstance(evidence, Mapping) else None
        if (
            not isinstance(reference, str)
            or not reference.strip()
            or not isinstance(checksum, str)
            or len(checksum) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in checksum)
        ):
            raise FiscalRuleError(
                "tax_rule.evidence_required",
                "A publicação exige referência oficial/revisão e checksum SHA-256.",
            )

    async def resolve_for_sale_item(
        self,
        *,
        market_id: uuid.UUID,
        product_id: uuid.UUID,
        occurred_at: datetime,
        context: TaxContext,
    ) -> ProductTaxRule:
        if not context.is_supported():
            raise TaxRuleNotFoundError(
                "Contexto fiscal ainda não homologado para esta regra de produto."
            )

        candidates = await self._repository.list_effective_linked_rules(
            market_id,
            product_id,
            occurred_at.date(),
        )
        if len({candidate.association_id for candidate in candidates}) > 1:
            raise FiscalRuleAmbiguousError(product_id, occurred_at.date())

        valid = [
            candidate.rule
            for candidate in candidates
            if candidate.rule.market_id == market_id
            and candidate.rule.status is ProductTaxRuleStatus.PUBLISHED
            and candidate.rule.is_effective_on(occurred_at.date())
        ]
        if not valid:
            raise TaxRuleNotFoundError("Produto sem regra fiscal publicada e vigente.")

        return max(
            valid,
            key=lambda rule: (rule.effective_from or date.min, str(rule.id)),
        )
