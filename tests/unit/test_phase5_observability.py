import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)


def test_request_id_accepts_valid_header_and_rejects_invalid_header():
    from infra.observability.request_context import ensure_request_id

    sent = str(uuid.uuid4())

    assert ensure_request_id(sent) == sent
    assert ensure_request_id("bad\nheader") != "bad\nheader"
    assert uuid.UUID(ensure_request_id(None))


def test_error_response_has_consistent_shape_without_stack_trace():
    from infra.observability.errors import error_response

    payload = error_response(
        code="internal_error",
        message="Erro interno.",
        request_id="req-123",
        details={"field": "value"},
    )

    assert payload == {
        "error": {
            "code": "internal_error",
            "message": "Erro interno.",
            "request_id": "req-123",
            "details": {"field": "value"},
        }
    }
    assert "traceback" not in json.dumps(payload).lower()
    assert "stack" not in json.dumps(payload).lower()


def test_sanitize_log_data_masks_sensitive_keys_recursively():
    from infra.observability.sanitization import sanitize_log_data

    payload = {
        "email": "cliente@example.com",
        "authorization": "Bearer secret-token",
        "nested": {
            "certificate_password": "senha",
            "csc_token": "token",
            "sale": {"total": 10, "items": [{"product": "A"}]},
        },
    }

    sanitized = sanitize_log_data(payload)

    assert sanitized["email"] == "cliente@example.com"
    assert sanitized["authorization"] == "[REDACTED]"
    assert sanitized["nested"]["certificate_password"] == "[REDACTED]"
    assert sanitized["nested"]["csc_token"] == "[REDACTED]"
    assert sanitized["nested"]["sale"] == "[REDACTED]"


def test_metrics_registry_tracks_request_latency_and_errors():
    from infra.observability.metrics import MetricsRegistry

    registry = MetricsRegistry()

    registry.record_request("GET", "/api/v1/test", 200, 12.5)
    registry.record_request("GET", "/api/v1/test", 500, 22.0)

    snapshot = registry.snapshot()

    assert snapshot["api_requests_total"][("GET", "/api/v1/test", 200)] == 1
    assert snapshot["api_requests_total"][("GET", "/api/v1/test", 500)] == 1
    assert snapshot["api_errors_total"][("GET", "/api/v1/test", 500)] == 1
    assert snapshot["api_latency_ms"][("GET", "/api/v1/test")]["count"] == 2
    assert snapshot["api_latency_ms"][("GET", "/api/v1/test")]["max"] == 22.0


def test_metrics_text_export_does_not_expose_sensitive_labels():
    from infra.observability.metrics import MetricsRegistry

    registry = MetricsRegistry()
    registry.record_billing_webhook("evt-secret-token", "failed")
    registry.record_fiscal_invoice("12345678901", "failed")

    text = registry.to_prometheus_text()

    assert "evt-secret-token" not in text
    assert "12345678901" not in text
    assert "marketfy_billing_webhooks_total" in text
    assert "marketfy_fiscal_invoices_total" in text


@dataclass
class SavedAudit:
    actor_user_id: uuid.UUID | None = None
    actor_role: str | None = None
    market_id: uuid.UUID | None = None
    resource_type: str = ""
    resource_id: str | None = None
    action: str = ""
    result: str = ""
    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    metadata_json: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=datetime.utcnow)


class InMemoryAuditRepo:
    def __init__(self):
        self.saved = []

    async def save(self, audit_log, commit=True):
        self.saved.append(audit_log)
        return audit_log


@pytest.mark.asyncio
async def test_audit_service_sanitizes_metadata_before_persisting():
    from application.services.audit_service import AuditService

    repo = InMemoryAuditRepo()
    service = AuditService(repo)

    await service.record(
        actor_user_id=uuid.uuid4(),
        actor_role="admin",
        action="fiscal.config.updated",
        resource_type="fiscal_config",
        result="success",
        request_id="req-123",
        metadata={
            "csc_token": "secret",
            "certificate_password": "secret",
            "safe": "ok",
        },
    )

    saved = repo.saved[0]
    metadata = json.loads(saved.metadata_json)

    assert metadata["safe"] == "ok"
    assert metadata["csc_token"] == "[REDACTED]"
    assert metadata["certificate_password"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_readiness_check_reports_database_failure_without_details():
    from infra.observability.health import readiness_payload

    async def failing_db_check():
        raise RuntimeError("password=secret host=private-db")

    payload, status_code = await readiness_payload(database_check=failing_db_check)

    assert status_code == 503
    assert payload["status"] == "not_ready"
    assert payload["checks"]["database"]["status"] == "unavailable"
    assert "secret" not in json.dumps(payload).lower()
    assert "private-db" not in json.dumps(payload).lower()


def test_app_returns_request_id_header_on_liveness():
    from infra.web.main import app

    client = TestClient(app)
    response = client.get("/health/live", headers={"X-Request-ID": "req-test-123"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-test-123"
    assert response.json()["status"] == "ok"


def test_metrics_endpoint_exports_api_metrics_without_sensitive_payload():
    from infra.web.main import app

    client = TestClient(app)
    client.get("/health/live")
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "marketfy_api_requests_total" in response.text
    assert "authorization" not in response.text.lower()
    assert "access_token" not in response.text.lower()
