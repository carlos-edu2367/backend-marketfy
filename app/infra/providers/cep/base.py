from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CepAddress:
    postal_code: str
    street_name: str
    city_name: str
    state_code: str
    district: str | None = None

    def public_dict(self) -> dict:
        return {
            "postal_code": self.postal_code,
            "street_name": self.street_name,
            "city_name": self.city_name,
            "state_code": self.state_code,
            "district": self.district,
        }


class CepProvider(Protocol):
    async def lookup(self, postal_code: str) -> CepAddress | None: ...
