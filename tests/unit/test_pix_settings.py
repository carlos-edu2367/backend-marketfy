# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)


def _fresh_settings(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from infra.config import settings as settings_module
    settings_module.get_settings.cache_clear()
    return settings_module.get_settings()


def test_mp_defaults_are_safe(monkeypatch):
    s = _fresh_settings(
        monkeypatch,
        DATABASE_URL="postgresql://x:y@localhost/z",
        SECRET_KEY="a" * 32,
    )
    assert s.MP_ENABLED is False
    assert s.MP_API_BASE_URL == "https://api.mercadopago.com"
    assert s.MP_AUTH_BASE_URL == "https://auth.mercadopago.com"
    assert s.MP_ORDER_DEFAULT_EXPIRATION == "PT5M"
    assert s.MP_VALIDATE_COOLDOWN_SECONDS == 5


def test_mp_enabled_reads_env(monkeypatch):
    s = _fresh_settings(
        monkeypatch,
        DATABASE_URL="postgresql://x:y@localhost/z",
        SECRET_KEY="a" * 32,
        MP_ENABLED="true",
        MP_APP_ID="123",
        MP_CLIENT_SECRET="secret",
        MP_SECRET_KEY="k" * 32,
    )
    assert s.MP_ENABLED is True
    assert s.MP_APP_ID == "123"
