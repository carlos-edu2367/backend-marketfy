"""Endpoints Pix (Mercado Pago) — conexão OAuth por tenant."""

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from infra.cache.pix_event_bus import PixEventBus

from infra.database.setup import get_db
from infra.config.settings import get_settings
from infra.config.logger import get_logger
from infra.web.dependencies import (
    get_current_user, require_market_access, get_pix_payment_service, get_audit_service,
)
from infra.security.market_access import MarketPermission
from infra.security.rate_limiter import (
    enforce_address_lookup_rate_limit,
    enforce_pix_verify_rate_limit,
)
from infra.observability.audit import record_audit_event
from infra.observability.metrics import metrics_registry
from application.services.audit_service import AuditService
from infra.repositories.pix_repo import (
    MercadoPagoConnectionRepository, MercadoPagoOAuthStateRepository, PixPaymentAttemptRepository,
)
from infra.repositories.market_location_repo import MarketLocationRepository
from infra.clients.mercadopago_client import MercadoPagoClient, MercadoPagoError
from application.services.pix.oauth_service import (
    MercadoPagoOAuthService, OAuthStateInvalidError,
)
from application.services.pix.payment_service import (
    PixNotConnectedError, PixActiveAttemptError, PixBoxClosedError, PixInvalidItemsError,
    PixLocationNotConfiguredError, PixAttemptNotFoundError,
)
from application.services.market_location_service import MarketLocationService
from domain.market_location import MarketLocationValidationError
from infra.providers.cep.viacep import ViaCepProvider

router = APIRouter()
logger = get_logger("pix_router")


def _mask(value: str | None, keep: int = 4) -> str | None:
    if not value:
        return value
    return value[:keep] + "***" + value[-keep:] if len(value) > keep * 2 else "***"


@router.get("/address/cep/{postal_code}")
async def lookup_cep(
    postal_code: str,
    request: Request,
    current_user=Depends(get_current_user),
):
    await enforce_address_lookup_rate_limit(request, user_id=current_user.id)
    result = await ViaCepProvider().lookup(postal_code)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "pix.cep_not_found"})
    return result.public_dict()


@router.get("/{market_id}/location")
async def get_location(
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    market=Depends(require_market_access(MarketPermission.PAYMENTS_READ)),
):
    return await MarketLocationService(MarketLocationRepository(db)).get(market_id)


@router.put("/{market_id}/location")
async def update_location(
    market_id: uuid.UUID,
    payload: dict,
    request: Request,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.PAYMENTS_WRITE)),
):
    try:
        result = await MarketLocationService(MarketLocationRepository(db)).save(market_id, payload)
    except MarketLocationValidationError as exc:
        metrics_registry.record_pix_location_event("location_validation_failed")
        raise HTTPException(status_code=422, detail={"code": "pix.location_invalid", "field": str(exc)})
    await record_audit_event(
        audit, request, actor=current_user, action="pix.location.updated",
        resource_type="market_location", resource_id=str(market_id), result="success",
        market_id=market_id, metadata={"location_version": result["location_version"]},
    )
    return result


@router.post("/{market_id}/oauth/authorize")
async def authorize(
    market_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    market=Depends(require_market_access(MarketPermission.PAYMENTS_WRITE)),
):
    svc = MercadoPagoOAuthService(state_repo=MercadoPagoOAuthStateRepository(db))
    return await svc.build_authorization(market_id=market_id, user_id=current_user.id)


@router.get("/oauth/callback")
async def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
):
    settings = get_settings()
    front = (settings.PUBLIC_FRONTEND_URL or "").rstrip("/")
    if error == "access_denied":
        return RedirectResponse(f"{front}/settings?pix_oauth=denied")
    if not code or not state:
        return RedirectResponse(f"{front}/settings?pix_oauth=error")

    svc = MercadoPagoOAuthService(
        state_repo=MercadoPagoOAuthStateRepository(db),
        connection_repo=MercadoPagoConnectionRepository(db),
        client=MercadoPagoClient(),
    )
    try:
        conn = await svc.handle_callback(code=code, state=state)
    except OAuthStateInvalidError:
        return RedirectResponse(f"{front}/settings?pix_oauth=error")
    except MercadoPagoError:
        logger.warning("pix_oauth_callback_provider_error")
        return RedirectResponse(f"{front}/settings?pix_oauth=error")
    await record_audit_event(
        audit, request, actor=None, action="pix.oauth.connected",
        resource_type="pix_connection", resource_id=str(conn.id),
        result="success", market_id=conn.market_id, metadata={},
    )
    return RedirectResponse(f"{front}/settings?pix_oauth=success")


