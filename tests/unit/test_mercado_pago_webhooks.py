from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
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

from application.jobs.fiscal_jobs import reconcile_pending_mp_payments
from application.services.fiscal.fiscal_credits_service import FiscalCreditsService
from domain.fiscal import FiscalEmissionPackage, UsageLedgerEventType
from infra.web.routers.mercado_pago_webhooks import (
    MercadoPagoWebhookProcessor,
    validate_mp_signature,
)


SECRET = "webhook-secret"


def _headers(data_id: str, request_id: str = "req-1", ts: int | None = None, secret: str = SECRET):
    ts = ts or int(time.time())
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    digest = hmac.new(secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"x-signature": f"ts={ts},v1={digest}", "x-request-id": request_id}


def FakePackage(**kwargs) -> FiscalEmissionPackage:
    defaults = {
        "owner_id": uuid.uuid4(),
        "package_slug": "pack_100",
        "quantity": 100,
        "remaining": 100,
        "payment_status": "pending",
        "mp_preference_id": "pref-1",
        "mp_external_reference": "idem",
        "price_gross": Decimal("41.99"),
        "price_net_target": Decimal("39.90"),
    }
    defaults.update(kwargs)
    return FiscalEmissionPackage(**defaults)


def _credits_service(package=None, existing_ledger=None):
    repo = AsyncMock()
    repo.get_package.return_value = package
    repo.activate_package.return_value = None
    repo.mark_package_failed.return_value = None
    quota_repo = AsyncMock()
    quota_repo.get_ledger_entry_by_idempotency.return_value = existing_ledger
    quota_service = AsyncMock()
    notification_service = AsyncMock()
    audit_service = AsyncMock()
    return FiscalCreditsService(
        credits_repo=repo,
        mp_client=AsyncMock(),
        quota_service=quota_service,
        quota_repo=quota_repo,
        notification_service=notification_service,
        audit_service=audit_service,
        settings=SimpleNamespace(MP_SANDBOX=True),
    )


def test_valid_signature_accepted():
    assert validate_mp_signature(_headers("123"), "123", SECRET) is True


def test_invalid_signature_rejected():
    headers = _headers("123")
    headers["x-signature"] = headers["x-signature"].replace("a", "b", 1)
    assert validate_mp_signature(headers, "123", SECRET) is False


def test_missing_signature_header_rejected():
    assert validate_mp_signature({"x-request-id": "req-1"}, "123", SECRET) is False


def test_replay_attack_old_ts_rejected():
    old_ts = int(time.time()) - 301
    assert validate_mp_signature(_headers("123", ts=old_ts), "123", SECRET) is False


def test_ts_within_tolerance_accepted():
    accepted_ts = int(time.time()) - 299
    assert validate_mp_signature(_headers("123", ts=accepted_ts), "123", SECRET) is True


@pytest.mark.asyncio
async def test_duplicate_webhook_returns_200_no_reprocess():
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = SimpleNamespace(id=uuid.uuid4(), processing_status="processed")
    credits_service = AsyncMock()
    processor = MercadoPagoWebhookProcessor(webhook_repo, AsyncMock(), credits_service)

    status = await processor.process({"id": "notif-1", "type": "payment", "data": {"id": "pay-1"}}, b"{}", {})

    assert status == 200
    credits_service.activate_package.assert_not_called()


@pytest.mark.asyncio
async def test_different_notification_id_processed():
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    mp_client = AsyncMock()
    mp_client.get_payment.return_value = {"status": "pending", "external_reference": str(uuid.uuid4())}
    processor = MercadoPagoWebhookProcessor(webhook_repo, mp_client, AsyncMock())

    await processor.process({"id": "notif-1", "type": "payment", "action": "payment.updated", "data": {"id": "pay-1"}}, b"{}", {})
    await processor.process({"id": "notif-2", "type": "payment", "action": "payment.updated", "data": {"id": "pay-2"}}, b"{}", {})

    assert webhook_repo.create_event.call_count == 2
    assert mp_client.get_payment.call_count == 2


@pytest.mark.asyncio
async def test_payment_approved_activates_package():
    package_id = uuid.uuid4()
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    mp_client = AsyncMock()
    payment = {"status": "approved", "external_reference": str(package_id), "id": "pay-1"}
    mp_client.get_payment.return_value = payment
    credits_service = AsyncMock()
    processor = MercadoPagoWebhookProcessor(webhook_repo, mp_client, credits_service)

    await processor.process({"id": "notif-1", "type": "payment", "action": "payment.updated", "data": {"id": "pay-1"}}, b"{}", {})

    credits_service.activate_package.assert_called_once_with(
        package_id=package_id,
        mp_payment_id="pay-1",
        payment_data=payment,
    )


