from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from application.dtos import (
    MarketingFunnelEventCreateDTO,
    MarketingFunnelLeadCreateDTO,
    MarketingFunnelLeadResponseDTO,
    MarketingFunnelSummaryDTO,
)
from domain.identity import User
from infra.database.setup import get_db
from infra.repositories.marketing_funnel_repo import MarketingFunnelRepository
from infra.security.rate_limiter import enforce_rate_limit_async
from infra.web.dependencies import require_admin

router_public = APIRouter()
router_admin = APIRouter()


@router_public.post("/events", status_code=status.HTTP_202_ACCEPTED)
async def create_funnel_event(
    dto: MarketingFunnelEventCreateDTO,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit_async(request, bucket="marketing_funnel_event", limit=60, window_seconds=60)
    repo = MarketingFunnelRepository(db)
    await repo.create_event(
        visitor_id=dto.visitor_id,
        funnel_variant=dto.funnel_variant,
        event_name=dto.event_name,
        step=dto.step,
        properties=dto.properties,
    )
    return None


@router_public.post(
    "/leads", response_model=MarketingFunnelLeadResponseDTO, status_code=status.HTTP_201_CREATED
)
async def create_funnel_lead(
    dto: MarketingFunnelLeadCreateDTO,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit_async(request, bucket="marketing_funnel_lead", limit=5, window_seconds=60)
    repo = MarketingFunnelRepository(db)
    return await repo.create_lead(
        visitor_id=dto.visitor_id,
        funnel_variant=dto.funnel_variant,
        name=dto.name,
        phone=dto.phone,
        email=dto.email,
        city=dto.city,
        control_score=dto.control_score,
        answers=dto.answers,
    )


@router_admin.get("/marketing-funnel/summary", response_model=List[MarketingFunnelSummaryDTO])
async def get_marketing_funnel_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    repo = MarketingFunnelRepository(db)
    result = []
    for variant in ("A", "B"):
        steps = await repo.get_funnel_summary(variant)
        lead_count = next((s["count"] for s in steps if s["label"] == "Lead enviado"), 0)
        cta_count = next((s["count"] for s in steps if s["label"] == "CTA clicado"), 0)
        lead_to_cta_rate = round((cta_count / lead_count) * 100, 1) if lead_count else 0.0
        result.append(
            MarketingFunnelSummaryDTO(
                funnel_variant=variant,
                steps=steps,
                lead_count=lead_count,
                cta_click_count=cta_count,
                lead_to_cta_rate=lead_to_cta_rate,
            )
        )
    return result


@router_admin.get("/marketing-funnel/leads", response_model=List[MarketingFunnelLeadResponseDTO])
async def list_marketing_funnel_leads(
    variant: Optional[str] = Query(None, pattern="^(A|B)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    repo = MarketingFunnelRepository(db)
    return await repo.list_leads(funnel_variant=variant, limit=limit, offset=offset)
