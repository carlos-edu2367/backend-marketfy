# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from decimal import Decimal
from uuid import uuid4

import pytest

from domain.market_location import MarketLocation, MarketLocationValidationError


def valid_location(**overrides):
    data = {
        "market_id": uuid4(),
        "postal_code": "01001000",
        "street_name": "Praça da Sé",
        "street_number": "1",
        "city_name": "São Paulo",
        "state_code": "SP",
        "state_name": "São Paulo",
        "latitude": Decimal("-23.550520"),
        "longitude": Decimal("-46.633308"),
    }
    data.update(overrides)
    return MarketLocation(**data)


def test_location_requires_valid_brazil_coordinates():
    with pytest.raises(MarketLocationValidationError, match="latitude"):
        valid_location(latitude=Decimal("-99")).validate()


def test_location_normalizes_postal_code_and_exports_mercado_pago_location():
    location = valid_location(postal_code="01001-000")

    location.validate()

    assert location.postal_code == "01001000"
    assert location.to_mp_location() == {
        "street_number": "1",
        "street_name": "Praça da Sé",
        "city_name": "São Paulo",
        "state_name": "São Paulo",
        "latitude": -23.55052,
        "longitude": -46.633308,
    }


def test_location_rejects_missing_required_address_fields():
    with pytest.raises(MarketLocationValidationError, match="street_number"):
        valid_location(street_number="   ").validate()
