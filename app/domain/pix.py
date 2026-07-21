import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from domain.shared import Entity


class MercadoPagoConnectionStatus(Enum):
    NOT_CONNECTED = "not_connected"
    PENDING = "pending"
    CONNECTED = "connected"
    REFRESH_REQUIRED = "refresh_required"
    REAUTHORIZATION_REQUIRED = "reauthorization_required"
    REVOKED = "revoked"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass(frozen=True)
class PixCredentials:
    access_token: str
    refresh_token: Optional[str]
    expires_in: int
    mp_user_id: str
    scope: Optional[str] = None


@dataclass
class MercadoPagoConnection(Entity):
    market_id: uuid.UUID
    provider: str = "mercado_pago"
    status: MercadoPagoConnectionStatus = MercadoPagoConnectionStatus.NOT_CONNECTED

    mp_user_id: Optional[str] = None
    mp_nickname: Optional[str] = None
    mp_email_masked: Optional[str] = None
    scopes: Optional[str] = None
    pix_enabled: Optional[bool] = None

    access_token_ciphertext: Optional[str] = None
    refresh_token_ciphertext: Optional[str] = None
    access_token_expires_at: Optional[datetime] = None

    connected_at: Optional[datetime] = None
    last_refreshed_at: Optional[datetime] = None
    last_validated_at: Optional[datetime] = None
    last_error: Optional[str] = None

    def is_token_expiring(self, now: datetime, margin_seconds: int) -> bool:
        if self.access_token_expires_at is None:
            return True
        return self.access_token_expires_at <= now + timedelta(seconds=margin_seconds)
