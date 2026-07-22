# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest

import infra.security.rate_limiter as rate_limiter


@pytest.mark.asyncio
async def test_address_lookup_rate_limit_uses_user_bucket(monkeypatch):
    calls = []

    async def fake_limit(request, bucket, limit, window_seconds):
        calls.append((bucket, limit, window_seconds))

    monkeypatch.setattr(rate_limiter, "enforce_rate_limit_async", fake_limit)

    await rate_limiter.enforce_address_lookup_rate_limit(object(), user_id="user-1")

    assert calls == [("pix_address:user-1", 30, 60)]
