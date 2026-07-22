# ruff: noqa: E402
import os
import sys
from uuid import uuid4

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest

from application.services.market_location_service import MarketLocationService
from domain.market_location import MarketLocationValidationError


VALID_LOCATION = {
    "postal_code": "01001-000",
    "street_name": "Praça da Sé",
    "street_number": "1",
    "district": "Sé",
    "city_name": "São Paulo",
    "state_code": "SP",
    "state_name": "São Paulo",
    "latitude": "-23.550520",
    "longitude": "-46.633308",
    "source": "manual",
}


class FakeLocationRepository:
    def __init__(self):
        self.locations = {}

    async def get_by_market(self, market_id):
        return self.locations.get(market_id)

    async def get_store_registration(self, market_id):
        return None

    async def save_location(self, location):
        self.locations[location.market_id] = location
        return location


@pytest.mark.asyncio
async def test_save_rejects_incomplete_location_before_repository_write():
    repo = FakeLocationRepository()
    service = MarketLocationService(repo)

    with pytest.raises(MarketLocationValidationError, match="street_number"):
        await service.save(uuid4(), {**VALID_LOCATION, "street_number": ""})

    assert repo.locations == {}


@pytest.mark.asyncio
async def test_save_rejects_missing_coordinates_as_validation_error():
    service = MarketLocationService(FakeLocationRepository())

    with pytest.raises(MarketLocationValidationError, match="latitude"):
        await service.save(uuid4(), {**VALID_LOCATION, "latitude": None})


@pytest.mark.asyncio
async def test_save_and_get_return_only_the_requested_market_location():
    repo = FakeLocationRepository()
    service = MarketLocationService(repo)
    market_a, market_b = uuid4(), uuid4()

    await service.save(market_a, VALID_LOCATION)

    result_a = await service.get(market_a)
    result_b = await service.get(market_b)

    assert result_a["status"] == "ready"
    assert result_a["market_id"] == str(market_a)
    assert result_b == {"status": "not_configured", "market_id": str(market_b)}
