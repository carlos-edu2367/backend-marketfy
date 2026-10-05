# ruff: noqa: E402
"""Requer TEST_POSTGRES_URL apontando para um Postgres descartável de teste."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[2]
app_dir = backend_dir / "app"
if str(app_dir) not in sys.path:
    sys.path.append(str(app_dir))

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _alembic(*args):
    env = {**os.environ, "DATABASE_URL": TEST_POSTGRES_URL}
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=backend_dir, env=env, check=True)


def test_migration_roundtrip():
    _alembic("upgrade", "head")
    _alembic("downgrade", "20260915_0026")
    _alembic("upgrade", "head")


@pytest.mark.asyncio
async def test_tables_and_partial_unique_index():
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
        assert {"funnels", "funnel_variants", "funnel_steps", "funnel_sessions", "funnel_events"} <= set(tables)

        admin_id, funnel_id, variant_id, session_id = (uuid.uuid4() for _ in range(4))
        await conn.execute(text(
            "INSERT INTO users (id, name, email, password_hash, role) VALUES (:id, 'a', :email, 'h', 'admin')"
        ), {"id": admin_id, "email": f"{admin_id}@t.com"})
        await conn.execute(text(
            "INSERT INTO funnels (id, slug, name, created_by) VALUES (:id, :slug, 'f', :admin)"
        ), {"id": funnel_id, "slug": f"pg-{funnel_id.hex[:8]}", "admin": admin_id})
        await conn.execute(text(
            "INSERT INTO funnel_variants (id, funnel_id, name) VALUES (:id, :f, 'A')"
        ), {"id": variant_id, "f": funnel_id})
        await conn.execute(text(
            "INSERT INTO funnel_sessions (id, funnel_id, variant_id) VALUES (:id, :f, :v)"
        ), {"id": session_id, "f": funnel_id, "v": variant_id})
        insert_event = text(
            "INSERT INTO funnel_events (id, session_id, funnel_id, variant_id, type, step_position) "
            "VALUES (:id, :s, :f, :v, :t, 0)"
        )
        params = {"s": session_id, "f": funnel_id, "v": variant_id}
        await conn.execute(insert_event, {"id": uuid.uuid4(), "t": "step_next", **params})
        await conn.execute(insert_event, {"id": uuid.uuid4(), "t": "step_next", **params})
        await conn.execute(insert_event, {"id": uuid.uuid4(), "t": "step_view", **params})
        with pytest.raises(IntegrityError):
            await conn.execute(insert_event, {"id": uuid.uuid4(), "t": "step_view", **params})
        await conn.rollback()
    await engine.dispose()
