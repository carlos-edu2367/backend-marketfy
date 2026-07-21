# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import uuid
import pytest


def _clear():
    from infra.config import settings as sm
    sm.get_settings.cache_clear()


class StateRow:
    def __init__(self, market_id, verifier_ct, redirect_uri):
        self.market_id = market_id
        self.code_verifier_ciphertext = verifier_ct
        self.redirect_uri = redirect_uri


class FakeStateRepo:
    def __init__(self, row):
        self._row = row
        self.consumed = False

    async def consume(self, state, now):
        if self.consumed:
            return None
        self.consumed = True
        return self._row


class FakeConnRepo:
    def __init__(self):
        self.saved = None

    async def get_by_market(self, market_id):
        return None

    async def save(self, model, commit=True):
        self.saved = model
        return model


class FakeClient:
    async def exchange_code(self, *, code, redirect_uri, code_verifier=None):
        from domain.pix import PixCredentials
        assert code == "CODE" and code_verifier == "VERIFIER"
        return PixCredentials(access_token="AT", refresh_token="RT",
                              expires_in=15552000, mp_user_id="42", scope="offline_access read write")


@pytest.mark.asyncio
async def test_handle_callback_persists_encrypted_connection(monkeypatch):
    monkeypatch.setenv("MP_SECRET_KEY", "k" * 32)
    _clear()

    market_id = uuid.uuid4()
    from infra.security.secret_cipher import SecretCipher
    verifier_ct = SecretCipher("k" * 32).encrypt("VERIFIER")
    state_repo = FakeStateRepo(StateRow(market_id, verifier_ct, "https://cb"))
    conn_repo = FakeConnRepo()
    from application.services.pix.oauth_service import MercadoPagoOAuthService
    svc = MercadoPagoOAuthService(state_repo=state_repo, connection_repo=conn_repo, client=FakeClient())

    conn = await svc.handle_callback(code="CODE", state="ST")
    assert conn.status == "connected"
    assert conn.mp_user_id == "42"
    assert conn.access_token_ciphertext.startswith("enc:")
    assert conn.refresh_token_ciphertext.startswith("enc:")
    # token nunca em claro
    assert "AT" not in conn.access_token_ciphertext


@pytest.mark.asyncio
async def test_handle_callback_rejects_used_state(monkeypatch):
    monkeypatch.setenv("MP_SECRET_KEY", "k" * 32)
    _clear()
    state_repo = FakeStateRepo(None)  # consume retorna None
    from application.services.pix.oauth_service import (
        MercadoPagoOAuthService, OAuthStateInvalidError,
    )
    svc = MercadoPagoOAuthService(state_repo=state_repo, connection_repo=FakeConnRepo(), client=FakeClient())
    with pytest.raises(OAuthStateInvalidError):
        await svc.handle_callback(code="CODE", state="ST")