@router.get("/{market_id}/oauth/status")
async def oauth_status(
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    market=Depends(require_market_access(MarketPermission.PAYMENTS_READ)),
):
    conn = await MercadoPagoConnectionRepository(db).get_by_market(market_id)
    if conn is None:
        return {"status": "not_connected"}
    return {
        "status": conn.status,
        "mp_user_id_masked": _mask(conn.mp_user_id),
        "mp_nickname": conn.mp_nickname,
        "mp_email_masked": conn.mp_email_masked,
        "pix_enabled": conn.pix_enabled,
        "scopes": conn.scopes,
        "connected_at": conn.connected_at,
        "last_refreshed_at": conn.last_refreshed_at,
        "access_token_expires_at": conn.access_token_expires_at,
        "last_validated_at": conn.last_validated_at,
        "last_error": conn.last_error,
        # O PDV deriva a disponibilidade do QR destes campos (ver
        # pages/pdv/pixAvailability.js). Sem eles o operador habilita no
        # painel e o botão de QR nunca aparece no caixa.
        "enabled_in_pdv": conn.enabled_in_pdv,
        "fees_acknowledged": conn.fees_acknowledged_at is not None,
        "allowed_terminal_ids": conn.allowed_terminal_ids,
    }


@router.post("/{market_id}/oauth/test")
async def oauth_test(
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    market=Depends(require_market_access(MarketPermission.PAYMENTS_WRITE)),
):
    """Valida a conexão com uma chamada leve autenticada e grava o resultado."""
    from datetime import datetime, timezone
    from application.services.pix.connection_service import (
        MercadoPagoConnectionService, ConnectionNotReadyError, ReauthorizationRequiredError,
    )
    from infra.cache.redis_lock import RedisLock

    repo = MercadoPagoConnectionRepository(db)
    conn = await repo.get_by_market(market_id)
    if conn is None:
        raise HTTPException(status_code=404, detail={"code": "pix.not_connected"})

    service = MercadoPagoConnectionService(repo, MercadoPagoClient(), RedisLock())
    try:
        access_token = await service.get_valid_access_token(market_id)
        await MercadoPagoClient().get_user_me(access_token=access_token)
    except (ConnectionNotReadyError, ReauthorizationRequiredError) as exc:
        logger.warning("pix_oauth_test_needs_reauth")
        return {"ok": False, "status": "reauthorization_required", "detail": str(exc)}
    except MercadoPagoError:
        # Nunca ecoar o corpo do provider: pode conter dados da conta.
        logger.warning("pix_oauth_test_provider_error")
        return {"ok": False, "status": conn.status, "detail": "Falha ao validar a conexão."}

    now = datetime.now(timezone.utc)
    conn.last_validated_at = now
    conn.last_error = None
    await repo.save(conn)
    return {"ok": True, "status": conn.status, "checked_at": now.isoformat()}


@router.delete("/{market_id}/oauth")
async def disconnect(
    market_id: uuid.UUID,
    request: Request,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.PAYMENTS_WRITE)),
):
    repo = MercadoPagoConnectionRepository(db)
    conn = await repo.get_by_market(market_id)
    if conn is None:
        return {"status": "not_connected"}
    conn.status = "not_connected"
    conn.access_token_ciphertext = None
    conn.refresh_token_ciphertext = None
    conn.access_token_expires_at = None
    await repo.save(conn)
    await record_audit_event(
        audit, request, actor=current_user, action="pix.oauth.disconnected",
        resource_type="pix_connection", resource_id=str(conn.id),
        result="success", market_id=market_id, metadata={},
    )
    return {"status": "not_connected"}


