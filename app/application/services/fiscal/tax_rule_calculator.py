"""Pure fiscal snapshots from explicit, accountant-approved rule evidence."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Optional

from application.services.fiscal.snapshot_integrity import CALCULATION_VERSION
from domain.fiscal import ProductTaxRule
from domain.sales import SaleItem
from domain.shared import BusinessRuleException


_MONEY = Decimal("0.01")
_RATE = Decimal("0.0001")
_ZERO_MONEY = "0.00"
_ZERO_RATE = "0.0000"
_RETAINED_ST_GROUPS = frozenset({"ICMSSN500", "ICMS60"})
_CURRENT_ST_GROUPS = frozenset({"ICMSSN201", "ICMS10", "ICMS30", "ICMS70", "ICMS90"})


def _scaled(value: Any, *, quantum: Decimal, field: str) -> str:
    if isinstance(value, (float, bool)):
        raise BusinessRuleException(
            f"{field} deve usar decimal exato, nunca float binário."
        )
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BusinessRuleException(f"{field} possui valor decimal inválido.") from exc
    places = abs(quantum.as_tuple().exponent)
    return f"{number.quantize(quantum, rounding=ROUND_HALF_UP):.{places}f}"


def _optional_scaled(
    value: Any, *, quantum: Decimal, field: str
) -> Optional[str]:
    if value is None:
        return None
    return _scaled(value, quantum=quantum, field=field)


@dataclass(frozen=True)
class IcmsSnapshot:
    mode: str
    group: Optional[str]
    cst: Optional[str]
    csosn: Optional[str]
    own_base: str = _ZERO_MONEY
    own_rate: str = _ZERO_RATE
    own_amount: str = _ZERO_MONEY
    current_st_base: str = _ZERO_MONEY
    current_st_rate: str = _ZERO_RATE
    current_st_amount: str = _ZERO_MONEY
    retained_st_base: Optional[str] = None
    retained_st_rate: Optional[str] = None
    retained_st_amount: Optional[str] = None
    retained_fcp_base: Optional[str] = None
    retained_fcp_rate: Optional[str] = None
    retained_fcp_amount: Optional[str] = None

    @property
    def reduction_rate(self) -> Decimal:
        return Decimal(_ZERO_RATE)

    @property
    def st_base(self) -> Decimal:
        return Decimal(self.current_st_base)

    @property
    def st_mva_rate(self) -> Decimal:
        return Decimal(_ZERO_RATE)

    @property
    def st_rate(self) -> Decimal:
        return Decimal(self.current_st_rate)

    @property
    def st_amount(self) -> Decimal:
        return Decimal(self.current_st_amount)

    @property
    def fcp_rate(self) -> Decimal:
        return Decimal(_ZERO_RATE)

    @property
    def fcp_amount(self) -> Decimal:
        return Decimal(_ZERO_MONEY)

    def as_dict(self, *, compatibility: bool = False) -> dict[str, Any]:
        result = {
            "mode": self.mode,
            "group": self.group,
            "cst": self.cst,
            "csosn": self.csosn,
            "own_base": self.own_base,
            "own_rate": self.own_rate,
            "own_amount": self.own_amount,
            "current_st_base": self.current_st_base,
            "current_st_rate": self.current_st_rate,
            "current_st_amount": self.current_st_amount,
            "retained_st_base": self.retained_st_base,
            "retained_st_rate": self.retained_st_rate,
            "retained_st_amount": self.retained_st_amount,
            "retained_fcp_base": self.retained_fcp_base,
            "retained_fcp_rate": self.retained_fcp_rate,
            "retained_fcp_amount": self.retained_fcp_amount,
        }
        if compatibility:
            result.update(
                reduction_rate=self.reduction_rate,
                st_base=self.st_base,
                st_mva_rate=self.st_mva_rate,
                st_rate=self.st_rate,
                st_amount=self.st_amount,
                fcp_rate=self.fcp_rate,
                fcp_amount=self.fcp_amount,
            )
        return result


@dataclass(frozen=True)
class ContributionSnapshot:
    group: str
    cst: Optional[str]
    base: str
    rate: str
    amount: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "cst": self.cst,
            "base": self.base,
            "rate": self.rate,
            "amount": self.amount,
        }


@dataclass(frozen=True)
class ItemFiscalSnapshot:
    rule_id: str
    rule_version: int
    calculation_version: str
    ncm: Optional[str]
    cest: Optional[str]
    origin: Optional[str]
    cfop: Optional[str]
    cbenef: Optional[str]
    approval_ref: str
    icms: IcmsSnapshot
    pis: ContributionSnapshot
    cofins: ContributionSnapshot

    def as_contract_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "calculation_version": self.calculation_version,
            "ncm": self.ncm,
            "cest": self.cest,
            "origin": self.origin,
            "cfop": self.cfop,
            "cbenef": self.cbenef,
            "approval_ref": self.approval_ref,
            "icms": self.icms.as_dict(),
            "pis": self.pis.as_dict(),
            "cofins": self.cofins.as_dict(),
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_contract_dict()[key]

    def as_persistence_dict(self) -> dict[str, Any]:
        """Keep current consumers working without restoring legacy ST formulas."""
        result = self.as_contract_dict()
        result["icms"] = self.icms.as_dict(compatibility=True)
        return result


def _contribution(params: Mapping[str, Any], name: str) -> ContributionSnapshot:
    raw = params.get(name)
    if not isinstance(raw, Mapping):
        raise BusinessRuleException(
            f"Parâmetros explícitos de {name.upper()} são obrigatórios."
        )
    try:
        group = raw["group"]
        base = raw["base"]
        rate = raw["rate"]
        amount = raw["amount"]
    except KeyError as exc:
        raise BusinessRuleException(
            f"Parâmetros explícitos de {name.upper()} estão incompletos."
        ) from exc
    return ContributionSnapshot(
        group=str(group),
        cst=None if raw.get("cst") is None else str(raw["cst"]),
        base=_scaled(base, quantum=_MONEY, field=f"{name}.base"),
        rate=_scaled(rate, quantum=_RATE, field=f"{name}.rate"),
        amount=_scaled(amount, quantum=_MONEY, field=f"{name}.amount"),
    )


class TaxRuleCalculator:
    """Build a v2 snapshot without deriving missing fiscal evidence."""

    def calculate(self, *, item: SaleItem, rule: ProductTaxRule) -> ItemFiscalSnapshot:
        del item  # The retail price is not evidence for retained ST.
        if rule.icms_group in _CURRENT_ST_GROUPS:
            raise BusinessRuleException(
                f"Grupo {rule.icms_group} exige ST da operação atual, ainda não suportada."
            )

        params = rule.tax_parameters
        if not isinstance(params, Mapping) or not isinstance(
            params.get("icms_mode"), str
        ):
            raise BusinessRuleException("Parâmetros fiscais v2 explícitos são obrigatórios.")

        approval = rule.approval
        if not isinstance(approval, Mapping) or not isinstance(
            approval.get("reference"), str
        ):
            raise BusinessRuleException("Referência de aprovação fiscal é obrigatória.")

        is_retained = rule.icms_group in _RETAINED_ST_GROUPS
        icms = IcmsSnapshot(
            mode=params["icms_mode"],
            group=rule.icms_group,
            cst=rule.icms_cst,
            csosn=rule.icms_csosn,
            retained_st_base=_optional_scaled(
                params.get("retained_st_base") if is_retained else None,
                quantum=_MONEY,
                field="retained_st_base",
            ),
            retained_st_rate=_optional_scaled(
                params.get("retained_st_rate") if is_retained else None,
                quantum=_RATE,
                field="retained_st_rate",
            ),
            retained_st_amount=_optional_scaled(
                params.get("retained_st_amount") if is_retained else None,
                quantum=_MONEY,
                field="retained_st_amount",
            ),
            retained_fcp_base=_optional_scaled(
                params.get("retained_fcp_base") if is_retained else None,
                quantum=_MONEY,
                field="retained_fcp_base",
            ),
            retained_fcp_rate=_optional_scaled(
                params.get("retained_fcp_rate") if is_retained else None,
                quantum=_RATE,
                field="retained_fcp_rate",
            ),
            retained_fcp_amount=_optional_scaled(
                params.get("retained_fcp_amount") if is_retained else None,
                quantum=_MONEY,
                field="retained_fcp_amount",
            ),
        )
        return ItemFiscalSnapshot(
            rule_id=str(rule.id),
            rule_version=rule.version,
            calculation_version=CALCULATION_VERSION,
            ncm=rule.ncm,
            cest=rule.cest,
            origin=rule.origin,
            cfop=rule.cfop,
            cbenef=rule.cbenef,
            approval_ref=approval["reference"],
            icms=icms,
            pis=_contribution(params, "pis"),
            cofins=_contribution(params, "cofins"),
        )
