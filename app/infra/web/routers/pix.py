"""Endpoints Pix (Mercado Pago) — conexão OAuth por tenant."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.setup import get_db
from infra.config.settings import get_settings
from infra.config.logger import get_logger
from infra.web.dependencies import get_current_user, require_market_access, get_pix_payment_service
from infra.security.market_access import MarketPermission
from infra.security.rate_limiter import enforce_pix_verify_rate_limit
from infra.repositories.pix_repo import (
    MercadoPagoConnectionRepository, MercadoPagoOAuthStateRepository, PixPaymentAttemptRepository,
)
from infra.clients.mercadopago_client import MercadoPagoClient, MercadoPagoError
from application.services.pix.oauth_service import (
    MercadoPagoOAuthService, OAuthStateInvalidError,
)
from application.services.pix.payment_service import (
    PixNotConnectedError, PixActiveAttemptError, PixBoxClosedError, PixInvalidItemsError,
    PixLocationNotConfiguredError,
)

router = APIRouter()
logger = get_logger("pix_router")


def _mask(value: str | None, keep: int = 4) -> str | None:
    if not value:
        return value
    return value[:keep] + "***" + value[-keep:] if len(value) > keep * 2 else "***"


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
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
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
        await svc.handle_callback(code=code, state=state)
    except OAuthStateInvalidError:
        return RedirectResponse(f"{front}/settings?pix_oauth=error")
    except MercadoPagoError:
        logger.warning("pix_oauth_callback_provider_error")
        return RedirectResponse(f"{front}/settings?pix_oauth=error")
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
    }


@router.delete("/{market_id}/oauth")
async def disconnect(
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
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
    return {"status": "not_connected"}


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
async def create_qr(market_id: uuid.UUID, payload: dict,
                    current_user=Depends(get_current_user),
                    svc=Depends(get_pix_payment_service),
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
        raise HTTPException(status_code=409, detail={"code": "pix.location_not_configured"})
    except PixBoxClosedError:
        raise HTTPException(status_code=409, detail={"code": "pix.box_closed"})
    except (PixInvalidItemsError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
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
                         market=Depends(require_market_access(MarketPermission.SALES_WRITE))):
    settings = get_settings()
    await enforce_pix_verify_rate_limit(request, attempt_id=str(attempt_id), sale_id=str(attempt_id),
        user_id=str(current_user.id), market_id=str(market_id),
        cooldown_seconds=settings.MP_VALIDATE_COOLDOWN_SECONDS)
    attempt = await svc.verify(market_id=market_id, attempt_id=attempt_id)
    return {"attempt_id": str(attempt.id), "status": attempt.status,
            "sale_completed": attempt.status == "approved", "amount": f"{attempt.amount:.2f}"}


@router.post("/{market_id}/attempts/{attempt_id}/cancel")
async def cancel_attempt(market_id: uuid.UUID, attempt_id: uuid.UUID,
                         svc=Depends(get_pix_payment_service),
                         market=Depends(require_market_access(MarketPermission.SALES_WRITE))):
    attempt = await svc.cancel(market_id=market_id, attempt_id=attempt_id)
    return {"attempt_id": str(attempt.id), "status": attempt.status,
            "sale_completed": attempt.status == "approved"}
