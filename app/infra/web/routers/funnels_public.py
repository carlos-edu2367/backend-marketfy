"""Endpoints públicos dos funis de venda (visitante anônimo) e claim autenticado."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from application.dtos_funnels import FunnelEventRequest, FunnelSessionRequest
from application.services.funnel_public_service import UTM_KEYS, FunnelGone, FunnelPublicService
from domain.funnels import FunnelError, FunnelNotFound
from infra.database.setup import get_db
from infra.repositories.funnel_repo import FunnelRepository
from infra.security.rate_limiter import enforce_rate_limit_async
from infra.web.dependencies import get_current_user, get_funnel_attribution_service

router = APIRouter()


def funnel_http_error(exc: FunnelError) -> HTTPException:
    if isinstance(exc, FunnelNotFound):
        return HTTPException(status_code=404, detail={"code": exc.code, "message": exc.message})
    if isinstance(exc, FunnelGone):
        return HTTPException(status_code=410, detail={"code": exc.code, "message": exc.message})
    detail = {"code": exc.code, "message": exc.message}
    if getattr(exc, "errors", None):
        detail["errors"] = exc.errors
    return HTTPException(status_code=422, detail=detail)


@router.post("/public/{slug}/session")
async def start_session(slug: str, body: FunnelSessionRequest, request: Request, db: AsyncSession = Depends(get_db)):
    await enforce_rate_limit_async(request, bucket="funnel_session", limit=30, window_seconds=60)
    svc = FunnelPublicService(FunnelRepository(db))
    try:
        data = await svc.start_session(
            slug, fsid=body.fsid, utm={k: getattr(body, k) for k in UTM_KEYS}, referrer=body.referrer,
        )
    except FunnelError as exc:
        raise funnel_http_error(exc)
    await db.commit()
    return data


@router.post("/public/events", status_code=status.HTTP_204_NO_CONTENT)
async def record_event(body: FunnelEventRequest, request: Request, db: AsyncSession = Depends(get_db)):
    await enforce_rate_limit_async(request, bucket="funnel_events", limit=120, window_seconds=60)
    await enforce_rate_limit_async(request, bucket=f"funnel_events_fsid:{body.fsid}", limit=60, window_seconds=60)
    svc = FunnelPublicService(FunnelRepository(db))
    try:
        await svc.record_event(body.fsid, body.type, body.step_position)
    except FunnelError as exc:
        raise funnel_http_error(exc)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sessions/{fsid}/claim")
async def claim_session(fsid: uuid.UUID, current_user=Depends(get_current_user),
                        attribution=Depends(get_funnel_attribution_service)):
    return {"linked": await attribution.claim(fsid, current_user.id)}
