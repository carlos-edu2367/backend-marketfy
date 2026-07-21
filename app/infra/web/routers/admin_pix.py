"""Rotas admin de conciliação Pix (Mercado Pago) — somente `require_admin`."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from application.services.audit_service import AuditService
from infra.database.setup import get_db
from infra.web.dependencies import require_admin, get_pix_payment_service, get_audit_service
from infra.repositories.pix_repo import PixPaymentAttemptRepository, MercadoPagoConnectionRepository
from infra.observability.audit import record_audit_event

router = APIRouter()


@router.get("/pix/reconciliation")
async def reconciliation(db: AsyncSession = Depends(get_db), admin=Depends(require_admin)):
    repo = PixPaymentAttemptRepository(db)
    conn_repo = MercadoPagoConnectionRepository(db)
    return {
        "paid_not_completed": await repo.count_paid_not_completed(),
        "completed_not_confirmed": await repo.count_completed_not_confirmed(),
        "divergent": await repo.count_by_status("divergent"),
        "stale_pending": await repo.count_by_status("pending"),
        "reauthorization_required": await conn_repo.count_by_status("reauthorization_required"),
    }


@router.post("/pix/attempts/{attempt_id}/reprocess")
async def reprocess(attempt_id: uuid.UUID, request: Request,
                    db: AsyncSession = Depends(get_db), admin=Depends(require_admin),
                    svc=Depends(get_pix_payment_service), audit: AuditService = Depends(get_audit_service)):
    repo = PixPaymentAttemptRepository(db)
    attempt = await repo.get_by_id_any_market(attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail={"code": "pix.attempt_not_found"})
    result = await svc.verify(market_id=attempt.market_id, attempt_id=attempt.id,
                              source="reconciliation")
    await record_audit_event(audit, request, actor=admin, action="pix.reconcile.reprocessed",
                             resource_type="pix_attempt", resource_id=str(attempt.id),
                             result="success", market_id=attempt.market_id, metadata={})
    return {"attempt_id": str(result.id), "status": result.status}
