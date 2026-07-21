# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from fastapi import HTTPException


class FakeReq:
    class client: host = "1.1.1.1"
    headers = {}


@pytest.mark.asyncio
async def test_second_verify_within_cooldown_is_blocked(monkeypatch):
    from infra.config import settings as sm
    sm.get_settings.cache_clear()
    from infra.security import rate_limiter as rl
    # força backend em memória p/ teste determinístico
    monkeypatch.setattr(rl, "rate_limiter", rl.InMemoryRateLimiter())
    from infra.security.rate_limiter import enforce_pix_verify_rate_limit
    kw = dict(attempt_id="a1", sale_id="s1", user_id="u1", market_id="m1", cooldown_seconds=5)
    await enforce_pix_verify_rate_limit(FakeReq(), **kw)  # 1a passa
    with pytest.raises(HTTPException) as exc:
        await enforce_pix_verify_rate_limit(FakeReq(), **kw)  # 2a bloqueia
    assert exc.value.status_code == 429
