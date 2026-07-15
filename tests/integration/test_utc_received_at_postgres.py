"""Optional real-PostgreSQL proof for the fiscal receipt audit timestamp."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def pg_pool():
    dsn = os.getenv("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN não configurado para teste PostgreSQL de received_at")
    import asyncpg

    pool = await asyncpg.create_pool(dsn)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_received_at_round_trips_as_aware_utc_timestamptz(pg_pool):
    """The production sales column must preserve an aware UTC server receipt time."""
    owner_id, market_id, terminal_id, box_id, sale_id = (uuid.uuid4() for _ in range(5))
    received_at = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    client_claimed_at = received_at.replace(tzinfo=None)

    async with pg_pool.acquire() as connection:
        transaction = connection.transaction()
        await transaction.start()
        try:
            column_type = await connection.fetchval(
                """SELECT data_type FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'sales' AND column_name = 'received_at'"""
            )
            assert column_type == "timestamp with time zone"
            await connection.execute(
                "INSERT INTO users (id, name, email, password_hash, role, is_active) VALUES ($1, $2, $3, $4, $5, true)",
                owner_id, "UTC Owner", f"{owner_id}@example.test", "hash", "owner",
            )
            await connection.execute(
                "INSERT INTO markets (id, owner_id, name, document, address, is_active) VALUES ($1, $2, $3, $4, $5, true)",
                market_id, owner_id, "UTC Market", str(uuid.uuid4()).replace("-", "")[:14], "Rua UTC",
            )
            await connection.execute(
                "INSERT INTO terminals (id, market_id, name, active) VALUES ($1, $2, $3, true)",
                terminal_id, market_id, "PDV UTC",
            )
            await connection.execute(
                """INSERT INTO boxes (id, market_id, terminal_id, operator_id, status, initial_balance, current_balance)
                   VALUES ($1, $2, $3, $4, 'aberto', 0, 0)""",
                box_id, market_id, terminal_id, owner_id,
            )
            persisted = await connection.fetchval(
                """INSERT INTO sales (id, market_id, box_id, operator_id, status, total_amount, created_at, received_at)
                   VALUES ($1, $2, $3, $4, 'concluida', 1.00, $5, $6)
                   RETURNING received_at""",
                sale_id, market_id, box_id, owner_id, client_claimed_at, received_at,
            )
            assert persisted == received_at
            assert persisted.tzinfo is not None
            assert persisted.utcoffset().total_seconds() == 0
        finally:
            await transaction.rollback()
