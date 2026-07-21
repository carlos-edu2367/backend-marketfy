# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import uuid
import pytest
from urllib.parse import urlparse, parse_qs


class FakeStateRepo:
    def __init__(self): self.saved = None

    async def create(self, **kwargs):
        self.saved = kwargs

        class M:
            pass

        m = M()
        m.expires_at = kwargs["expires_at"]
        return m


@pytest.mark.asyncio
async def test_build_authorization_url(monkeypatch):
    monkeypatch.setenv("MP_APP_ID", "app-1")
    monkeypatch.setenv("MP_OAUTH_REDIRECT_URI", "https://cb")
    monkeypatch.setenv("MP_SECRET_KEY", "k" * 32)
    from infra.config import settings as sm
    sm.get_settings.cache_clear()

    from application.services.pix.oauth_service import MercadoPagoOAuthService
    repo = FakeStateRepo()
    svc = MercadoPagoOAuthService(state_repo=repo)
    out = await svc.build_authorization(market_id=uuid.uuid4(), user_id=uuid.uuid4())

    q = parse_qs(urlparse(out["authorization_url"]).query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["app-1"]
    assert q["redirect_uri"] == ["https://cb"]
    assert q["code_challenge_method"] == ["S256"]
    assert "code_challenge" in q and "state" in q
    # code_verifier persistido cifrado (prefixo enc:), nunca em claro na URL
    assert repo.saved["code_verifier_ciphertext"].startswith("enc:")
