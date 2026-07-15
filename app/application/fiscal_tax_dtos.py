"""Strict public DTOs for the versioned product-tax rule API."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.fiscal import IcmsMode, TaxRegime


def _reject_binary_floats(value: Any, path: str = "body") -> None:
    if isinstance(value, float):
        raise ValueError(f"float binário não permitido em payload fiscal: {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_binary_floats(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_binary_floats(child, f"{path}[{index}]")


class StrictFiscalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_binary_floats(cls, value: Any) -> Any:
        _reject_binary_floats(value)
        return value


class FiscalIcmsParameters(StrictFiscalModel):
    group: str = Field(min_length=1, max_length=32)
    cst: str | None = Field(default=None, min_length=2, max_length=3)
    csosn: str | None = Field(default=None, min_length=3, max_length=3)
    mode: IcmsMode
    retained_st_base: Decimal | None = None
    retained_st_rate: Decimal | None = None
    retained_st_amount: Decimal | None = None
    retained_fcp_base: Decimal | None = None
    retained_fcp_rate: Decimal | None = None
    retained_fcp_amount: Decimal | None = None


class FiscalContributionParameters(StrictFiscalModel):
    group: str = Field(min_length=1, max_length=32)
    cst: str = Field(min_length=2, max_length=2)
    base: Decimal
    rate: Decimal
    amount: Decimal


class FiscalApprovalReference(StrictFiscalModel):
    reference: str = Field(min_length=1, max_length=2048)
    checksum: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class FiscalTaxRuleDraftRequest(StrictFiscalModel):
    name: str = Field(min_length=1, max_length=255)
    effective_from: date | None = None
    effective_to: date | None = None
    issuer_regime: TaxRegime | None = None
    destination_uf: str | None = Field(default=None, min_length=2, max_length=2)
    document_model: str | None = Field(default=None, min_length=2, max_length=2)
    ncm: str | None = Field(default=None, min_length=8, max_length=8)
    cest: str | None = Field(default=None, min_length=7, max_length=7)
    origin: str | None = Field(default=None, min_length=1, max_length=1)
    cfop: str | None = Field(default=None, min_length=4, max_length=4)
    cbenef: str | None = Field(default=None, max_length=16)
    icms: FiscalIcmsParameters | None = None
    pis: FiscalContributionParameters | None = None
    cofins: FiscalContributionParameters | None = None
    approval: FiscalApprovalReference | None = None

    def to_domain_kwargs(self, *, exclude_unset: bool = False) -> dict[str, Any]:
        values = self.model_dump(exclude_unset=exclude_unset)
        icms = values.pop("icms", None)
        pis = values.pop("pis", None)
        cofins = values.pop("cofins", None)
        approval = values.pop("approval", None)

        if icms is not None:
            values.update(
                icms_group=icms.pop("group"),
                icms_cst=icms.pop("cst"),
                icms_csosn=icms.pop("csosn"),
            )
            values["tax_parameters"] = {
                "icms_mode": icms.pop("mode").value,
                **{
                    key: format(value, "f") if isinstance(value, Decimal) else value
                    for key, value in icms.items()
                },
            }
        if pis is not None:
            values["pis_cst"] = pis["cst"]
            values["pis_rate"] = pis["rate"]
            values.setdefault("tax_parameters", {})["pis"] = _decimal_strings(pis)
        if cofins is not None:
            values["cofins_cst"] = cofins["cst"]
            values["cofins_rate"] = cofins["rate"]
            values.setdefault("tax_parameters", {})["cofins"] = _decimal_strings(cofins)
        if approval is not None:
            values["approval"] = approval
        return values


class FiscalTaxRuleDraftPatchRequest(FiscalTaxRuleDraftRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)


def _decimal_strings(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: format(value, "f") if isinstance(value, Decimal) else value
        for key, value in values.items()
    }


class ProductTaxRulePublishRequest(StrictFiscalModel):
    homologation_xml_storage_key: str = Field(min_length=1, max_length=2048)


class ProductTaxRuleAssignmentRequest(StrictFiscalModel):
    tax_rule_id: UUID
    product_ids: list[UUID] = Field(min_length=1)
    effective_from: date
    reason: str = Field(min_length=3, max_length=500)


class FiscalPreflightItem(StrictFiscalModel):
    product_id: UUID
    quantity: Decimal = Field(gt=0)


class FiscalPreflightRequest(StrictFiscalModel):
    occurred_at: datetime
    items: list[FiscalPreflightItem] = Field(min_length=1)


class FiscalProductError(StrictFiscalModel):
    code: str
    product_id: UUID
    product_name: str
    details: list[dict[str, Any]] | None = None
    reason: str | None = None


class FiscalPreflightResponse(StrictFiscalModel):
    allowed: bool
    enforcement: Literal["off", "warn", "block"]
    errors: list[FiscalProductError]


class FiscalRuleEnforcementRequest(StrictFiscalModel):
    mode: Literal["off", "warn", "block"]
