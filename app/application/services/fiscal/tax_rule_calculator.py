"""Pure fiscal calculation for an already approved product tax rule."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from domain.fiscal import ProductTaxRule
from domain.sales import SaleItem


_HUNDRED = Decimal("100")
_MONEY = Decimal("0.01")
_ZERO = Decimal("0.00")
_ZERO_VALUE_ICMS_GROUPS = frozenset({"ICMSSN102", "ICMS40"})
_ZERO_VALUE_PIS_GROUPS = frozenset({"PIS07"})
_ZERO_VALUE_COFINS_GROUPS = frozenset({"COFINS07"})


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(_MONEY, rounding=ROUND_HALF_UP)


def _rate_amount(base: Decimal, rate: Optional[Decimal]) -> Decimal:
    if rate is None:
        return Decimal("0.00")
    return _money(base * Decimal(rate) / _HUNDRED)


@dataclass(frozen=True)
class IcmsSnapshot:
    group: Optional[str]
    cst: Optional[str]
    csosn: Optional[str]
    own_base: Decimal
    reduction_rate: Decimal
    own_rate: Decimal
    own_amount: Decimal
    st_base: Decimal
    st_mva_rate: Decimal
    st_rate: Decimal
    st_amount: Decimal
    fcp_rate: Decimal
    fcp_amount: Decimal


@dataclass(frozen=True)
class ContributionSnapshot:
    group: str
    cst: Optional[str]
    base: Decimal
    rate: Decimal
    amount: Decimal


@dataclass(frozen=True)
class ItemFiscalSnapshot:
    rule_id: str
    rule_version: int
    ncm: Optional[str]
    cest: Optional[str]
    cfop: Optional[str]
    origin: Optional[str]
    icms: IcmsSnapshot
    pis: ContributionSnapshot
    cofins: ContributionSnapshot

    def as_persistence_dict(self) -> dict:
        """Return the immutable, normalized snapshot kept with the sale item.

        Decimals deliberately remain Decimals here. Repository serialization is
        responsible for lossless JSON encoding and no provider-contract strings
        are produced by this calculation layer.
        """
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "ncm": self.ncm,
            "cest": self.cest,
            "cfop": self.cfop,
            "origin": self.origin,
            "icms": self.icms.__dict__.copy(),
            "pis": self.pis.__dict__.copy(),
            "cofins": self.cofins.__dict__.copy(),
        }


class TaxRuleCalculator:
    """Calculates monetary tax values with a single, deterministic rounding rule."""

    def calculate(self, *, item: SaleItem, rule: ProductTaxRule) -> ItemFiscalSnapshot:
        gross_base = _money(item.total)
        reduction_rate = Decimal(rule.icms_reduction_rate or "0")
        is_zero_value_icms_group = rule.icms_group in _ZERO_VALUE_ICMS_GROUPS
        own_base = (
            _ZERO
            if is_zero_value_icms_group
            else _money(gross_base * (Decimal("1") - reduction_rate / _HUNDRED))
        )
        own_rate = Decimal(rule.icms_rate or "0")
        own_amount = _ZERO if is_zero_value_icms_group else _rate_amount(own_base, own_rate)

        has_st = not is_zero_value_icms_group and rule.icms_st_rate is not None
        st_rate = Decimal(rule.icms_st_rate or "0")
        mva_rate = Decimal(rule.icms_st_mva_rate or "0")
        st_base = _money(own_base * (Decimal("1") + mva_rate / _HUNDRED)) if has_st else _ZERO
        st_gross = _rate_amount(st_base, st_rate) if has_st else _ZERO
        st_amount = max(st_gross - own_amount, _ZERO) if has_st else _ZERO

        fcp_rate = Decimal(rule.fcp_rate or "0")
        fcp_base = st_base if has_st else own_base
        fcp_amount = _ZERO if is_zero_value_icms_group else _rate_amount(fcp_base, fcp_rate)
        pis_group = f"PIS{rule.pis_cst or ''}"
        cofins_group = f"COFINS{rule.cofins_cst or ''}"
        pis_base = _ZERO if pis_group in _ZERO_VALUE_PIS_GROUPS else gross_base
        cofins_base = _ZERO if cofins_group in _ZERO_VALUE_COFINS_GROUPS else gross_base
        pis_rate = _ZERO if pis_group in _ZERO_VALUE_PIS_GROUPS else Decimal(rule.pis_rate or "0")
        cofins_rate = (
            _ZERO if cofins_group in _ZERO_VALUE_COFINS_GROUPS else Decimal(rule.cofins_rate or "0")
        )

        return ItemFiscalSnapshot(
            rule_id=str(rule.id),
            rule_version=rule.version,
            ncm=rule.ncm,
            cest=rule.cest,
            cfop=rule.cfop,
            origin=rule.origin,
            icms=IcmsSnapshot(
                group=rule.icms_group,
                cst=rule.icms_cst,
                csosn=rule.icms_csosn,
                own_base=own_base,
                reduction_rate=reduction_rate,
                own_rate=own_rate,
                own_amount=own_amount,
                st_base=st_base,
                st_mva_rate=mva_rate,
                st_rate=st_rate,
                st_amount=st_amount,
                fcp_rate=fcp_rate,
                fcp_amount=fcp_amount,
            ),
            pis=ContributionSnapshot(
                group=pis_group,
                cst=rule.pis_cst,
                base=pis_base,
                rate=pis_rate,
                amount=_rate_amount(pis_base, pis_rate),
            ),
            cofins=ContributionSnapshot(
                group=cofins_group,
                cst=rule.cofins_cst,
                base=cofins_base,
                rate=cofins_rate,
                amount=_rate_amount(cofins_base, cofins_rate),
            ),
        )
