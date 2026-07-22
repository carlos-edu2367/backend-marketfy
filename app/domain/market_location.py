"""Validated physical location belonging to one Marketfy market."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID


class MarketLocationValidationError(ValueError):
    """Raised when a location cannot be safely sent to a provider."""


_SOURCES = {"manual", "cep", "browser_geolocation", "map_pin"}


@dataclass(frozen=True)
class MarketLocation:
    market_id: UUID
    postal_code: str
    street_name: str
    street_number: str
    city_name: str
    state_code: str
    state_name: str
    latitude: Decimal
    longitude: Decimal
    country_code: str = "BR"
    district: Optional[str] = None
    complement: Optional[str] = None
    source: str = "manual"
    version: int = 1

    def __post_init__(self) -> None:
        postal_code = re.sub(r"\D", "", str(self.postal_code or ""))
        object.__setattr__(self, "postal_code", postal_code)
        object.__setattr__(self, "state_code", str(self.state_code or "").strip().upper())
        object.__setattr__(self, "country_code", str(self.country_code or "BR").strip().upper())
        for field in ("street_name", "street_number", "city_name", "state_name"):
            object.__setattr__(self, field, str(getattr(self, field) or "").strip())
        for field in ("district", "complement"):
            value = getattr(self, field)
            object.__setattr__(self, field, str(value).strip() if value else None)
        try:
            object.__setattr__(self, "latitude", Decimal(str(self.latitude)))
            object.__setattr__(self, "longitude", Decimal(str(self.longitude)))
        except (InvalidOperation, ValueError) as exc:
            raise MarketLocationValidationError("latitude/longitude inválidas") from exc

    def validate(self) -> None:
        required = {
            "postal_code": self.postal_code,
            "street_name": self.street_name,
            "street_number": self.street_number,
            "city_name": self.city_name,
            "state_code": self.state_code,
            "state_name": self.state_name,
        }
        for field, value in required.items():
            if not value:
                raise MarketLocationValidationError(f"{field} é obrigatório")
        if not re.fullmatch(r"\d{8}", self.postal_code):
            raise MarketLocationValidationError("postal_code inválido")
        if not re.fullmatch(r"[A-Z]{2}", self.state_code):
            raise MarketLocationValidationError("state_code inválido")
        if self.country_code != "BR":
            raise MarketLocationValidationError("country_code deve ser BR")
        if self.latitude.is_nan() or not Decimal("-90") <= self.latitude <= Decimal("90"):
            raise MarketLocationValidationError("latitude inválida")
        if self.longitude.is_nan() or not Decimal("-180") <= self.longitude <= Decimal("180"):
            raise MarketLocationValidationError("longitude inválida")
        if self.source not in _SOURCES:
            raise MarketLocationValidationError("source inválida")
        if self.version < 1:
            raise MarketLocationValidationError("version inválida")

    def to_mp_location(self) -> dict:
        self.validate()
        return {
            "street_number": self.street_number,
            "street_name": self.street_name,
            "city_name": self.city_name,
            "state_name": self.state_name,
            "latitude": float(self.latitude),
            "longitude": float(self.longitude),
        }