@pytest.mark.asyncio
async def test_payment_approved_mp_api_consulted():
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    mp_client = AsyncMock()
    mp_client.get_payment.return_value = {"status": "pending", "external_reference": str(uuid.uuid4())}
    processor = MercadoPagoWebhookProcessor(webhook_repo, mp_client, AsyncMock())

    await processor.process({"id": "notif-1", "type": "payment", "action": "payment.created", "data": {"id": "pay-1"}}, b"{}", {})

    mp_client.get_payment.assert_called_once_with("pay-1")


@pytest.mark.asyncio
async def test_payment_rejected_marks_package_failed():
    package_id = uuid.uuid4()
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    mp_client = AsyncMock()
    mp_client.get_payment.return_value = {
        "status": "rejected",
        "external_reference": str(package_id),
        "status_detail": "cc_rejected",
    }
    credits_service = AsyncMock()
    processor = MercadoPagoWebhookProcessor(webhook_repo, mp_client, credits_service)

    await processor.process({"id": "notif-1", "type": "payment", "action": "payment.updated", "data": {"id": "pay-1"}}, b"{}", {})

    credits_service.mark_package_failed.assert_called_once()


@pytest.mark.asyncio
async def test_payment_cancelled_marks_package_failed():
    package_id = uuid.uuid4()
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    mp_client = AsyncMock()
    mp_client.get_payment.return_value = {"status": "cancelled", "external_reference": str(package_id)}
    credits_service = AsyncMock()
    processor = MercadoPagoWebhookProcessor(webhook_repo, mp_client, credits_service)

    await processor.process({"id": "notif-1", "type": "payment", "action": "payment.updated", "data": {"id": "pay-1"}}, b"{}", {})

    credits_service.mark_package_failed.assert_called_once()


@pytest.mark.asyncio
async def test_payment_pending_no_action():
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    mp_client = AsyncMock()
    mp_client.get_payment.return_value = {"status": "pending", "external_reference": str(uuid.uuid4())}
    credits_service = AsyncMock()
    processor = MercadoPagoWebhookProcessor(webhook_repo, mp_client, credits_service)

    await processor.process({"id": "notif-1", "type": "payment", "action": "payment.updated", "data": {"id": "pay-1"}}, b"{}", {})

    credits_service.activate_package.assert_not_called()
    credits_service.mark_package_failed.assert_not_called()


@pytest.mark.asyncio
async def test_payment_in_process_no_action():
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    mp_client = AsyncMock()
    mp_client.get_payment.return_value = {"status": "in_process", "external_reference": str(uuid.uuid4())}
    credits_service = AsyncMock()
    processor = MercadoPagoWebhookProcessor(webhook_repo, mp_client, credits_service)

    await processor.process({"id": "notif-1", "type": "payment", "action": "payment.updated", "data": {"id": "pay-1"}}, b"{}", {})

    credits_service.activate_package.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_event_type_ignored():
    webhook_repo = AsyncMock()
    webhook_repo.get_event.return_value = None
    webhook_repo.create_event.return_value = SimpleNamespace(id=uuid.uuid4())
    mp_client = AsyncMock()
    processor = MercadoPagoWebhookProcessor(webhook_repo, mp_client, AsyncMock())

    await processor.process({"id": "notif-1", "type": "merchant_order", "data": {"id": "mo-1"}}, b"{}", {})

    mp_client.get_payment.assert_not_called()
    webhook_repo.mark_processed.assert_called_once()


@pytest.mark.asyncio
async def test_activate_package_increments_addon_limit():
    pkg = FakePackage(owner_id=uuid.uuid4(), payment_status="pending")
    svc = _credits_service(package=pkg)

    await svc.activate_package(pkg.id, "pay-1", {"id": "pay-1"})

    svc.quota_service.add_addon_credits.assert_called_once()
    assert svc.quota_service.add_addon_credits.call_args.kwargs["amount"] == 100


@pytest.mark.asyncio
async def test_activate_package_creates_ledger_entry():
    pkg = FakePackage(owner_id=uuid.uuid4(), payment_status="pending")
    svc = _credits_service(package=pkg)

    await svc.activate_package(pkg.id, "pay-1", {"id": "pay-1"})

    assert svc.quota_service.add_addon_credits.call_args.kwargs["idempotency_key"] == "mp_payment:pay-1"


@pytest.mark.asyncio
async def test_activate_package_creates_notification():
    pkg = FakePackage(owner_id=uuid.uuid4(), payment_status="pending")
    svc = _credits_service(package=pkg)

    await svc.activate_package(pkg.id, "pay-1", {"id": "pay-1"})

    svc.notification_service.create_notification.assert_called_once()


@pytest.mark.asyncio
async def test_activate_package_idempotent():
    pkg = FakePackage(owner_id=uuid.uuid4(), payment_status="pending")
    svc = _credits_service(package=pkg, existing_ledger=SimpleNamespace(id=uuid.uuid4()))

    await svc.activate_package(pkg.id, "pay-1", {"id": "pay-1"})

    svc.credits_repo.activate_package.assert_not_called()
    svc.quota_service.add_addon_credits.assert_not_called()


