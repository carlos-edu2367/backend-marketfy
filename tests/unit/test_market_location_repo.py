# ruff: noqa: E402
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from decimal import Decimal

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest

from domain.market_location import MarketLocation
from infra.repositories.market_location_repo import MarketLocationRepository


@pytest.mark.asyncio
async def test_save_location_maps_domain_version_to_database_column():
    db = SimpleNamespace(
        add= lambda _model: None,
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    repository = MarketLocationRepository(db)
    repository.get_by_market = AsyncMock(return_value=None)
    location = MarketLocation(
        market_id=uuid4(), postal_code="01001000", street_name="Sé",
        street_number="1", city_name="São Paulo", state_code="SP",
        state_name="São Paulo", latitude=Decimal("-23.55"),
        longitude=Decimal("-46.63"), version=3,
    )

    model = await repository.save_location(location)

    assert model.location_version == 3
    assert model.market_id == location.market_id
    db.commit.assert_awaited_once()
