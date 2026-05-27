from __future__ import annotations

import hashlib
import hmac
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.fiscal.fiscal_credits_service import FiscalCreditsService
from domain.fiscal import FiscalEmissionPackage
from infra.web.routers.billing_core_webhooks import (
    BillingCoreWebhookProcessor,
    validate_bc_signature,
)

SECRET = "billing-core-secret"


def _signature(raw_body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def FakePackage(**kwargs) -> FiscalEmissionPackage:
    defaults = {
        "owner_id": uuid.uuid4(),
        "package_slug": "pack_100",
        "quantity": 100,
        "remaining": 100,
        "payment_status": "pending",
        "bc_job_id": "job-1",
        "bc_idempotency_key": "idem-1",
        "price_gross": Decimal("41.99"),
        "price_net_target": Decimal("39.90"),
    }
    defaults.update(kwargs)
    return FiscalEmissionPackage(**defaults)


def _credits_service(package=None, existing_ledger=None):
    repo = AsyncMock()
    repo.get_package.return_value = package
    repo.activate_package.return_value = 1
    repo.mark_package_failed.return_value = None
    quota_repo = AsyncMock()
    quota_repo.get_ledger_entry_by_idempotency.return_value = existing_ledger
    quota_service = AsyncMock()
    notification_service = AsyncMock()
    audit_service = AsyncMock()
    return FiscalCreditsService(
        credits_repo=repo,
        quota_repo=quota_repo,
        mp_client=AsyncMock(),
        bc_client=AsyncMock(),
        user_repo=AsyncMock(),
        quota_service=quota_service,
        notification_service=notification_service,
        audit_service=audit_service,
        settings=SimpleNamespace(BILLING_CORE_ENABLED=True),
    )


def test_valid_signature_accepted():
    body = b'{"event":"payment.updated"}'
    sig = _signature(body)
    assert validate_bc_signature(body, sig, SECRET) is True


def test_invalid_signature_rejected():
    body = b'{"event":"payment.updated"}'
    sig = _signature(body) + "invalid"
    assert validate_bc_signature(body, sig, SECRET) is False


def test_missing_signature_or_secret_rejected():
    body = b'{"event":"payment.updated"}'
    assert validate_bc_signature(body, "", SECRET) is False
    assert validate_bc_signature(body, "sig", "") is False


@pytest.mark.asyncio
async def test_duplicate_webhook_returns_200_no_reprocess():
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = SimpleNamespace(id=uuid.uuid4(), processing_status="processed")
    credits_service = AsyncMock()
    processor = BillingCoreWebhookProcessor(webhook_repo, credits_service)

    payload = {
        "payment_id": "pay-1",
        "system_payment_id": str(uuid.uuid4()),
        "payment_status": "CONFIRMED",
    }
    status = await processor.process(payload, b"{}", {})

    assert status == 200
    credits_service.activate_package.assert_not_called()


@pytest.mark.asyncio
async def test_different_webhook_processed():
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    credits_service = AsyncMock()
    processor = BillingCoreWebhookProcessor(webhook_repo, credits_service)

    payload_1 = {
        "payment_id": "pay-1",
        "system_payment_id": str(uuid.uuid4()),
        "payment_status": "CONFIRMED",
    }
    payload_2 = {
        "payment_id": "pay-1",
        "system_payment_id": str(uuid.uuid4()),
        "payment_status": "OVERDUE",
    }

    await processor.process(payload_1, b"{}", {})
    await processor.process(payload_2, b"{}", {})

    assert webhook_repo.create_event.call_count == 2
    assert credits_service.activate_package.call_count == 1
    assert credits_service.mark_package_failed.call_count == 1


@pytest.mark.asyncio
async def test_payment_status_confirmed_activates_package():
    package_id = uuid.uuid4()
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    credits_service = AsyncMock()
    processor = BillingCoreWebhookProcessor(webhook_repo, credits_service)

    payload = {
        "payment_id": "pay-123",
        "system_payment_id": str(package_id),
        "payment_status": "CONFIRMED",
    }
    await processor.process(payload, b"{}", {})

    credits_service.activate_package.assert_called_once_with(
        package_id=package_id,
        bc_payment_id="pay-123",
        payment_data=payload,
    )


@pytest.mark.asyncio
async def test_payment_status_received_activates_package():
    package_id = uuid.uuid4()
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    credits_service = AsyncMock()
    processor = BillingCoreWebhookProcessor(webhook_repo, credits_service)

    payload = {
        "payment_id": "pay-123",
        "system_payment_id": str(package_id),
        "payment_status": "RECEIVED",
    }
    await processor.process(payload, b"{}", {})

    credits_service.activate_package.assert_called_once_with(
        package_id=package_id,
        bc_payment_id="pay-123",
        payment_data=payload,
    )


@pytest.mark.asyncio
async def test_payment_status_overdue_marks_package_failed():
    package_id = uuid.uuid4()
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    credits_service = AsyncMock()
    processor = BillingCoreWebhookProcessor(webhook_repo, credits_service)

    payload = {
        "payment_id": "pay-123",
        "system_payment_id": str(package_id),
        "payment_status": "OVERDUE",
    }
    await processor.process(payload, b"{}", {})

    credits_service.mark_package_failed.assert_called_once_with(
        package_id=package_id,
        reason="Status: OVERDUE",
    )


@pytest.mark.asyncio
async def test_payment_status_refunded_marks_package_failed():
    package_id = uuid.uuid4()
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    credits_service = AsyncMock()
    processor = BillingCoreWebhookProcessor(webhook_repo, credits_service)

    payload = {
        "payment_id": "pay-123",
        "system_payment_id": str(package_id),
        "payment_status": "REFUNDED",
    }
    await processor.process(payload, b"{}", {})

    credits_service.mark_package_failed.assert_called_once_with(
        package_id=package_id,
        reason="Status: REFUNDED",
    )


@pytest.mark.asyncio
async def test_payment_status_pending_ignored():
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    credits_service = AsyncMock()
    processor = BillingCoreWebhookProcessor(webhook_repo, credits_service)

    payload = {
        "payment_id": "pay-123",
        "system_payment_id": str(uuid.uuid4()),
        "payment_status": "PENDING",
    }
    await processor.process(payload, b"{}", {})

    credits_service.activate_package.assert_not_called()
    credits_service.mark_package_failed.assert_not_called()


@pytest.mark.asyncio
async def test_non_existent_package_logs_and_returns_200():
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    credits_service = AsyncMock()
    processor = BillingCoreWebhookProcessor(webhook_repo, credits_service)

    payload = {
        "payment_id": "pay-123",
        "system_payment_id": "invalid-uuid",
        "payment_status": "CONFIRMED",
    }
    status = await processor.process(payload, b"{}", {})

    assert status == 200
    credits_service.activate_package.assert_not_called()
    webhook_repo.mark_processed.assert_called_once()


@pytest.mark.asyncio
async def test_internal_error_always_returns_200():
    webhook_repo = AsyncMock()
    webhook_repo.get_event.side_effect = RuntimeError("DB error")
    processor = BillingCoreWebhookProcessor(webhook_repo, AsyncMock())

    payload = {
        "payment_id": "pay-123",
        "system_payment_id": str(uuid.uuid4()),
        "payment_status": "CONFIRMED",
    }
    status = await processor.process(payload, b"{}", {})

    assert status == 200


@pytest.mark.asyncio
async def test_activate_package_increments_addon_credits():
    pkg = FakePackage(owner_id=uuid.uuid4(), payment_status="pending")
    svc = _credits_service(package=pkg)

    await svc.activate_package(pkg.id, "pay-1", {"payment_id": "pay-1"})

    svc.quota_service.add_addon_credits.assert_called_once()
    assert svc.quota_service.add_addon_credits.call_args.kwargs["amount"] == 100
    assert svc.quota_service.add_addon_credits.call_args.kwargs["idempotency_key"] == "bc_payment:pay-1"


@pytest.mark.asyncio
async def test_activate_package_creates_ledger_notification_and_audit():
    pkg = FakePackage(owner_id=uuid.uuid4(), payment_status="pending")
    svc = _credits_service(package=pkg)

    await svc.activate_package(pkg.id, "pay-1", {"payment_id": "pay-1"})

    svc.notification_service.create_notification.assert_called_once()
    svc.audit_service.record.assert_called_once()


@pytest.mark.asyncio
async def test_activate_package_idempotent():
    pkg = FakePackage(owner_id=uuid.uuid4(), payment_status="pending")
    svc = _credits_service(package=pkg, existing_ledger=SimpleNamespace(id=uuid.uuid4()))

    await svc.activate_package(pkg.id, "pay-1", {"payment_id": "pay-1"})

    svc.credits_repo.activate_package.assert_not_called()
    svc.quota_service.add_addon_credits.assert_not_called()
