import uuid
from types import SimpleNamespace
from application.services.pix.feature_flag import is_pix_qr_enabled


def _s(enabled=True): return SimpleNamespace(MP_ENABLED=enabled)


def _c(status="connected", enabled_in_pdv=True, allowed=None):
    return SimpleNamespace(status=status, enabled_in_pdv=enabled_in_pdv, allowed_terminal_ids=allowed)


def test_all_layers_must_allow():
    t = uuid.uuid4()
    assert is_pix_qr_enabled(settings=_s(), connection=_c(), terminal_id=t) is True
    assert is_pix_qr_enabled(settings=_s(False), connection=_c(), terminal_id=t) is False
    assert is_pix_qr_enabled(settings=_s(), connection=_c(status="revoked"), terminal_id=t) is False
    assert is_pix_qr_enabled(settings=_s(), connection=_c(enabled_in_pdv=False), terminal_id=t) is False


def test_terminal_allowlist():
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    conn = _c(allowed=[str(t1)])
    assert is_pix_qr_enabled(settings=_s(), connection=conn, terminal_id=t1) is True
    assert is_pix_qr_enabled(settings=_s(), connection=conn, terminal_id=t2) is False


def test_empty_allowlist_means_all():
    t = uuid.uuid4()
    assert is_pix_qr_enabled(settings=_s(), connection=_c(allowed=[]), terminal_id=t) is True
