# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import uuid
import pytest
from datetime import datetime, timezone, timedelta

from domain.pix import PixCredentials
from infra.database.models import MercadoPagoConnectionModel


def _clear():
    from infra.config import settings as sm
    sm.get_settings.cache_clear()


class FakeConnRepo:
    def __init__(self, conn):
        self.conn = conn

    async def get_by_market(self, market_id):
        return self.conn

    async def save(self, model, commit=True):
        self.conn = model
        return model


class FakeLock:
    async def acquire(self, key, ttl):
        return True

    async def release(self, key):
        return None


class FakeClientOK:
    async def refresh_credentials(self, *, refresh_token):
        assert refresh_token == "OLD_RT"
        return PixCredentials(access_token="NEW_AT", refresh_token="NEW_RT",
                              expires_in=15552000, mp_user_id="42", scope="offline_access")


class FakeClientFail:
    async def refresh_credentials(self, *, refresh_token):
        from infra.clients.mercadopago_client import MercadoPagoAuthError
        raise MercadoPagoAuthError("invalid", status_code=400)


def _conn(cipher, expires_at):
    c = MercadoPagoConnectionModel(market_id=uuid.uuid4(), status="connected")
    c.access_token_ciphertext = cipher.encrypt("OLD_AT")
    c.refresh_token_ciphertext = cipher.encrypt("OLD_RT")
    c.access_token_expires_at = expires_at
    return c


@pytest.mark.asyncio
async def test_returns_current_token_when_not_expiring(monkeypatch):
    monkeypatch.setenv("MP_SECRET_KEY", "k" * 32)
    _clear()
    from infra.security.secret_cipher import SecretCipher
    from application.services.pix.connection_service import MercadoPagoConnectionService
    cipher = SecretCipher("k" * 32)
    now = datetime.now(timezone.utc)
    conn = _conn(cipher, now + timedelta(days=100))
    svc = MercadoPagoConnectionService(FakeConnRepo(conn), FakeClientOK(), FakeLock())
    token = await svc.get_valid_access_token(conn.market_id)
    assert token == "OLD_AT"


@pytest.mark.asyncio
async def test_refreshes_and_rotates_when_expiring(monkeypatch):
    monkeypatch.setenv("MP_SECRET_KEY", "k" * 32)
    _clear()
    from infra.security.secret_cipher import SecretCipher
    from application.services.pix.connection_service import MercadoPagoConnectionService
    cipher = SecretCipher("k" * 32)
    now = datetime.now(timezone.utc)
    conn = _conn(cipher, now + timedelta(hours=1))
    repo = FakeConnRepo(conn)
    svc = MercadoPagoConnectionService(repo, FakeClientOK(), FakeLock())
    token = await svc.get_valid_access_token(conn.market_id)
    assert token == "NEW_AT"
    assert cipher.decrypt(repo.conn.refresh_token_ciphertext) == "NEW_RT"  # rotacionado


@pytest.mark.asyncio
async def test_refresh_failure_marks_reauthorization(monkeypatch):
    monkeypatch.setenv("MP_SECRET_KEY", "k" * 32)
    _clear()
    from infra.security.secret_cipher import SecretCipher
    from application.services.pix.connection_service import (
        MercadoPagoConnectionService, ReauthorizationRequiredError,
    )
    cipher = SecretCipher("k" * 32)
    now = datetime.now(timezone.utc)
    conn = _conn(cipher, now + timedelta(hours=1))
    repo = FakeConnRepo(conn)
    svc = MercadoPagoConnectionService(repo, FakeClientFail(), FakeLock())
    with pytest.raises(ReauthorizationRequiredError):
        await svc.get_valid_access_token(conn.market_id)
    assert repo.conn.status == "reauthorization_required"