@pytest.mark.asyncio
async def test_activate_package_not_found_logs_error():
    svc = _credits_service(package=None)

    await svc.activate_package(uuid.uuid4(), "pay-1", {"id": "pay-1"})

    svc.quota_service.add_addon_credits.assert_not_called()


@pytest.mark.asyncio
async def test_activate_package_already_paid_no_duplicate():
    pkg = FakePackage(owner_id=uuid.uuid4(), payment_status="paid")
    svc = _credits_service(package=pkg)

    await svc.activate_package(pkg.id, "pay-1", {"id": "pay-1"})

    svc.quota_service.add_addon_credits.assert_not_called()


@pytest.mark.asyncio
async def test_activate_package_audit_log_created():
    pkg = FakePackage(owner_id=uuid.uuid4(), payment_status="pending")
    svc = _credits_service(package=pkg)

    await svc.activate_package(pkg.id, "pay-1", {"id": "pay-1"})

    svc.audit_service.record.assert_called_once()


@pytest.mark.asyncio
async def test_reconcile_finds_pending_packages():
    pkg = FakePackage(owner_id=uuid.uuid4(), created_at=datetime.utcnow() - timedelta(minutes=20))
    repo = AsyncMock()
    repo.list_packages_pending_since.return_value = [pkg]
    mp = AsyncMock()
    mp.search_payments_by_external_reference.return_value = []
    svc = AsyncMock()

    await reconcile_pending_mp_payments({}, credits_repo=repo, mp_client=mp, fiscal_credits_service=svc)

    repo.list_packages_pending_since.assert_called_once()
    mp.search_payments_by_external_reference.assert_called_once_with(str(pkg.id))


@pytest.mark.asyncio
async def test_reconcile_activates_approved_payment():
    pkg = FakePackage(owner_id=uuid.uuid4(), created_at=datetime.utcnow() - timedelta(minutes=20))
    repo = AsyncMock()
    repo.list_packages_pending_since.return_value = [pkg]
    mp = AsyncMock()
    mp.search_payments_by_external_reference.return_value = [{"id": "pay-1", "status": "approved"}]
    svc = AsyncMock()

    await reconcile_pending_mp_payments({}, credits_repo=repo, mp_client=mp, fiscal_credits_service=svc)

    svc.activate_package.assert_called_once_with(
        package_id=pkg.id,
        mp_payment_id="pay-1",
        payment_data={"id": "pay-1", "status": "approved"},
    )


@pytest.mark.asyncio
async def test_reconcile_skips_pending_payments():
    pkg = FakePackage(owner_id=uuid.uuid4(), created_at=datetime.utcnow() - timedelta(minutes=20))
    repo = AsyncMock()
    repo.list_packages_pending_since.return_value = [pkg]
    mp = AsyncMock()
    mp.search_payments_by_external_reference.return_value = [{"id": "pay-1", "status": "pending"}]
    svc = AsyncMock()

    await reconcile_pending_mp_payments({}, credits_repo=repo, mp_client=mp, fiscal_credits_service=svc)

    svc.activate_package.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_idempotent_with_webhook():
    pkg = FakePackage(owner_id=uuid.uuid4(), created_at=datetime.utcnow() - timedelta(minutes=20))
    repo = AsyncMock()
    repo.list_packages_pending_since.return_value = [pkg]
    mp = AsyncMock()
    mp.search_payments_by_external_reference.return_value = [{"id": "pay-1", "status": "approved"}]
    svc = AsyncMock()

    await reconcile_pending_mp_payments({}, credits_repo=repo, mp_client=mp, fiscal_credits_service=svc)
    await reconcile_pending_mp_payments({}, credits_repo=repo, mp_client=mp, fiscal_credits_service=svc)

    assert svc.activate_package.call_count == 2


@pytest.mark.asyncio
async def test_reconcile_handles_mp_api_failure():
    pkg = FakePackage(owner_id=uuid.uuid4(), created_at=datetime.utcnow() - timedelta(minutes=20))
    repo = AsyncMock()
    repo.list_packages_pending_since.return_value = [pkg]
    mp = AsyncMock()
    mp.search_payments_by_external_reference.side_effect = RuntimeError("MP down")
    svc = AsyncMock()

    result = await reconcile_pending_mp_payments({}, credits_repo=repo, mp_client=mp, fiscal_credits_service=svc)

    assert result["checked"] == 1
    assert result["errors"] == 1


@pytest.mark.asyncio
async def test_always_returns_200_even_on_internal_error():
    webhook_repo = AsyncMock()
    webhook_repo.get_event.side_effect = RuntimeError("database unavailable")
    processor = MercadoPagoWebhookProcessor(webhook_repo, AsyncMock(), AsyncMock())

    status = await processor.process({"id": "notif-1", "type": "payment", "data": {"id": "pay-1"}}, b"{}", {})

    assert status == 200
