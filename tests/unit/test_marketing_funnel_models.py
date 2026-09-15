from __future__ import annotations

import os
import sys
import uuid

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


@pytest.mark.asyncio
async def test_event_and_lead_models_round_trip():
    from infra.database.models import Base, MarketingFunnelEventModel, MarketingFunnelLeadModel

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    visitor_id = str(uuid.uuid4())

    async with Session() as session:
        event = MarketingFunnelEventModel(
            id=uuid.uuid4(),
            visitor_id=visitor_id,
            funnel_variant="A",
            event_name="marketfy_funnel_step_view",
            step="q3",
            properties={"answer": "Sim"},
        )
        lead = MarketingFunnelLeadModel(
            id=uuid.uuid4(),
            visitor_id=visitor_id,
            funnel_variant="A",
            name="Ana",
            phone="11999990000",
            email=None,
            city="São Paulo/SP",
            control_score=72,
            answers={"business": "Mercadinho de bairro"},
        )
        session.add_all([event, lead])
        await session.commit()

    async with Session() as session:
        reloaded_event = await session.get(MarketingFunnelEventModel, event.id)
        reloaded_lead = await session.get(MarketingFunnelLeadModel, lead.id)

        assert reloaded_event.visitor_id == visitor_id
        assert reloaded_event.step == "q3"
        assert reloaded_event.properties == {"answer": "Sim"}

        assert reloaded_lead.name == "Ana"
        assert reloaded_lead.control_score == 72
        assert reloaded_lead.answers == {"business": "Mercadinho de bairro"}
