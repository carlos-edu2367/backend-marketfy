from __future__ import annotations

import os
import sys
import uuid
from types import SimpleNamespace

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def client_and_session():
    from infra.database.models import Base
    from infra.web.main import app
    from infra.web.routers import marketing_funnel as funnel_router

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    import asyncio
    asyncio.run(_create_tables(engine, Base))

    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as session:
            yield session

    admin_user = SimpleNamespace(id=uuid.uuid4(), role="admin")
    app.dependency_overrides[funnel_router.get_db] = _override_get_db
    app.dependency_overrides[funnel_router.require_admin] = lambda: admin_user
    try:
        yield TestClient(app), Session
    finally:
        app.dependency_overrides.clear()


async def _create_tables(engine, Base):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed(Session):
    from infra.repositories.marketing_funnel_repo import MarketingFunnelRepository

    async with Session() as session:
        repo = MarketingFunnelRepository(session)
        await repo.create_event(visitor_id="v1", funnel_variant="A", event_name="marketfy_funnel_start")
        await repo.create_event(visitor_id="v2", funnel_variant="A", event_name="marketfy_funnel_start")
        await repo.create_event(visitor_id="v1", funnel_variant="A", event_name="marketfy_lead_submit")
        await repo.create_event(visitor_id="v1", funnel_variant="A", event_name="marketfy_offer_cta_click")
        await repo.create_lead(
            visitor_id="v1", funnel_variant="A", name="Ana", phone="11999990000", answers={},
        )


def test_summary_returns_both_variants_with_counts(client_and_session):
    import asyncio

    client, Session = client_and_session
    asyncio.run(_seed(Session))

    response = client.get("/api/v1/admin/marketing-funnel/summary")
    assert response.status_code == 200
    body = response.json()

    variants = {item["funnel_variant"]: item for item in body}
    assert variants["A"]["lead_count"] == 1
    assert variants["A"]["cta_click_count"] == 1
    assert variants["A"]["lead_to_cta_rate"] == 100.0

    start_step = next(s for s in variants["A"]["steps"] if s["label"] == "Início")
    assert start_step["count"] == 2

    assert variants["B"]["lead_count"] == 0


def test_leads_endpoint_filters_by_variant(client_and_session):
    import asyncio

    client, Session = client_and_session
    asyncio.run(_seed(Session))

    response = client.get("/api/v1/admin/marketing-funnel/leads", params={"variant": "A"})
    assert response.status_code == 200
    leads = response.json()
    assert len(leads) == 1
    assert leads[0]["name"] == "Ana"

    response_b = client.get("/api/v1/admin/marketing-funnel/leads", params={"variant": "B"})
    assert response_b.json() == []
