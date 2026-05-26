import os
import sys

from fastapi.testclient import TestClient

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)


def test_fiscal_credits_config_route_is_not_captured_as_market_config():
    from infra.web.main import app

    client = TestClient(app)
    response = client.get("/api/v1/fiscal/credits/config")

    assert response.status_code == 200
    assert response.json()["min_qty"] >= 1
