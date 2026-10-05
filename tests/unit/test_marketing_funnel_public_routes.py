from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def client():
    from infra.database.models import Base
    from infra.web.main import app
    from infra.web.routers import marketing_funnel as funnel_router
    from infra.security import rate_limiter as rl

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    import asyncio
    asyncio.run(_create_tables(engine, Base))

    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as session:
            yield session

    # rate limiter isolado por teste, pra nao vazar contagem entre testes
    rl.rate_limiter = rl.InMemoryRateLimiter()

    app.dependency_overrides[funnel_router.get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


async def _create_tables(engine, Base):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def test_post_event_returns_202(client):
    response = client.post(
        "/api/v1/marketing-funnel/events",
        json={
            "visitor_id": "v" * 10,
            "funnel_variant": "A",
            "event_name": "marketfy_funnel_start",
        },
    )
    assert response.status_code == 202


def test_post_lead_returns_201_with_body(client):
    response = client.post(
        "/api/v1/marketing-funnel/leads",
        json={
            "visitor_id": "v" * 10,
            "funnel_variant": "B",
            "name": "Ana",
            "phone": "11999990000",
            "answers": {"business": "Mercadinho"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Ana"
    assert body["funnel_variant"] == "B"


def test_post_lead_without_phone_or_email_returns_422(client):
    response = client.post(
        "/api/v1/marketing-funnel/leads",
        json={"visitor_id": "v" * 10, "funnel_variant": "A", "name": "Ana", "answers": {}},
    )
    assert response.status_code == 422


def test_post_lead_rate_limited_after_five_per_minute(client):
    payload = {
        "visitor_id": "v" * 10,
        "funnel_variant": "A",
        "name": "Ana",
        "phone": "11999990000",
        "answers": {},
    }
    for _ in range(5):
        response = client.post("/api/v1/marketing-funnel/leads", json=payload)
        assert response.status_code == 201

    response = client.post("/api/v1/marketing-funnel/leads", json=payload)
    assert response.status_code == 429