@router.put("/{market_id}/settings")
async def update_settings(
    market_id: uuid.UUID,
    payload: dict,
    request: Request,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.PAYMENTS_WRITE)),
):
    from datetime import datetime, timezone

    repo = MercadoPagoConnectionRepository(db)
    conn = await repo.get_by_market(market_id)
    if conn is None:
        raise HTTPException(status_code=404, detail={"code": "pix.not_connected"})

    enabled_in_pdv = payload.get("enabled_in_pdv", conn.enabled_in_pdv)
    fees_acknowledged = payload.get("fees_acknowledged")

    if fees_acknowledged is True:
        conn.fees_acknowledged_at = datetime.now(timezone.utc)
    elif fees_acknowledged is False:
        conn.fees_acknowledged_at = None

    if enabled_in_pdv and conn.fees_acknowledged_at is None:
        raise HTTPException(status_code=422, detail={"code": "pix.fees_acknowledgement_required"})

    conn.enabled_in_pdv = bool(enabled_in_pdv)
    if "allowed_terminal_ids" in payload:
        conn.allowed_terminal_ids = payload["allowed_terminal_ids"]
    if "expiration" in payload:
        conn.expiration_override = payload["expiration"]

    await repo.save(conn)
    await record_audit_event(
        audit, request, actor=current_user, action="pix.settings.updated",
        resource_type="pix_connection", resource_id=str(conn.id),
        result="success", market_id=market_id, metadata={"enabled_in_pdv": conn.enabled_in_pdv},
    )
    return {
        "enabled_in_pdv": conn.enabled_in_pdv,
        "allowed_terminal_ids": conn.allowed_terminal_ids,
        "expiration": conn.expiration_override,
        "fees_acknowledged_at": conn.fees_acknowledged_at,
    }


def _attempt_response(a, include_qr=False):
    body = {"attempt_id": str(a.id), "sale_id": str(a.sale_id), "status": a.status,
            "amount": f"{a.amount:.2f}", "currency": a.currency, "order_id": a.order_id,
            "attempt_number": a.attempt_number,
            "expires_at": a.expires_at.isoformat() if a.expires_at else None,
            "stream_url": f"/api/v1/pix/{a.market_id}/attempts/{a.id}/events"}
    if include_qr:
        body["qr_data"] = a.qr_data
    return body


@router.post("/{market_id}/qr", status_code=201)
async def create_qr(market_id: uuid.UUID, payload: dict, request: Request,
                    current_user=Depends(get_current_user),
                    svc=Depends(get_pix_payment_service),
                    audit: AuditService = Depends(get_audit_service),
                    market=Depends(require_market_access(MarketPermission.SALES_WRITE))):
    try:
        attempt = await svc.create_qr(market_id=market_id,
            terminal_id=uuid.UUID(payload["terminal_id"]), box_id=uuid.UUID(payload["box_id"]),
            operator_id=current_user.id, items=payload.get("items", []))
    except PixActiveAttemptError as exc:
        raise HTTPException(status_code=409, detail={"code": "pix.active_attempt_exists",
                                                     "attempt_id": str(exc.attempt.id)})
    except PixNotConnectedError:
        raise HTTPException(status_code=409, detail={"code": "pix.not_connected"})
    except PixLocationNotConfiguredError:
        metrics_registry.record_pix_location_event("location_missing")
        raise HTTPException(status_code=409, detail={"code": "pix.location_not_configured"})
    except PixBoxClosedError:
        raise HTTPException(status_code=409, detail={"code": "pix.box_closed"})
    except (PixInvalidItemsError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await record_audit_event(
        audit, request, actor=current_user, action="pix.qr.created",
        resource_type="pix_attempt", resource_id=str(attempt.id),
        result="success", market_id=market_id, metadata={"box_id": str(attempt.box_id)},
    )
    return _attempt_response(attempt, include_qr=True)


@router.get("/{market_id}/attempts/{attempt_id}")
async def get_attempt(market_id: uuid.UUID, attempt_id: uuid.UUID,
                      db: AsyncSession = Depends(get_db),
                      market=Depends(require_market_access(MarketPermission.SALES_READ))):
    a = await PixPaymentAttemptRepository(db).get_by_id_for_update(attempt_id, market_id)
    if a is None:
        raise HTTPException(status_code=404, detail={"code": "pix.attempt_not_found"})
    return _attempt_response(a)


@router.post("/{market_id}/attempts/{attempt_id}/verify")
async def verify_attempt(market_id: uuid.UUID, attempt_id: uuid.UUID, request: Request,
                         current_user=Depends(get_current_user),
                         svc=Depends(get_pix_payment_service),
                         audit: AuditService = Depends(get_audit_service),
                         market=Depends(require_market_access(MarketPermission.SALES_WRITE))):
    settings = get_settings()
    await enforce_pix_verify_rate_limit(request, attempt_id=str(attempt_id), sale_id=str(attempt_id),
        user_id=str(current_user.id), market_id=str(market_id),
        cooldown_seconds=settings.MP_VALIDATE_COOLDOWN_SECONDS)
    try:
        attempt = await svc.verify(market_id=market_id, attempt_id=attempt_id)
    except PixAttemptNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "pix.attempt_not_found"})
    if attempt.status == "approved":
        await record_audit_event(
            audit, request, actor=current_user, action="pix.payment.confirmed",
            resource_type="pix_attempt", resource_id=str(attempt.id),
            result="success", market_id=market_id, metadata={},
        )
    return {"attempt_id": str(attempt.id), "status": attempt.status,
            "sale_completed": attempt.status == "approved", "amount": f"{attempt.amount:.2f}"}


