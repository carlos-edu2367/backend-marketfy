"""Endpoints Pix (Mercado Pago) — conexão OAuth por tenant."""

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.setup import get_db
from infra.config.settings import get_settings
from infra.config.logger import get_logger
from infra.web.dependencies import get_current_user, require_market_access
from infra.security.market_access import MarketPermission
from infra.repositories.pix_repo import (
    MercadoPagoConnectionRepository, MercadoPagoOAuthStateRepository,
)
from infra.clients.mercadopago_client import MercadoPagoClient, MercadoPagoError
from application.services.pix.oauth_service import (
    MercadoPagoOAuthService, OAuthStateInvalidError,
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
