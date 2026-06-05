import os
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

current_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.abspath(os.path.join(current_dir, "../../.."))
app_dir = os.path.join(repo_root, "backend", "app")
if app_dir not in sys.path:
    sys.path.append(app_dir)


def _prod_settings_kwargs(**overrides):
    values = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql://user:pass@db:5432/marketfy",
        "SECRET_KEY": "x" * 64,
        "FISCAL_SECRET_KEY": "f" * 32,
        "BACKEND_CORS_ORIGINS": ["https://app.marketfy.example"],
        "PUBLIC_FRONTEND_URL": "https://app.marketfy.example",
        "PUBLIC_API_BASE_URL": "https://api.marketfy.example",
        "METRICS_ACCESS_TOKEN": "m" * 32,
        "BILLING_CORE_ENABLED": True,
        "BILLING_CORE_BASE_URL": "https://billing.example",
        "BILLING_CORE_API_KEY": "b" * 32,
        "BILLING_CORE_WEBHOOK_SECRET": "s" * 32,
        "BILLING_CORE_WEBHOOK_CALLBACK_URL": "https://api.marketfy.example/callback",
        "BILLING_CORE_WEBHOOK_HOST": "https://api.marketfy.example",
        "NEECTIFY_API_KEY": "nf_live_abc123_" + "x" * 32,
        "ACCESS_TOKEN_EXPIRE_MINUTES": 15,
        "RATE_LIMIT_BACKEND": "redis",
    }
    values.update(overrides)
    return values


def test_production_settings_reject_wildcard_cors():
    from infra.config.settings import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_prod_settings_kwargs(BACKEND_CORS_ORIGINS=["*"]))


def test_production_settings_require_secure_secrets_and_urls():
    from infra.config.settings import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_prod_settings_kwargs(SECRET_KEY="short"))

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_prod_settings_kwargs(PUBLIC_FRONTEND_URL=None))


def test_access_token_uses_shorter_production_default():
    from infra.config.settings import Settings

    settings = Settings(_env_file=None, **_prod_settings_kwargs())

    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES <= 30
    assert settings.is_production


def test_auth_handler_creates_typed_refresh_token_and_hashes_jti():
    from infra.security.auth_handler import AuthHandler

    token = AuthHandler.create_refresh_token(
        subject="user-123",
        role="owner",
        jti="session-123",
        expires_delta=timedelta(days=7),
    )
    payload = AuthHandler.decode_token(token)

    assert payload["sub"] == "user-123"
    assert payload["role"] == "owner"
    assert payload["typ"] == "refresh"
    assert payload["jti"] == "session-123"
    assert AuthHandler.hash_token_jti("session-123") != "session-123"
    assert AuthHandler.hash_token_jti("session-123") == AuthHandler.hash_token_jti("session-123")


def test_liveness_response_has_csp_header():
    from infra.web.main import app

    client = TestClient(app)
    response = client.get("/health/live")

    assert response.status_code == 200
    assert "Content-Security-Policy" in response.headers
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


@pytest.mark.asyncio
async def test_readiness_checks_redis_when_rate_limit_backend_requires_it(monkeypatch):
    from infra.observability import health

    async def ok_database_check():
        return None

    async def failing_redis_check():
        raise RuntimeError("redis password=secret")

    monkeypatch.setattr(health, "get_settings", lambda: SimpleNamespace(RATE_LIMIT_BACKEND="redis"))

    payload, status_code = await health.readiness_payload(
        database_check=ok_database_check,
        redis_check=failing_redis_check,
    )

    assert status_code == 503
    assert payload["checks"]["database"]["status"] == "ok"
    assert payload["checks"]["redis"]["status"] == "unavailable"
    assert "secret" not in str(payload).lower()


def test_frontend_app_uses_lazy_route_code_splitting():
    app_path = os.path.join(repo_root, "frontend", "src", "App.jsx")
    source = open(app_path, encoding="utf-8").read()

    assert "React.lazy" in source
    assert "<Suspense" in source
    assert "import('./pages/pdv/Pdv')" in source
    assert "import('./pages/admin/SaaSAdminDashboard')" in source
