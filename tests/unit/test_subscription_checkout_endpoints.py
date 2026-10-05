from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta
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


def _user(owner_id=None):
    return SimpleNamespace(
        id=owner_id or uuid.uuid4(), name="Ana", email="ana@t.com", role="owner",
        plan_id=uuid.uuid4(), plan_expiration=None, is_active=True, cpf=None,
    )


def _override_common(user):
    app.dependency_overrides[billing_router.get_current_user] = lambda: user
    app.dependency_overrides[billing_router.get_db] = lambda: AsyncMock()


class StubRecurringService:
    last_kwargs = None

    def __init__(self, **kwargs):
        pass

    async def ensure_checkout(self, subscription_id):
        return {"status": "completed", "checkout_url": "https://pay/mp/x"}


def test_subscriptions_checkout_endpoint_confirms_pending_checkout(monkeypatch):
    sub_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    local_sub = SimpleNamespace(id=sub_id, owner_id=owner_id)

    fake_repo = AsyncMock()
    fake_repo.get_by_id.return_value = local_sub
    monkeypatch.setattr(
        "infra.repositories.billing_repo.SQLAlchemyBillingSubscriptionRepository",
        lambda db: fake_repo,
    )
    monkeypatch.setattr("application.services.recurring_service.RecurringService", StubRecurringService)

    _override_common(_user(owner_id))
    try:
        response = TestClient(app).post(f"/api/v1/billing/subscriptions/{sub_id}/checkout")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    body = response.json()
    assert body["checkout_url"] == "https://pay/mp/x"
    assert body["status"] == "completed"


def test_subscriptions_checkout_endpoint_404_for_other_owner(monkeypatch):
    sub_id = uuid.uuid4()
    local_sub = SimpleNamespace(id=sub_id, owner_id=uuid.uuid4())

    fake_repo = AsyncMock()
    fake_repo.get_by_id.return_value = local_sub
    monkeypatch.setattr(
        "infra.repositories.billing_repo.SQLAlchemyBillingSubscriptionRepository",
        lambda db: fake_repo,
    )

    _override_common(_user())
    try:
        response = TestClient(app).post(f"/api/v1/billing/subscriptions/{sub_id}/checkout")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_subscriptions_checkout_endpoint_404_for_unknown_id(monkeypatch):
    fake_repo = AsyncMock()
    fake_repo.get_by_id.return_value = None
    monkeypatch.setattr(
        "infra.repositories.billing_repo.SQLAlchemyBillingSubscriptionRepository",
        lambda db: fake_repo,
    )

    _override_common(_user())
    try:
        response = TestClient(app).post(f"/api/v1/billing/subscriptions/{uuid.uuid4()}/checkout")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_cancel_subscription_marks_cancel_at_period_end_and_keeps_access(monkeypatch):
    owner_id = uuid.uuid4()
    now = datetime.utcnow()
    current_sub = SimpleNamespace(
        id=uuid.uuid4(), owner_id=owner_id, billing_mode="recurring", status="active",
        billing_subscription_id="preapproval_1", expires_at=now + timedelta(days=10),
        cancel_at_period_end=False, canceled_at=None,
    )
    fake_repo = AsyncMock()
    fake_repo.get_current_for_owner.return_value = current_sub
    monkeypatch.setattr(
        "infra.repositories.billing_repo.SQLAlchemyBillingSubscriptionRepository",
        lambda db: fake_repo,
    )

    bc_client = AsyncMock()
    monkeypatch.setattr(billing_router, "BillingCoreClient", lambda: bc_client)

    _override_common(_user(owner_id))
    app.dependency_overrides[billing_router.get_audit_service] = lambda: AsyncMock()
    try:
        response = TestClient(app).post("/api/v1/billing/subscription/cancel")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert current_sub.cancel_at_period_end is True
    assert current_sub.canceled_at is not None
    assert current_sub.status == "active"  # acesso continua ate expires_at
    bc_client.cancel_subscription.assert_awaited_once()
    fake_repo.save.assert_awaited_once_with(current_sub)


def test_cancel_subscription_skips_gateway_call_for_invoice_mode(monkeypatch):
    owner_id = uuid.uuid4()
    now = datetime.utcnow()
    current_sub = SimpleNamespace(
        id=uuid.uuid4(), owner_id=owner_id, billing_mode="invoice", status="active",
        billing_subscription_id=None, expires_at=now + timedelta(days=10),
        cancel_at_period_end=False, canceled_at=None,
    )
    fake_repo = AsyncMock()
    fake_repo.get_current_for_owner.return_value = current_sub
    monkeypatch.setattr(
        "infra.repositories.billing_repo.SQLAlchemyBillingSubscriptionRepository",
        lambda db: fake_repo,
    )
    bc_client = AsyncMock()
    monkeypatch.setattr(billing_router, "BillingCoreClient", lambda: bc_client)

    _override_common(_user(owner_id))
    app.dependency_overrides[billing_router.get_audit_service] = lambda: AsyncMock()
    try:
        response = TestClient(app).post("/api/v1/billing/subscription/cancel")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert current_sub.cancel_at_period_end is True
    bc_client.cancel_subscription.assert_not_awaited()


