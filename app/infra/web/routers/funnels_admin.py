"""Rotas admin de funis de venda — somente `require_admin`."""
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from application.dtos_funnels import (
    FunnelCreateRequest, FunnelUpdateRequest, StepCreateRequest, StepOrderRequest, StepUpdateRequest,
    VariantCreateRequest, VariantUpdateRequest,
)
from application.services.funnel_admin_service import UNSET, FunnelAdminService
from application.services.funnel_reporting import funnel_metrics_report, list_funnels_with_kpis
from domain.funnels import FunnelError, FunnelNotFound
from infra.database.setup import get_db
from infra.observability.audit import record_audit_event
from infra.repositories.funnel_repo import FunnelRepository
from infra.web.dependencies import get_audit_service, require_admin
from infra.web.routers.funnels_public import funnel_http_error

router = APIRouter()


def _svc(db: AsyncSession) -> FunnelAdminService:
    return FunnelAdminService(FunnelRepository(db))


def _step(s):
    return {"id": s.id, "position": s.position, "name": s.name, "html": s.html}


def _variant(v, steps=None):
    data = {"id": v.id, "name": v.name, "weight": v.weight, "is_active": v.is_active, "position": v.position}
    if steps is not None:
        data["steps"] = [_step(s) for s in steps]
    return data


def _detail(d):
    f = d["funnel"]
    return {
        "funnel": {"id": f.id, "slug": f.slug, "name": f.name, "status": f.status, "plan_id": f.plan_id,
                   "tracking_html": f.tracking_html, "created_at": f.created_at, "updated_at": f.updated_at},
        "variants": [_variant(x["variant"], x["steps"]) for x in d["variants"]],
        "session_count": d["session_count"],
    }


async def _run(db, coro):
    try:
        result = await coro
    except FunnelError as exc:
        await db.rollback()
        raise funnel_http_error(exc)
    await db.commit()
    return result


async def _audit(audit, request, admin, action, funnel_id, metadata=None):
    await record_audit_event(audit, request, actor=admin, action=action, resource_type="funnel",
                             resource_id=str(funnel_id), result="success", metadata=metadata or {})


@router.get("")
async def list_funnels(db: AsyncSession = Depends(get_db), admin=Depends(require_admin)):
    return await list_funnels_with_kpis(db)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_funnel(body: FunnelCreateRequest, request: Request, db: AsyncSession = Depends(get_db),
                        admin=Depends(require_admin), audit=Depends(get_audit_service)):
    svc = _svc(db)
    funnel = await _run(db, svc.create_funnel(name=body.name, slug=body.slug, plan_id=body.plan_id, created_by=admin.id))
    await _audit(audit, request, admin, "funnel.created", funnel.id)
    return _detail(await svc.get_detail(funnel.id))


@router.get("/variants/{variant_id}/preview")
async def preview_variant(variant_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin=Depends(require_admin)):
    repo = FunnelRepository(db)
    variant = await repo.get_variant(variant_id)
    if variant is None:
        raise funnel_http_error(FunnelNotFound("Variante"))
    funnel = await repo.get_funnel(variant.funnel_id)
    return {"funnel": {"name": funnel.name, "plan_id": funnel.plan_id}, "tracking_html": funnel.tracking_html or "",
            "steps": [{"position": s.position, "name": s.name, "html": s.html} for s in await repo.list_steps(variant.id)]}


@router.get("/{funnel_id}")
async def get_funnel(funnel_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin=Depends(require_admin)):
    try:
        return _detail(await _svc(db).get_detail(funnel_id))
    except FunnelError as exc:
        raise funnel_http_error(exc)


@router.patch("/{funnel_id}")
async def update_funnel(funnel_id: uuid.UUID, body: FunnelUpdateRequest, db: AsyncSession = Depends(get_db),
                        admin=Depends(require_admin)):
    svc, sent = _svc(db), body.model_fields_set
    await _run(db, svc.update_funnel(
        funnel_id, name=body.name, slug=body.slug,
        plan_id=body.plan_id if "plan_id" in sent else UNSET,
        tracking_html=body.tracking_html if "tracking_html" in sent else UNSET,
    ))
    return _detail(await svc.get_detail(funnel_id))


@router.post("/{funnel_id}/duplicate", status_code=status.HTTP_201_CREATED)
async def duplicate_funnel(funnel_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db),
                           admin=Depends(require_admin), audit=Depends(get_audit_service)):
    svc = _svc(db)
    copy = await _run(db, svc.duplicate_funnel(funnel_id, created_by=admin.id))
    await _audit(audit, request, admin, "funnel.duplicated", copy.id, {"source_id": str(funnel_id)})
    return _detail(await svc.get_detail(copy.id))


