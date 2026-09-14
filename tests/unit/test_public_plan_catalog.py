from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal

from fastapi.testclient import TestClient

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.identity import Plan, PlanType
from application.services.plan_catalog import select_public_plans


def _plan(name, type_=PlanType.PAID, active=True, order=0, monthly="79.90"):
    return Plan(
        name=name, type=type_, max_markets=1, max_terminals=1,
        price_monthly=Decimal(monthly), is_active=active, display_order=order,
    )


def test_hides_inactive_and_courtesy_plans():
    plans = [
        _plan("Pago"),
        _plan("Trial", type_=PlanType.TRIAL, monthly="0"),
        _plan("Cortesia", type_=PlanType.FREE, monthly="0"),
        _plan("Antigo", active=False),
    ]

    assert [p.name for p in select_public_plans(plans)] == ["Trial", "Pago"]


def test_orders_by_display_order_then_monthly_price():
    plans = [
        _plan("Caro", order=0, monthly="199.90"),
        _plan("Barato", order=0, monthly="49.90"),
        _plan("Primeiro", order=-1, monthly="999.00"),
    ]

    assert [p.name for p in select_public_plans(plans)] == ["Primeiro", "Barato", "Caro"]


def test_public_route_returns_only_sellable_plans_without_internal_fields():
    from infra.web.main import app
    from infra.web.routers import identity as identity_router

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return [_plan("Pago", monthly="79.90"), _plan("Cortesia", type_=PlanType.FREE)]

    original = identity_router.SQLAlchemyPlanRepository
    identity_router.SQLAlchemyPlanRepository = FakeRepo
    app.dependency_overrides[identity_router.get_db] = lambda: None
    try:
        response = TestClient(app).get("/api/v1/identity/plans")
    finally:
        identity_router.SQLAlchemyPlanRepository = original
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert [p["name"] for p in body] == ["Pago"]
    assert body[0]["type"] == "pago"
    assert body[0]["is_recommended"] is False
    assert "created_at" not in body[0]
