import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
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


class PixPaymentModality(Enum):
    MANUAL = "manual"
    QR_DYNAMIC = "qr_dynamic"


class PixAttemptStatus(Enum):
    PENDING = "pending"
    IN_ANALYSIS = "in_analysis"
    CONFIRMATION_PENDING = "confirmation_pending"
    APPROVED = "approved"
    EXPIRED = "expired"
    CANCELED = "canceled"
    REJECTED = "rejected"
    ERROR = "error"
    DIVERGENT = "divergent"


@dataclass(frozen=True)
class PixItem:
    title: str
    unit_price: Decimal
    quantity: int
    unit_measure: str = "unit"
    external_code: Optional[str] = None


@dataclass(frozen=True)
class PixOrderResult:
    order_id: str
    external_status: str
    status_detail: Optional[str]
    mapped_status: Optional["PixAttemptStatus"]
    total_amount: Decimal
    currency: str
    qr_data: Optional[str]
    receiver_account_id: Optional[str]


# Mapeamento externo->interno. Fail-safe: status desconhecido -> None (nao avanca).
_ORDER_STATUS_MAP = {
    "created": PixAttemptStatus.PENDING,
    "processed": PixAttemptStatus.APPROVED,
    "canceled": PixAttemptStatus.CANCELED,
    "expired": PixAttemptStatus.EXPIRED,
    "refunded": None,  # tratado por conciliacao/estorno, fora do MVP
}


def map_order_status(status: str, status_detail: Optional[str] = None) -> Optional[PixAttemptStatus]:
    return _ORDER_STATUS_MAP.get(status, None)
