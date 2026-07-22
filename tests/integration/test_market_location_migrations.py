# ruff: noqa: E402
"""Requer TEST_POSTGRES_URL apontando para um Postgres descartável de teste."""
import os
import sys
from pathlib import Path

app_dir = Path(__file__).resolve().parents[2] / "app"
if str(app_dir) not in sys.path:
    sys.path.append(str(app_dir))

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.mark.asyncio
async def test_market_location_and_store_registration_tables_exist():
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
        location_columns = await conn.run_sync(
            lambda c: {column["name"] for column in inspect(c).get_columns("market_locations")}
        )
    await engine.dispose()

    assert "market_locations" in tables
    assert "mercado_pago_store_registrations" in tables
    assert {"market_id", "postal_code", "latitude", "longitude", "location_version"} <= location_columns
