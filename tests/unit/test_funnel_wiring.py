from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.dtos import UserCreateDTO, UserResponseDTO
from application.services.subscription_service import SubscriptionService
from infra.web.routers import identity as identity_router


class _Attribution:
    def __init__(self):
        self.links = []

    async def link_registration(self, fsid, user_id):
        self.links.append((fsid, user_id))
        return True


class _Identity:
    def __init__(self, user_id):
        self.user_id = user_id

    async def register_user(self, dto):
        return UserResponseDTO(id=self.user_id, name=dto.name, email=dto.email, role="owner")


def _client(attribution, user_id):
    app = FastAPI()
    app.include_router(identity_router.router, prefix="/api/v1/identity")
    app.dependency_overrides[identity_router.get_identity_service] = lambda: _Identity(user_id)
    app.dependency_overrides[identity_router.get_funnel_attribution_service] = lambda: attribution
    return TestClient(app)


def test_user_create_dto_accepts_optional_funnel_session_id():
    fsid = uuid.uuid4()
    dto = UserCreateDTO(name="A", email="a@t.com", password="123456", funnel_session_id=fsid)
    assert dto.funnel_session_id == fsid
    assert UserCreateDTO(name="A", email="a@t.com", password="123456").funnel_session_id is None


def test_register_links_funnel_session():
    attribution, user_id, fsid = _Attribution(), uuid.uuid4(), uuid.uuid4()
    resp = _client(attribution, user_id).post("/api/v1/identity/register", json={
        "name": "A", "email": "a@t.com", "password": "123456", "funnel_session_id": str(fsid),
    })
    assert resp.status_code == 201
    assert attribution.links == [(fsid, user_id)]


def test_register_without_fsid_does_not_link():
    attribution = _Attribution()
    resp = _client(attribution, uuid.uuid4()).post("/api/v1/identity/register", json={
        "name": "A", "email": "a@t.com", "password": "123456",
    })
    assert resp.status_code == 201
    assert attribution.links == []


class _Analytics:
    def __init__(self):
        self.calls = []

    async def track_event(self, distinct_id, event, properties=None):
        self.calls.append((distinct_id, event, properties))


class _EventRepo:
    async def get_by_event_id(self, event_id):
        return None

    async def save(self, model):
        return model


class _SubRepo:
    def __init__(self, sub):
        self.sub = sub

    async def get_by_billing_subscription_id(self, _):
        return self.sub

    async def save(self, sub):
        return sub


class _UserRepo:
    async def get_by_id(self, _):
        return None

    async def save(self, user):
        return user


@pytest.mark.asyncio
async def test_recurring_payment_received_emits_invoice_paid():
    owner_id = uuid.uuid4()
    sub = SimpleNamespace(id=uuid.uuid4(), owner_id=owner_id, plan_id=None, status="pending",
                          value=Decimal("89.90"), subscription_type="monthly",
                          last_event_at=None, expires_at=None)
    analytics = _Analytics()
    svc = SubscriptionService(user_repo=_UserRepo(), plan_repo=None, subscription_repo=_SubRepo(sub),
                              event_repo=_EventRepo(), analytics=analytics)
    await svc.process_recurring_event("PAYMENT_RECEIVED", "bc-sub-1", None, datetime(2026, 10, 4), {})
    assert analytics.calls == [(str(owner_id), "invoice_paid",
                                {"amount": "89.90", "subscription_type": "monthly", "billing_mode": "recurring"})]


@pytest.mark.asyncio
async def test_recurring_other_events_do_not_emit_invoice_paid():
    sub = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4(), plan_id=None, status="active",
                          value=Decimal("1"), subscription_type="monthly", last_event_at=None, expires_at=None)
    analytics = _Analytics()
    svc = SubscriptionService(user_repo=_UserRepo(), plan_repo=None, subscription_repo=_SubRepo(sub),
                              event_repo=_EventRepo(), analytics=analytics)
    await svc.process_recurring_event("SUBSCRIPTION_INACTIVATED", "bc-sub-1", None, None, {})
    assert analytics.calls == []


def test_services_default_to_build_analytics(monkeypatch):
    import application.services.invoice_service as inv
    import application.services.recurring_service as rec
    import application.services.subscription_service as sub
    sentinel = object()
    for module in (inv, rec, sub):
        monkeypatch.setattr(module, "build_analytics", lambda: sentinel)
    assert sub.SubscriptionService(user_repo=None, plan_repo=None)._analytics is sentinel
    assert inv.InvoiceService(None, None, None, None, None)._analytics is sentinel
    assert rec.RecurringService(None, None, None, None, None)._analytics is sentinel
