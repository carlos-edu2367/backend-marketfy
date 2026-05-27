import os
import sys

from fastapi.testclient import TestClient

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)


def test_fiscal_auth_error_returns_upstream_error_response(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")

    from domain.fiscal import FiscalAuthError
    from infra.web.main import app

    path = "/__test__/fiscal-auth-error"
    if not any(getattr(route, "path", None) == path for route in app.routes):
        @app.get(path)
        async def _raise_fiscal_auth_error():
            raise FiscalAuthError(
                "Neectify Fiscal retornou HTTP 401: credencial invalida ou sem permissao."
            )

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(path)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "fiscal_provider_auth_error"
    assert response.json()["error"]["message"] == (
        "Falha de autenticacao com o provedor fiscal."
    )


def test_fiscal_validation_error_returns_unprocessable_entity(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")

    from domain.fiscal import FiscalValidationError
    from infra.web.main import app

    path = "/__test__/fiscal-validation-error"
    if not any(getattr(route, "path", None) == path for route in app.routes):
        @app.get(path)
        async def _raise_fiscal_validation_error():
            raise FiscalValidationError(
                "Dados invalidos no provedor.",
                details={"detail": [{"loc": ["body", "cnpj"], "msg": "issuer.invalid_cnpj", "type": "value_error"}]}
            )

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(path)

    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == "validation_error"
    assert data["error"]["message"] == "Dados invalidos no provedor fiscal."
    assert len(data["error"]["details"]) == 1
    assert data["error"]["details"][0]["msg"] == "issuer.invalid_cnpj"

