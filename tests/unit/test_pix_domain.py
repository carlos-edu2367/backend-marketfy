# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import uuid
from datetime import datetime, timedelta, timezone

from domain.pix import (
    MercadoPagoConnection, MercadoPagoConnectionStatus, PixCredentials,
)


def test_connection_defaults_not_connected():
    c = MercadoPagoConnection(market_id=uuid.uuid4())
    assert c.status is MercadoPagoConnectionStatus.NOT_CONNECTED
    assert c.access_token_ciphertext is None


def test_is_token_expiring_true_when_close_to_expiry():
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    c = MercadoPagoConnection(
        market_id=uuid.uuid4(),
        access_token_expires_at=now + timedelta(hours=1),
    )
    assert c.is_token_expiring(now, margin_seconds=24 * 3600) is True
    assert c.is_token_expiring(now, margin_seconds=60) is False


def test_pix_credentials_is_frozen():
    creds = PixCredentials(access_token="a", refresh_token="r", expires_in=100,
                           mp_user_id="1", scope="offline_access")
    assert creds.mp_user_id == "1"
