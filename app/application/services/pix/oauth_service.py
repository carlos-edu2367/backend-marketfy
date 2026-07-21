"""Serviço de OAuth Mercado Pago — conexão por tenant (aplicação única)."""

from __future__ import annotations
import base64
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from infra.config.settings import get_settings
from infra.security.secret_cipher import SecretCipher

STATE_TTL_MINUTES = 10


class OAuthStateInvalidError(Exception):
    """State ausente, já usado ou expirado."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class MercadoPagoOAuthService:
    def __init__(self, state_repo, connection_repo=None, client=None):
        self.state_repo = state_repo
        self.connection_repo = connection_repo
        self.client = client
        self.settings = get_settings()
        self.cipher = SecretCipher(self.settings.MP_SECRET_KEY)

    async def build_authorization(self, market_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)  # 43-128 chars
        challenge = _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=STATE_TTL_MINUTES)

        await self.state_repo.create(
            state=state,
            market_id=market_id,
            initiated_by_user_id=user_id,
            code_verifier_ciphertext=self.cipher.encrypt(code_verifier),
            redirect_uri=self.settings.MP_OAUTH_REDIRECT_URI,
            expires_at=expires_at,
        )
        params = {
            "response_type": "code",
            "client_id": self.settings.MP_APP_ID,
            "redirect_uri": self.settings.MP_OAUTH_REDIRECT_URI,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = f"{self.settings.MP_AUTH_BASE_URL.rstrip('/')}/authorization?{urlencode(params)}"
        return {"authorization_url": url, "state_expires_at": expires_at}

    async def handle_callback(self, code: str, state: str):
        now = datetime.now(timezone.utc)
        row = await self.state_repo.consume(state, now)
        if row is None:
            raise OAuthStateInvalidError("State inválido, expirado ou já utilizado.")

        code_verifier = (
            self.cipher.decrypt(row.code_verifier_ciphertext)
            if row.code_verifier_ciphertext else None
        )
        creds = await self.client.exchange_code(
            code=code, redirect_uri=row.redirect_uri, code_verifier=code_verifier,
        )

        from infra.database.models import MercadoPagoConnectionModel

        conn = await self.connection_repo.get_by_market(row.market_id)
        if conn is None:
            conn = MercadoPagoConnectionModel(market_id=row.market_id)
        conn.status = "connected"
        conn.mp_user_id = creds.mp_user_id
        conn.scopes = creds.scope
        conn.access_token_ciphertext = self.cipher.encrypt(creds.access_token)
        conn.refresh_token_ciphertext = (
            self.cipher.encrypt(creds.refresh_token) if creds.refresh_token else None
        )
        conn.access_token_expires_at = now + timedelta(seconds=creds.expires_in)
        conn.connected_at = now
        conn.last_refreshed_at = now
        conn.last_error = None
        return await self.connection_repo.save(conn)