@router.post("/{market_id}/attempts/{attempt_id}/cancel")
async def cancel_attempt(market_id: uuid.UUID, attempt_id: uuid.UUID, request: Request,
                         current_user=Depends(get_current_user),
                         svc=Depends(get_pix_payment_service),
                         audit: AuditService = Depends(get_audit_service),
                         market=Depends(require_market_access(MarketPermission.SALES_WRITE))):
    try:
        attempt = await svc.cancel(market_id=market_id, attempt_id=attempt_id)
    except PixAttemptNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "pix.attempt_not_found"})
    if attempt.status == "canceled":
        await record_audit_event(
            audit, request, actor=current_user, action="pix.attempt.canceled",
            resource_type="pix_attempt", resource_id=str(attempt.id),
            result="success", market_id=market_id, metadata={},
        )
    return {"attempt_id": str(attempt.id), "status": attempt.status,
            "sale_completed": attempt.status == "approved"}


_SSE_STATUS_EVENT = {
    "pending": "payment.pending", "approved": "payment.approved",
    "expired": "payment.expired", "canceled": "payment.cancelled",
    "divergent": "payment.error", "confirmation_pending": "payment.confirmation_pending",
}
_SSE_FINAL_EVENTS = {"payment.approved", "payment.expired", "payment.cancelled"}
_SSE_HEARTBEAT_SECONDS = 15


@router.get("/{market_id}/attempts/{attempt_id}/events")
async def attempt_events(market_id: uuid.UUID, attempt_id: uuid.UUID,
                         db: AsyncSession = Depends(get_db),
                         market=Depends(require_market_access(MarketPermission.SALES_READ))):
    attempt = await PixPaymentAttemptRepository(db).get_by_id(attempt_id, market_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail={"code": "pix.attempt_not_found"})

    current_status = attempt.status
    current_event = _SSE_STATUS_EVENT.get(current_status, "payment.pending")
    current_data = {"attempt_id": str(attempt.id), "status": current_status,
                    "sale_id": str(attempt.sale_id) if attempt.sale_id else None,
                    "sale_completed": current_status == "approved"}

    bus = PixEventBus()

    async def event_stream():
        seq = 0

        def frame(event, data):
            nonlocal seq
            seq += 1
            return f"id: {seq}\nevent: {event}\ndata: {json.dumps(data)}\n\n"

        # 1. estado atual imediato
        yield frame(current_event, current_data)
        if current_event in _SSE_FINAL_EVENTS:
            return
        # 2. assina o bus. `bus.subscribe` cede um tick `None` a cada ~1s sem
        # mensagem (ver pix_event_bus.py) — contamos ticks ociosos para emitir
        # `: ping` a cada ~15s sem precisar cancelar/dar timeout no generator
        # (fazer isso destruiria a assinatura Redis via o `finally` do subscribe).
        idle_ticks = 0
        subscription = bus.subscribe(str(attempt_id))
        try:
            async for evt in subscription:
                if evt is None:
                    idle_ticks += 1
                    if idle_ticks >= _SSE_HEARTBEAT_SECONDS:
                        idle_ticks = 0
                        yield ": ping\n\n"
                    continue
                idle_ticks = 0
                yield frame(evt["event"], evt["data"])
                if evt["event"] in _SSE_FINAL_EVENTS:
                    break
        except asyncio.CancelledError:
            return
        finally:
            await subscription.aclose()

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
