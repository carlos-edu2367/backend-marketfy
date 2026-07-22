# ruff: noqa: E402
import os
import sys
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest

from application.services.pix.payment_service import PixLocationNotConfiguredError
from infra.providers.pix.location import DatabasePosLocationProvider


class FakeRepo:
    def __init__(self, model=None): self.model = model

    async def get_by_market(self, market_id): return self.model


@pytest.mark.asyncio
async def test_database_provider_exports_only_validated_location():
    market_id = uuid4()
    model = SimpleNamespace(
        market_id=market_id, postal_code="01001000", street_name="Rua A",
        street_number="1", district=None, complement=None, city_name="São Paulo",
        state_code="SP", state_name="São Paulo", country_code="BR",
        latitude=Decimal("-23.5"), longitude=Decimal("-46.6"), source="manual",
        location_version=1,
    )

    result = await DatabasePosLocationProvider(FakeRepo(model)).get_location(market_id)

    assert result == {
        "street_number": "1", "street_name": "Rua A", "city_name": "São Paulo",
        "state_name": "São Paulo", "latitude": -23.5, "longitude": -46.6,
    }


@pytest.mark.asyncio
async def test_database_provider_fails_closed_when_market_has_no_location():
    with pytest.raises(PixLocationNotConfiguredError):
        await DatabasePosLocationProvider(FakeRepo()).get_location(uuid4())
