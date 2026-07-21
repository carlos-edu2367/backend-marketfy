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
os.environ.setdefault("DATABASE_URL", TEST_POSTGRES_URL or "postgresql://x:y@localhost/z")
os.environ.setdefault("SECRET_KEY", "test-secret-key-com-mais-de-32-caracteres-ok")

from domain.sales import SaleStatus


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def test_sale_status_has_awaiting_payment():
    assert SaleStatus.AWAITING_PAYMENT.value == "aguardando_pagamento"


@pytest.mark.asyncio
async def test_pix_attempt_tables_exist():
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
        payment_cols = await conn.run_sync(lambda c: [x["name"] for x in inspect(c).get_columns("payments")])
    await engine.dispose()
    assert "pix_payment_attempts" in tables
    assert "pix_status_queries" in tables
    assert "modality" in payment_cols
    assert "pix_attempt_id" in payment_cols
