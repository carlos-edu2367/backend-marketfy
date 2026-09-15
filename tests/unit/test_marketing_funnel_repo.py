from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


async def _make_session():
    from infra.database.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return Session()


@pytest.mark.asyncio
async def test_create_event_and_count_distinct_visitors():
    from infra.repositories.marketing_funnel_repo import MarketingFunnelRepository

    async with await _make_session() as session:
        repo = MarketingFunnelRepository(session)

        await repo.create_event(visitor_id="v1", funnel_variant="A", event_name="marketfy_funnel_start")
        await repo.create_event(visitor_id="v1", funnel_variant="A", event_name="marketfy_funnel_start")  # mesmo visitante, não deve dobrar a contagem
        await repo.create_event(visitor_id="v2", funnel_variant="A", event_name="marketfy_funnel_start")
        await repo.create_event(visitor_id="v3", funnel_variant="B", event_name="marketfy_funnel_start")

        count_a = await repo.count_distinct_visitors(funnel_variant="A", event_name="marketfy_funnel_start")
        count_b = await repo.count_distinct_visitors(funnel_variant="B", event_name="marketfy_funnel_start")

        assert count_a == 2
        assert count_b == 1


@pytest.mark.asyncio
async def test_count_distinct_visitors_filters_by_step():
    from infra.repositories.marketing_funnel_repo import MarketingFunnelRepository

    async with await _make_session() as session:
        repo = MarketingFunnelRepository(session)

        await repo.create_event(visitor_id="v1", funnel_variant="A", event_name="marketfy_funnel_step_view", step="q1")
        await repo.create_event(visitor_id="v2", funnel_variant="A", event_name="marketfy_funnel_step_view", step="q1")
        await repo.create_event(visitor_id="v1", funnel_variant="A", event_name="marketfy_funnel_step_view", step="q2")

        count_q1 = await repo.count_distinct_visitors(funnel_variant="A", event_name="marketfy_funnel_step_view", step="q1")
        count_q2 = await repo.count_distinct_visitors(funnel_variant="A", event_name="marketfy_funnel_step_view", step="q2")

        assert count_q1 == 2
        assert count_q2 == 1


@pytest.mark.asyncio
async def test_get_funnel_summary_computes_conversion_and_drop_off():
    from infra.repositories.marketing_funnel_repo import MarketingFunnelRepository

    async with await _make_session() as session:
        repo = MarketingFunnelRepository(session)

        # 4 visitantes iniciam o funil B, 2 chegam no cenário 1, 1 vira lead
        for v in ("v1", "v2", "v3", "v4"):
            await repo.create_event(visitor_id=v, funnel_variant="B", event_name="marketfy_funnel_start")
        for v in ("v1", "v2"):
            await repo.create_event(visitor_id=v, funnel_variant="B", event_name="marketfy_funnel_step_view", step="s1")
        for v in ("v1", "v2", "v3", "v4", "v5"):
            await repo.create_event(visitor_id=v, funnel_variant="B", event_name="marketfy_funnel_step_view", step="s2")

        summary = await repo.get_funnel_summary("B")

        start_step = next(s for s in summary if s["label"] == "Início")
        s1_step = next(s for s in summary if s["label"] == "Cenário 1")
        s2_step = next(s for s in summary if s["label"] == "Cenário 2")

        assert start_step["count"] == 4
        assert start_step["conversion_pct"] == 100.0
        assert start_step["drop_off_pct"] == 0.0

        assert s1_step["count"] == 2
        assert s1_step["conversion_pct"] == 50.0
        assert s1_step["drop_off_pct"] == 50.0

        # step com mais visitantes que a etapa anterior (v5 nunca passou por "Início"/"s1")
        # não gera drop-off negativo — fica em 0.0
        assert s2_step["count"] == 5
        assert s2_step["drop_off_pct"] == 0.0


@pytest.mark.asyncio
async def test_create_lead_and_list_leads():
    from infra.repositories.marketing_funnel_repo import MarketingFunnelRepository

    async with await _make_session() as session:
        repo = MarketingFunnelRepository(session)

        await repo.create_lead(
            visitor_id="v1", funnel_variant="A", name="Ana", phone="11999990000",
            email=None, city="São Paulo/SP", control_score=70, answers={"business": "Mercadinho"},
        )
        await repo.create_lead(
            visitor_id="v2", funnel_variant="B", name="Beto", phone=None,
            email="beto@x.com", city=None, control_score=40, answers={},
        )

        all_leads = await repo.list_leads()
        only_a = await repo.list_leads(funnel_variant="A")

        assert len(all_leads) == 2
        assert len(only_a) == 1
        assert only_a[0].name == "Ana"
        assert await repo.count_leads() == 2
        assert await repo.count_leads(funnel_variant="B") == 1
