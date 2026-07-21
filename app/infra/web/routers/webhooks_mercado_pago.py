from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from infra.config.logger import get_logger
from infra.config.settings import get_settings
from infra.database.setup import get_db

logger = get_logger("mp_webhook")
router = APIRouter()


def _parse_x_signature(header: str) -> tuple[Optional[str], Optional[str]]:
    """Parse a Mercado Pago `x-signature` header into its `ts` and `v1` parts.

    The header looks like `ts=1704908010000,v1=<hex-hmac>`. Unknown parts are
    ignored; missing parts yield `None` for that slot.
    """
    ts = v1 = None
    for part in (header or "").split(","):
        k, _, v = part.strip().partition("=")
        if k == "ts":
            ts = v
        elif k == "v1":
            v1 = v
    return ts, v1


def validate_mp_signature(
    *,
    x_signature: str,
    x_request_id: str,
    data_id: str,
    secret: str,
    now_ts: Optional[int] = None,
    max_skew: int = 300_000,
) -> bool:
    """Validate a Mercado Pago webhook's `x-signature` header.

    `now_ts` and `max_skew` are both expressed in **milliseconds** — Mercado
    Pago's `ts` value in the `x-signature` header is epoch milliseconds, not
    seconds. `max_skew` defaults to 300_000 ms (5 minutes).

    Per Mercado Pago's docs, `data.id` must be normalized to lowercase before
    it is used to build the HMAC manifest; this function does that
    internally so callers don't each have to remember to do it.

    Also per the docs: parts whose value is absent from the notification are
    omitted from the manifest entirely. Emitting `request-id:;` for a missing
    `x-request-id` header produces a different HMAC than the one Mercado Pago
    signed, which would reject a legitimate notification.
    """
    if not x_signature or not secret or not data_id:
        return False
    ts, v1 = _parse_x_signature(x_signature)
    if not ts or not v1:
        return False
    try:
        ts_int = int(ts)
    except ValueError:
        return False
    now = now_ts if now_ts is not None else int(time.time() * 1000)
    if abs(now - ts_int) > max_skew:
        return False
    parts = [f"id:{data_id.lower()}"]
    if x_request_id:
        parts.append(f"request-id:{x_request_id}")
    parts.append(f"ts:{ts}")
    manifest = ";".join(parts) + ";"
    expected = hmac.new(
        secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, v1)


@router.post("/mercado-pago")
async def mercado_pago_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}

    # `data.id` chega como query param na URL do webhook (ex.:
    # `POST /webhook?data.id=ORDX&type=order`), não apenas no corpo. A MP
    # documenta a query string como a fonte, então ela tem prioridade; o
    # corpo é apenas um fallback (ex.: chamadas de teste manuais). O valor
    # resolvido é normalizado para minúsculas uma única vez aqui e reusado
    # tanto na validação de assinatura quanto no payload passado ao
    # processor — que também normaliza internamente (Task 3), então
    # repassar um valor já em minúsculas é seguro e evita drift de casing.
    query_data_id = request.query_params.get("data.id") or ""
    body_data_id = str((payload.get("data") or {}).get("id") or "")
    if query_data_id and body_data_id and query_data_id.lower() != body_data_id.lower():
        logger.warning(
            "mp_webhook_data_id_mismatch",
            extra={"extra_data": {"query": query_data_id[:12], "body": body_data_id[:12]}},
        )
    data_id = (query_data_id or body_data_id).lower()

    x_sig = request.headers.get("x-signature", "")
    x_req = request.headers.get("x-request-id", "")
    if not validate_mp_signature(
        x_signature=x_sig, x_request_id=x_req, data_id=data_id,
        secret=settings.MP_WEBHOOK_SECRET,
    ):
        logger.warning("mp_webhook_invalid_signature")
        return Response(status_code=401)

    from application.services.pix.webhook_processor import MercadoPagoWebhookProcessor
    from infra.repositories.fiscal_repo import SQLAlchemyProviderWebhookEventRepository
    from infra.repositories.pix_repo import (
        PixPaymentAttemptRepository, MercadoPagoConnectionRepository,
    )
    from infra.web.dependencies import get_pix_payment_service

    payload.setdefault("data", {})
    if isinstance(payload.get("data"), dict):
        payload["data"]["id"] = data_id

    payment_service = get_pix_payment_service(db)
    processor = MercadoPagoWebhookProcessor(
        SQLAlchemyProviderWebhookEventRepository(db),
        PixPaymentAttemptRepository(db),
        MercadoPagoConnectionRepository(db),
        payment_service,
    )
    status = await processor.process(payload, raw, dict(request.headers))
    return Response(status_code=status)
