"""Serviço de conexão Mercado Pago — token válido com renovação segura."""

from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone

from infra.clients.mercadopago_client import MercadoPagoAuthError
from infra.config.settings import get_settings
from infra.security.secret_cipher import SecretCipher
from infra.observability.metrics import metrics_registry

REFRESH_MARGIN_SECONDS = 24 * 3600


class ConnectionNotReadyError(Exception):
    """Sem conexão connected para o tenant."""


class ReauthorizationRequiredError(Exception):
    """Refresh falhou; tenant precisa reconectar."""


class MercadoPagoConnectionService:
    def __init__(self, connection_repo, client, lock, pos_repo=None):
        self.repo = connection_repo
        self.client = client
        self.lock = lock
        self.pos_repo = pos_repo
        self.settings = get_settings()
        self.cipher = SecretCipher(self.settings.MP_SECRET_KEY)

    async def get_valid_access_token(self, market_id: uuid.UUID) -> str:
        conn = await self.repo.get_by_market(market_id)
        if conn is None or conn.status not in ("connected", "refresh_required"):
            raise ConnectionNotReadyError("Integração Mercado Pago não conectada.")

        now = datetime.now(timezone.utc)
        expires_at = conn.access_token_expires_at
        expiring = expires_at is None or expires_at <= now + timedelta(seconds=REFRESH_MARGIN_SECONDS)
        if not expiring:
            return self.cipher.decrypt(conn.access_token_ciphertext)

        lock_key = f"pix:refresh:{market_id}"
        got = await self.lock.acquire(lock_key, ttl=30)
        try:
            # Re-lê após o lock: outro worker pode ter renovado enquanto esperávamos.
            conn = await self.repo.get_by_market(market_id)
            if conn.access_token_expires_at and conn.access_token_expires_at > now + timedelta(seconds=REFRESH_MARGIN_SECONDS):
                return self.cipher.decrypt(conn.access_token_ciphertext)

            refresh_token = (
                self.cipher.decrypt(conn.refresh_token_ciphertext)
                if conn.refresh_token_ciphertext else None
            )
            if not refresh_token:
                conn.status = "reauthorization_required"
                await self.repo.save(conn)
                metrics_registry.record_pix_token_refresh(result="fail")
                raise ReauthorizationRequiredError("Sem refresh token; reconecte a conta.")
            try:
                creds = await self.client.refresh_credentials(refresh_token=refresh_token)
            except MercadoPagoAuthError as exc:
                conn.status = "reauthorization_required"
                conn.last_error = "refresh_rejected"
                await self.repo.save(conn)
                metrics_registry.record_pix_token_refresh(result="fail")
                raise ReauthorizationRequiredError("Renovação recusada; reconecte a conta.") from exc

            conn.access_token_ciphertext = self.cipher.encrypt(creds.access_token)
            if creds.refresh_token:  # o MP rotaciona o refresh a cada renovação
                conn.refresh_token_ciphertext = self.cipher.encrypt(creds.refresh_token)
            conn.access_token_expires_at = now + timedelta(seconds=creds.expires_in)
            conn.last_refreshed_at = now
            conn.status = "connected"
            conn.last_error = None
            await self.repo.save(conn)
            metrics_registry.record_pix_token_refresh(result="ok")
            return creds.access_token
        finally:
            if got:
                await self.lock.release(lock_key)

    async def ensure_pos_registered(self, *, market_id, terminal_id, access_token,
                                    market_name, location, mp_user_id) -> str:
        existing = await self.pos_repo.get_by_market_and_terminal(market_id, terminal_id)
        if existing:
            return existing.mp_pos_external_id

        store_id = await self.pos_repo.get_store_id_for_market(market_id)
        if not store_id:
            store = await self.client.create_store(
                access_token=access_token, user_id=mp_user_id, name=market_name,
                external_id=str(market_id).replace("-", "")[:60], location=location,
            )
            store_id = str(store["id"])

        pos_external_id = f"T{str(terminal_id).replace('-', '')[:38]}"  # <=40 chars
        pos = await self.client.create_pos(
            access_token=access_token, name=f"Caixa {terminal_id}", store_id=store_id,
            external_store_id=str(market_id).replace("-", "")[:60], external_id=pos_external_id,
        )
        from infra.database.models import MercadoPagoPosRegistrationModel
        registration = MercadoPagoPosRegistrationModel(
            market_id=market_id, terminal_id=terminal_id,
            mp_store_id=store_id, mp_pos_external_id=pos["external_id"],
        )
        await self.pos_repo.save(registration)
        return registration.mp_pos_external_id
