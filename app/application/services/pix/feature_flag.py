"""Feature flag em camadas para o Pix QR: env (kill switch) -> tenant -> caixa."""
from __future__ import annotations


class PixFeatureDisabledError(Exception):
    """Pix QR não habilitado para este ambiente/tenant/caixa."""


def is_pix_qr_enabled(*, settings, connection, terminal_id) -> bool:
    if not getattr(settings, "MP_ENABLED", False):
        return False
    if not getattr(settings, "PIX_LOCATION_ENABLED", True):
        return False
    if connection is None or getattr(connection, "status", None) != "connected":
        return False
    if not getattr(connection, "enabled_in_pdv", False):
        return False
    allowed = getattr(connection, "allowed_terminal_ids", None)
    if allowed:
        return str(terminal_id) in {str(x) for x in allowed}
    return True
