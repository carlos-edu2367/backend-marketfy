import uuid
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.models import MarketingFunnelEventModel, MarketingFunnelLeadModel

# Ordem fixa de etapas por funil, usada pelo painel admin para montar o funil
# de conversao. `step=None` significa que o evento nao carrega `step` (o
# proprio `event_name` identifica a etapa, ex. inicio, lead, oferta, cta).
FUNNEL_STEPS: Dict[str, List[Dict[str, Optional[str]]]] = {
    "A": [
        {"label": "Início", "event_name": "marketfy_funnel_start", "step": None},
        *[
            {"label": f"Pergunta {i}", "event_name": "marketfy_funnel_step_view", "step": f"q{i}"}
            for i in range(1, 12)
        ],
        {"label": "Lead enviado", "event_name": "marketfy_lead_submit", "step": None},
        {"label": "Oferta vista", "event_name": "marketfy_offer_view", "step": None},
        {"label": "CTA clicado", "event_name": "marketfy_offer_cta_click", "step": None},
    ],
    "B": [
        {"label": "Início", "event_name": "marketfy_funnel_start", "step": None},
        *[
            {"label": f"Cenário {i}", "event_name": "marketfy_funnel_step_view", "step": f"s{i}"}
            for i in range(1, 6)
        ],
        {"label": "Lead enviado", "event_name": "marketfy_lead_submit", "step": None},
        {"label": "Oferta vista", "event_name": "marketfy_offer_view", "step": None},
        {"label": "CTA clicado", "event_name": "marketfy_offer_cta_click", "step": None},
    ],
}


class MarketingFunnelRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_event(
        self,
        *,
        visitor_id: str,
        funnel_variant: str,
        event_name: str,
        step: Optional[str] = None,
        properties: Optional[dict] = None,
    ) -> MarketingFunnelEventModel:
        model = MarketingFunnelEventModel(
            id=uuid.uuid4(),
            visitor_id=visitor_id,
            funnel_variant=funnel_variant,
            event_name=event_name,
            step=step,
            properties=properties,
            created_at=datetime.utcnow(),
        )
        self.session.add(model)
        await self.session.commit()
        return model

    async def create_lead(
        self,
        *,
        visitor_id: str,
        funnel_variant: str,
        name: str,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        city: Optional[str] = None,
        control_score: Optional[int] = None,
        answers: Optional[dict] = None,
    ) -> MarketingFunnelLeadModel:
        model = MarketingFunnelLeadModel(
            id=uuid.uuid4(),
            visitor_id=visitor_id,
            funnel_variant=funnel_variant,
            name=name,
            phone=phone,
            email=email,
            city=city,
            control_score=control_score,
            answers=answers or {},
            created_at=datetime.utcnow(),
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        return model

    async def count_distinct_visitors(
        self, *, funnel_variant: str, event_name: str, step: Optional[str] = None
    ) -> int:
        query = select(func.count(func.distinct(MarketingFunnelEventModel.visitor_id))).where(
            MarketingFunnelEventModel.funnel_variant == funnel_variant,
            MarketingFunnelEventModel.event_name == event_name,
        )
        if step is not None:
            query = query.where(MarketingFunnelEventModel.step == step)
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def get_funnel_summary(self, funnel_variant: str) -> List[Dict]:
        steps = FUNNEL_STEPS[funnel_variant]
        counts = [
            await self.count_distinct_visitors(
                funnel_variant=funnel_variant, event_name=entry["event_name"], step=entry["step"]
            )
            for entry in steps
        ]
        first_count = counts[0] if counts and counts[0] > 0 else None

        summary = []
        for i, entry in enumerate(steps):
            count = counts[i]
            previous_count = counts[i - 1] if i > 0 else None
            conversion_pct = round((count / first_count) * 100, 1) if first_count else 0.0
            drop_off_pct = (
                round((1 - count / previous_count) * 100, 1)
                if previous_count and count <= previous_count
                else 0.0
            )
            summary.append(
                {
                    "label": entry["label"],
                    "count": count,
                    "conversion_pct": conversion_pct,
                    "drop_off_pct": drop_off_pct,
                }
            )
        return summary

    async def list_leads(
        self, *, funnel_variant: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> List[MarketingFunnelLeadModel]:
        query = (
            select(MarketingFunnelLeadModel)
            .order_by(MarketingFunnelLeadModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if funnel_variant is not None:
            query = query.where(MarketingFunnelLeadModel.funnel_variant == funnel_variant)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_leads(self, *, funnel_variant: Optional[str] = None) -> int:
        query = select(func.count(MarketingFunnelLeadModel.id))
        if funnel_variant is not None:
            query = query.where(MarketingFunnelLeadModel.funnel_variant == funnel_variant)
        result = await self.session.execute(query)
        return result.scalar() or 0
