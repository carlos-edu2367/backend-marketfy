from __future__ import annotations

import os
import sys
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.billing_document import (
    mask_document,
    registered_document,
    resolve_billing_document,
)


def _user(cpf="123.456.789-01"):
    return SimpleNamespace(
        id=uuid.uuid4(), name="Ana", email="ana@t.com", role="owner",
        plan_id=None, plan_expiration=None, is_active=True,
        cpf=SimpleNamespace(value=cpf.replace(".", "").replace("-", "")) if cpf else None,
    )


def test_registered_document_returns_cpf_digits():
    assert registered_document(_user()) == "12345678901"


def test_registered_document_is_none_without_cpf():
    assert registered_document(_user(cpf=None)) is None


def test_mask_keeps_only_middle_digits():
    assert mask_document("12345678901") == "***.456.789-**"
    assert mask_document(None) is None
    assert mask_document("123") is None


def test_typed_document_wins_over_registered_one():
    assert resolve_billing_document("98.765.432/0001-10", _user()) == "98765432000110"


def test_falls_back_to_registered_document_when_omitted():
    assert resolve_billing_document(None, _user()) == "12345678901"
    assert resolve_billing_document("", _user()) == "12345678901"


def test_auth_me_exposes_only_the_masked_document(monkeypatch):
    from infra.web.main import app
    from infra.web.routers import auth as auth_router

    class StubMarketRepo:
        def __init__(self, db):
            pass

        async def list_by_owner(self, owner_id):
            return []

    monkeypatch.setattr(auth_router, "SQLAlchemyMarketRepository", StubMarketRepo)

    app.dependency_overrides[auth_router.get_current_user] = lambda: _user()
    app.dependency_overrides[auth_router.get_db] = lambda: None
    try:
        response = TestClient(app).get("/api/v1/auth/me")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["document_masked"] == "***.456.789-**"
    assert "12345678901" not in response.text


def _market(document, created_at):
    return SimpleNamespace(document=document, created_at=created_at)


def test_registered_document_falls_back_to_oldest_market_without_personal_cpf():
    from datetime import datetime
    markets = [
        _market("12345678000195", datetime(2024, 6, 1)),
        _market("98765432000110", datetime(2023, 1, 1)),  # mais antiga
    ]
    assert registered_document(_user(cpf=None), markets=markets) == "98765432000110"


def test_registered_document_prefers_personal_cpf_over_markets():
    from datetime import datetime
    markets = [_market("12345678000195", datetime(2023, 1, 1))]
    assert registered_document(_user(), markets=markets) == "12345678901"


def test_registered_document_returns_none_without_cpf_or_markets():
    assert registered_document(_user(cpf=None), markets=[]) is None
    assert registered_document(_user(cpf=None)) is None


def test_resolve_billing_document_propagates_markets_fallback():
    from datetime import datetime
    markets = [_market("12345678000195", datetime(2023, 1, 1))]
    assert resolve_billing_document(None, _user(cpf=None), markets=markets) == "12345678000195"


def test_mask_document_masks_cnpj_keeping_only_middle_digits():
    assert mask_document("12345678000195") == "**.345.678/****-**"


def test_mask_document_still_masks_cpf():
    assert mask_document("12345678901") == "***.456.789-**"


def test_auth_me_falls_back_to_market_document_when_no_personal_cpf(monkeypatch):
    from datetime import datetime
    from infra.web.main import app
    from infra.web.routers import auth as auth_router

    class StubMarketRepo:
        def __init__(self, db):
            pass

        async def list_by_owner(self, owner_id):
            return [SimpleNamespace(document="12345678000195", created_at=datetime(2023, 1, 1))]

    monkeypatch.setattr(auth_router, "SQLAlchemyMarketRepository", StubMarketRepo)

    app.dependency_overrides[auth_router.get_current_user] = lambda: _user(cpf=None)
    app.dependency_overrides[auth_router.get_db] = lambda: None
    try:
        response = TestClient(app).get("/api/v1/auth/me")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["document_masked"] == "**.345.678/****-**"