def test_cancel_subscription_404_when_nothing_to_cancel(monkeypatch):
    fake_repo = AsyncMock()
    fake_repo.get_current_for_owner.return_value = None
    monkeypatch.setattr(
        "infra.repositories.billing_repo.SQLAlchemyBillingSubscriptionRepository",
        lambda db: fake_repo,
    )

    _override_common(_user())
    app.dependency_overrides[billing_router.get_audit_service] = lambda: AsyncMock()
    try:
        response = TestClient(app).post("/api/v1/billing/subscription/cancel")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_cancel_subscription_returns_already_canceled_when_repeated(monkeypatch):
    owner_id = uuid.uuid4()
    now = datetime.utcnow()
    current_sub = SimpleNamespace(
        id=uuid.uuid4(), owner_id=owner_id, billing_mode="invoice", status="active",
        expires_at=now + timedelta(days=10), cancel_at_period_end=True, canceled_at=now,
    )
    fake_repo = AsyncMock()
    fake_repo.get_current_for_owner.return_value = current_sub
    monkeypatch.setattr(
        "infra.repositories.billing_repo.SQLAlchemyBillingSubscriptionRepository",
        lambda db: fake_repo,
    )

    _override_common(_user(owner_id))
    app.dependency_overrides[billing_router.get_audit_service] = lambda: AsyncMock()
    try:
        response = TestClient(app).post("/api/v1/billing/subscription/cancel")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "already_canceled"
    fake_repo.save.assert_not_awaited()


def test_get_subscription_status_live_grants_provisional_access_when_gateway_active(monkeypatch):
    owner_id = uuid.uuid4()
    local_sub = SimpleNamespace(
        id=uuid.uuid4(), owner_id=owner_id, status="pending",
        billing_subscription_id="preapproval_1", provisional=False, expires_at=None,
    )
    fake_repo = AsyncMock()
    fake_repo.get_by_id.return_value = local_sub
    monkeypatch.setattr(
        "infra.repositories.billing_repo.SQLAlchemyBillingSubscriptionRepository",
        lambda db: fake_repo,
    )

    bc_client = AsyncMock()
    bc_client.get_subscription_status.return_value = {"gateway_status": "ACTIVE"}
    monkeypatch.setattr(billing_router, "BillingCoreClient", lambda: bc_client)

    _override_common(_user(owner_id))
    try:
        response = TestClient(app).get(f"/api/v1/billing/subscriptions/{local_sub.id}/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "active"
    assert body["provisional"] is True
    assert local_sub.status == "active"
    assert local_sub.provisional is True
    assert local_sub.expires_at is not None
    fake_repo.save.assert_awaited_once_with(local_sub)


def test_get_subscription_status_live_stays_pending_when_gateway_still_pending(monkeypatch):
    owner_id = uuid.uuid4()
    local_sub = SimpleNamespace(
        id=uuid.uuid4(), owner_id=owner_id, status="pending",
        billing_subscription_id="preapproval_1", provisional=False, expires_at=None,
    )
    fake_repo = AsyncMock()
    fake_repo.get_by_id.return_value = local_sub
    monkeypatch.setattr(
        "infra.repositories.billing_repo.SQLAlchemyBillingSubscriptionRepository",
        lambda db: fake_repo,
    )

    bc_client = AsyncMock()
    bc_client.get_subscription_status.return_value = {"gateway_status": "PENDING"}
    monkeypatch.setattr(billing_router, "BillingCoreClient", lambda: bc_client)

    _override_common(_user(owner_id))
    try:
        response = TestClient(app).get(f"/api/v1/billing/subscriptions/{local_sub.id}/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["provisional"] is False
    fake_repo.save.assert_not_awaited()


def test_get_subscription_status_live_404_for_other_owner(monkeypatch):
    local_sub = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4(), status="pending")
    fake_repo = AsyncMock()
    fake_repo.get_by_id.return_value = local_sub
    monkeypatch.setattr(
        "infra.repositories.billing_repo.SQLAlchemyBillingSubscriptionRepository",
        lambda db: fake_repo,
    )

    _override_common(_user())
    try:
        response = TestClient(app).get(f"/api/v1/billing/subscriptions/{local_sub.id}/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
