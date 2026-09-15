from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from fastapi.testclient import TestClient
import pytest

from infra.web.main import app
from infra.web.routers import billing as billing_router


class StubMarketRepo:
    def __init__(self, db):
        pass

    async def list_by_owner(self, owner_id):
        return [SimpleNamespace(document="12345678000195", created_at=datetime(2023, 1, 1))]


class StubRecurringService:
    def __init__(self, *args, **kwargs):
        pass

    async def contract(self, user, plan_id, subscription_type, document, idempotency_key):
        StubRecurringService.last_document = document
        return {"subscription_id": str(uuid.uuid4()), "job_id": "j1", "checkout_url": "https://pay"}


def _user_without_cpf():
    return SimpleNamespace(
        id=uuid.uuid4(), name="Ana", email="ana@t.com", role="owner",
        plan_id=uuid.uuid4(), plan_expiration=None, is_active=True, cpf=None,
    )


def test_recurring_subscribe_falls_back_to_oldest_market_document(monkeypatch):
    monkeypatch.setattr("infra.repositories.sqlalchemy_repos.SQLAlchemyMarketRepository", StubMarketRepo)
    monkeypatch.setattr("application.services.recurring_service.RecurringService", StubRecurringService)

    app.dependency_overrides[billing_router.get_current_user] = _user_without_cpf
    app.dependency_overrides[billing_router.get_db] = lambda: AsyncMock()
    app.dependency_overrides[billing_router.get_audit_service] = lambda: AsyncMock()

    try:
        response = TestClient(app).post(
            "/api/v1/billing/subscribe",
            json={"plan_id": str(uuid.uuid4()), "subscription_type": "monthly", "billing_mode": "recurring"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert StubRecurringService.last_document == "12345678000195"
