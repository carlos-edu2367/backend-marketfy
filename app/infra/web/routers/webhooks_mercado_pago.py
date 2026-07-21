from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional


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
    manifest = f"id:{data_id.lower()};request-id:{x_request_id};ts:{ts};"
    expected = hmac.new(
        secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, v1)