async def _transition(action, funnel_id, request, db, admin, audit):
    svc = _svc(db)
    await _run(db, getattr(svc, action)(funnel_id))
    await _audit(audit, request, admin, f"funnel.{action}", funnel_id)
    return _detail(await svc.get_detail(funnel_id))


@router.post("/{funnel_id}/publish")
async def publish(funnel_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db),
                  admin=Depends(require_admin), audit=Depends(get_audit_service)):
    return await _transition("publish", funnel_id, request, db, admin, audit)


@router.post("/{funnel_id}/unpublish")
async def unpublish(funnel_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db),
                    admin=Depends(require_admin), audit=Depends(get_audit_service)):
    return await _transition("unpublish", funnel_id, request, db, admin, audit)


@router.post("/{funnel_id}/archive")
async def archive(funnel_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db),
                  admin=Depends(require_admin), audit=Depends(get_audit_service)):
    return await _transition("archive", funnel_id, request, db, admin, audit)


@router.post("/{funnel_id}/variants", status_code=status.HTTP_201_CREATED)
async def add_variant(funnel_id: uuid.UUID, body: VariantCreateRequest, request: Request,
                      db: AsyncSession = Depends(get_db), admin=Depends(require_admin), audit=Depends(get_audit_service)):
    variant, warnings = await _run(db, _svc(db).add_variant(funnel_id, name=body.name, weight=body.weight))
    await _audit(audit, request, admin, "funnel.variant_added", funnel_id, {"weight": body.weight})
    return {"variant": _variant(variant, []), "warnings": warnings}


@router.patch("/variants/{variant_id}")
async def update_variant(variant_id: uuid.UUID, body: VariantUpdateRequest, request: Request,
                         db: AsyncSession = Depends(get_db), admin=Depends(require_admin), audit=Depends(get_audit_service)):
    variant, warnings = await _run(db, _svc(db).update_variant(
        variant_id, name=body.name, weight=body.weight, is_active=body.is_active))
    if body.weight is not None or body.is_active is not None:
        await _audit(audit, request, admin, "funnel.variant_weights_changed", variant.funnel_id,
                     {"variant_id": str(variant_id), "weight": variant.weight, "is_active": variant.is_active})
    return {"variant": _variant(variant), "warnings": warnings}


@router.delete("/variants/{variant_id}")
async def delete_variant(variant_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin=Depends(require_admin)):
    return {"warnings": await _run(db, _svc(db).delete_variant(variant_id))}


@router.post("/variants/{variant_id}/duplicate", status_code=status.HTTP_201_CREATED)
async def duplicate_variant(variant_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin=Depends(require_admin)):
    svc = _svc(db)
    variant, warnings = await _run(db, svc.duplicate_variant(variant_id))
    return {"variant": _variant(variant, await svc._repo.list_steps(variant.id)), "warnings": warnings}


@router.post("/variants/{variant_id}/steps", status_code=status.HTTP_201_CREATED)
async def add_step(variant_id: uuid.UUID, body: StepCreateRequest, db: AsyncSession = Depends(get_db),
                   admin=Depends(require_admin)):
    step, warnings = await _run(db, _svc(db).add_step(variant_id, name=body.name, html=body.html))
    return {"step": _step(step), "warnings": warnings}


@router.put("/variants/{variant_id}/steps/order")
async def reorder_steps(variant_id: uuid.UUID, body: StepOrderRequest, db: AsyncSession = Depends(get_db),
                        admin=Depends(require_admin)):
    return {"warnings": await _run(db, _svc(db).reorder_steps(variant_id, body.step_ids))}


@router.patch("/steps/{step_id}")
async def update_step(step_id: uuid.UUID, body: StepUpdateRequest, db: AsyncSession = Depends(get_db),
                      admin=Depends(require_admin)):
    step, warnings = await _run(db, _svc(db).update_step(step_id, name=body.name, html=body.html))
    return {"step": _step(step), "warnings": warnings}


@router.delete("/steps/{step_id}")
async def delete_step(step_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin=Depends(require_admin)):
    return {"warnings": await _run(db, _svc(db).delete_step(step_id))}


@router.get("/{funnel_id}/metrics")
async def funnel_metrics(
    funnel_id: uuid.UUID,
    from_: Optional[date] = Query(default=None, alias="from"),
    to: Optional[date] = Query(default=None),
    variant_id: Optional[uuid.UUID] = None,
    utm_source: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    try:
        return await funnel_metrics_report(db, funnel_id, from_=from_, to=to, variant_id=variant_id,
                                           utm_source=utm_source, utm_campaign=utm_campaign)
    except FunnelError as exc:
        raise funnel_http_error(exc)
