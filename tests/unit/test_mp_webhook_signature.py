from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.web.routers.webhooks_mercado_pago import validate_mp_signature

SECRET = "whsec-test"


def _sign(data_id, req_id, ts):
    # Mercado Pago signs using data.id lowercased; mirror that here so the
    # fixture behaves like a real MP-issued signature. Per the official docs,
    # parts whose value is absent from the notification are omitted from the
    # manifest entirely (not sent as an empty value).
    parts = []
    if data_id:
        parts.append(f"id:{data_id.lower()}")
    if req_id:
        parts.append(f"request-id:{req_id}")
    parts.append(f"ts:{ts}")
    manifest = ";".join(parts) + ";"
    v1 = hmac.new(SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts},v1={v1}"


def test_valid_signature_without_request_id():
    """MP docs: omit `request-id` from the manifest when the header is absent.

    Building `request-id:;` instead would reject a legitimate notification.
    """
    ts = int(time.time() * 1000)
    header = _sign("ORD1", "", ts)
    assert validate_mp_signature(
        x_signature=header,
        x_request_id="",
        data_id="ORD1",
        secret=SECRET,
        now_ts=ts,
    ) is True


def test_valid_signature():
    ts = int(time.time() * 1000)
    header = _sign("ORD1", "req-1", ts)
    assert validate_mp_signature(
        x_signature=header,
        x_request_id="req-1",
        data_id="ORD1",
        secret=SECRET,
        now_ts=ts,
    ) is True


def test_tampered_signature_rejected():
    ts = int(time.time() * 1000)
    header = _sign("ORD1", "req-1", ts)
    assert validate_mp_signature(
        x_signature=header,
        x_request_id="req-1",
        data_id="ORD_OTHER",
        secret=SECRET,
        now_ts=ts,
    ) is False


def test_replay_outside_window_rejected():
    # max_skew default is 300_000 ms (5 minutes); push well past it.
    ts = int(time.time() * 1000) - 400_000
    header = _sign("ORD1", "req-1", ts)
    assert validate_mp_signature(
        x_signature=header,
        x_request_id="req-1",
        data_id="ORD1",
        secret=SECRET,
        now_ts=int(time.time() * 1000),
    ) is False


def test_data_id_case_insensitive():
    # Mercado Pago requires data.id to be lowercased before building the
    # manifest; the signature is built with a lowercase id but the caller
    # passes it with mixed case (e.g. straight from the query string).
    ts = int(time.time() * 1000)
    header = _sign("ord1", "req-1", ts)
    assert validate_mp_signature(
        x_signature=header,
        x_request_id="req-1",
        data_id="ORD1",
        secret=SECRET,
        now_ts=ts,
    ) is True
