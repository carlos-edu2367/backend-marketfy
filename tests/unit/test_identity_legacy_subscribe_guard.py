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


def _post_as(user):
    from infra.web.main import app
    from infra.web.routers import identity as identity_router

    app.dependency_overrides[identity_router.get_current_user] = lambda: user
    app.dependency_overrides[identity_router.get_db] = lambda: None
    app.dependency_overrides[identity_router.get_subscription_service] = lambda: SimpleNamespace(user_repo=None)
    try:
        client = TestClient(app)
        return client.post(
            f"/api/v1/identity/plans/{uuid.uuid4()}/subscribe",
            json={"duration_days": 365},
        )
    finally:
        app.dependency_overrides.clear()


def test_owner_cannot_self_assign_a_plan_without_paying():
    owner = SimpleNamespace(id=uuid.uuid4(), role="owner")

    response = _post_as(owner)

    assert response.status_code == 403


def test_admin_is_not_blocked_by_the_guard():
    admin = SimpleNamespace(id=uuid.uuid4(), role="admin")

    response = _post_as(admin)

    # Sem banco o fluxo falha adiante (400/500), mas nunca no guard de permissão.
    assert response.status_code != 403
