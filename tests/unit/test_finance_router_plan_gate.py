from __future__ import annotations

import os
import sys
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.web.routers import finance_support as finance_router
from application.services.plan_access_service import PlanAccessResult


MARKET_ID = uuid.uuid4()


class StubFinanceService:
    async def get_dashboard(self, market_id):
        return {
            "balance": "0", "total_revenue": "0", "total_expense": "0",
            "accounts_receivable": "0", "recent_transactions": [],
        }

    async def add_transaction(self, market_id, dto):
        return SimpleNamespace(
            id=uuid.uuid4(), market_id=market_id, type="income", category="vendas",
            amount=dto.amount, description=dto.description, created_at=None,
        )

    async def list_transactions(self, market_id):
        return []

    async def list_customers(self, market_id, search=None):
        return []


def _build_client(*, plan_access_result: PlanAccessResult):
    app = FastAPI()
    app.include_router(finance_router.router_finance, prefix="/api/v1/finance")

    async def permitted_market():
        return object()

    class StubPlanAccessService:
        async def check_feature(self, owner_id, feature):
            return plan_access_result

    app.dependency_overrides[finance_router.get_current_user] = lambda: SimpleNamespace(id=uuid.uuid4(), role="owner")
    app.dependency_overrides[finance_router.get_finance_service] = lambda: StubFinanceService()
    app.dependency_overrides[finance_router.get_plan_access_service] = lambda: StubPlanAccessService()

    for route in app.routes:
        if route.path.startswith("/api/v1/finance/"):
            for dependency in route.dependant.dependencies:
                if getattr(dependency.call, "__name__", None) == "_dep":
                    app.dependency_overrides[dependency.call] = permitted_market

    return TestClient(app)


def _allowed():
    return PlanAccessResult(allowed=True, reason="Acesso permitido.", subscription_status="active")


def _denied():
    return PlanAccessResult(allowed=False, reason="Financeiro não incluído no seu plano.", subscription_status="active")


def test_dashboard_blocked_when_plan_excludes_finance():
    client = _build_client(plan_access_result=_denied())

    response = client.get(f"/api/v1/finance/{MARKET_ID}/dashboard")

    assert response.status_code == 403


def test_dashboard_allowed_when_plan_includes_finance():
    client = _build_client(plan_access_result=_allowed())

    response = client.get(f"/api/v1/finance/{MARKET_ID}/dashboard")

    assert response.status_code == 200


def test_add_transaction_blocked_when_plan_excludes_finance():
    client = _build_client(plan_access_result=_denied())

    response = client.post(
        f"/api/v1/finance/{MARKET_ID}/transactions",
        json={
            "type": "receita", "category": "vendas", "amount": "10.00",
            "description": "x", "due_date": "2026-09-14T00:00:00",
        },
    )

    assert response.status_code == 403


def test_list_transactions_blocked_when_plan_excludes_finance():
    client = _build_client(plan_access_result=_denied())

    response = client.get(f"/api/v1/finance/{MARKET_ID}/transactions")

    assert response.status_code == 403


def test_customers_endpoint_stays_open_regardless_of_finance_flag():
    """Clientes/fiado é feature básica (sempre disponível) — não deve ser tocada por este gate."""
    client = _build_client(plan_access_result=_denied())

    response = client.get(f"/api/v1/finance/{MARKET_ID}/customers")

    assert response.status_code != 403
