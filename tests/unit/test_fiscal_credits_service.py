from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.fiscal.fiscal_credits_service import FiscalCreditsService
from domain.fiscal import EMISSION_PACKAGES, FiscalEmissionPackage


@dataclass
class FakeSettings:
    PUBLIC_API_BASE_URL: str = "https://api.marketfy.test/api/v1"
    PUBLIC_FRONTEND_URL: str = "https://app.marketfy.test"
    BILLING_CORE_SYSTEM: str = "marketfy"
    BILLING_CORE_PAYMENT_DUE_DAYS: int = 3
    BILLING_CORE_WEBHOOK_CALLBACK_URL: str = "https://api.marketfy.test/webhooks/billing-core"


def FakePackage(**kwargs) -> FiscalEmissionPackage:
    defaults = {
        "owner_id": uuid.uuid4(),
        "package_slug": "pack_100",
        "quantity": 100,
        "remaining": 100,
        "payment_status": "pending",
    }
    defaults.update(kwargs)
    return FiscalEmissionPackage(**defaults)


def _repo(existing=None):
    repo = AsyncMock()
    repo.get_package_by_idempotency_key.return_value = existing
    repo.create_package.side_effect = lambda **kw: FakePackage(
        owner_id=kw["owner_id"],
        market_id=kw["market_id"],
        purchased_at_market_id=kw["market_id"],
        package_slug=kw["package_slug"],
        quantity=kw["quantity"],
        remaining=kw["remaining"],
        payment_status=kw["payment_status"],
        bc_idempotency_key=kw["bc_idempotency_key"],
        price_gross=kw["price_gross"],
        price_net_target=kw["price_net_target"],
    )
    repo.update_package_preference.return_value = None
    repo.update_package_job_id.return_value = None
    repo.update_package_payment_id.return_value = None
    repo.list_packages_by_owner.return_value = []
    return repo


def _service(repo=None, mp_client=None, quota_service=None, settings=None, bc_client=None, user_repo=None):
    from domain.shared import Email, CPF
    from domain.identity import User, UserRole

    mock_user = User(
        name="Carlos",
        email=Email("c@test.com"),
        cpf=CPF("12345678901"),
        password_hash="hash",
        role=UserRole.OWNER,
        is_active=True,
    )
    if user_repo is None:
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = mock_user

    if bc_client is None:
        bc_client = AsyncMock()
        bc_client.create_payment_link.return_value = {
            "job_id": "job-123",
            "message": "created",
        }
        bc_client.create_customer.return_value = "asaas-customer-123"

    return FiscalCreditsService(
        credits_repo=repo or _repo(),
        mp_client=mp_client or AsyncMock(),
        quota_service=quota_service or AsyncMock(),
        notification_service=AsyncMock(),
        audit_service=AsyncMock(),
        settings=settings or FakeSettings(),
        bc_client=bc_client,
        user_repo=user_repo,
    )


def test_get_packages_returns_three():
    svc = _service()
    assert [p.slug for p in svc.get_packages()] == ["pack_100", "pack_250", "pack_500"]


def test_pack_100_price_gross():
    assert EMISSION_PACKAGES["pack_100"].price_gross == Decimal("41.99")


def test_pack_250_price_gross():
    assert EMISSION_PACKAGES["pack_250"].price_gross == Decimal("73.57")


def test_pack_500_price_gross():
    assert EMISSION_PACKAGES["pack_500"].price_gross == Decimal("126.20")


@pytest.mark.asyncio
async def test_initiate_purchase_creates_preference():
    bc = AsyncMock()
    bc.create_payment_link.return_value = {
        "job_id": "job-123",
        "message": "created",
    }
    repo = _repo()
    svc = _service(repo=repo, bc_client=bc)

    result = await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-1")

    assert result.package.slug == "pack_100"
    assert result.job_id == "job-123"
    bc.create_payment_link.assert_called_once()
    bc.create_payment.assert_not_called()
    bc.create_customer.assert_not_called()
    repo.update_package_job_id.assert_called_once()


@pytest.mark.asyncio
async def test_initiate_purchase_external_reference_is_package_id():
    bc = AsyncMock()
    bc.create_payment_link.return_value = {"job_id": "job-1", "message": "created"}
    svc = _service(bc_client=bc)

    result = await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-2")

    payload = bc.create_payment_link.call_args.kwargs
    assert payload["system_payment_id"] == str(result.package_id)


@pytest.mark.asyncio
async def test_initiate_purchase_notification_url_correct():
    bc = AsyncMock()
    bc.create_payment_link.return_value = {"job_id": "job-1", "message": "created"}
    svc = _service(bc_client=bc)

    await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-3")

    payload = bc.create_payment_link.call_args.kwargs
    assert payload["webhook_link"] == "https://api.marketfy.test/webhooks/billing-core"


@pytest.mark.asyncio
async def test_initiate_purchase_back_urls_from_settings():
    bc = AsyncMock()
    bc.create_payment_link.return_value = {"job_id": "job-1", "message": "created"}
    svc = _service(bc_client=bc)

    await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_250", "idem-4")

    payload = bc.create_payment_link.call_args.kwargs
    assert payload["system"] == "marketfy"


@pytest.mark.asyncio
async def test_initiate_purchase_idempotent_same_key():
    existing = FakePackage(
        owner_id=uuid.uuid4(),
        market_id=uuid.uuid4(),
        package_slug="pack_100",
        quantity=100,
        remaining=100,
        payment_status="pending",
        bc_job_id="job-existing",
    )
    bc = AsyncMock()
    svc = _service(repo=_repo(existing=existing), bc_client=bc)

    result = await svc.initiate_purchase(existing.owner_id, existing.market_id, "pack_100", "idem-same")

    assert result.package_id == existing.id
    assert result.job_id == "job-existing"
    bc.create_payment_link.assert_not_called()


@pytest.mark.asyncio
async def test_initiate_purchase_different_key_new_preference():
    bc = AsyncMock()
    bc.create_payment_link.return_value = {"job_id": "job-new", "message": "created"}
    repo = _repo()
    svc = _service(repo=repo, bc_client=bc)

    await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-a")
    await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-b")

    assert bc.create_payment_link.call_count == 2


@pytest.mark.asyncio
async def test_invalid_package_slug_raises_error():
    svc = _service()
    with pytest.raises(ValueError):
        await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_999", "idem-x")


@pytest.mark.asyncio
async def test_due_date_limit_days_from_settings():
    bc = AsyncMock()
    bc.create_payment_link.return_value = {"job_id": "job-1", "message": "created"}
    svc = _service(bc_client=bc)

    await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-due-date")

    payload = bc.create_payment_link.call_args.kwargs
    assert payload["due_date_limit_days"] == 3
    assert "due_date" not in payload


@pytest.mark.asyncio
async def test_payment_value_mapping():
    bc = AsyncMock()
    bc.create_payment_link.return_value = {"job_id": "job-val", "message": "created"}
    svc = _service(bc_client=bc)

    await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_500", "idem-val")

    payload = bc.create_payment_link.call_args.kwargs
    assert payload["value"] == "126.20"


@pytest.mark.asyncio
async def test_get_credits_history_by_owner():
    owner_id = uuid.uuid4()
    repo = _repo()
    repo.list_packages_by_owner.return_value = [
        FakePackage(owner_id=owner_id, package_slug="pack_100", quantity=100, remaining=40, payment_status="paid")
    ]
    svc = _service(repo=repo)

    history = await svc.get_credits_history(owner_id)

    assert history[0].package_slug == "pack_100"
    assert history[0].remaining == 40


@pytest.mark.asyncio
async def test_get_credits_balance_correct_remaining():
    quota = AsyncMock()
    quota.get_quota_status.return_value = SimpleNamespace(
        period="202605",
        included_limit=200,
        addon_limit=100,
        addon_total=250,
        used_count=120,
        reserved_count=0,
        remaining=180,
        percentage_used=40.0,
    )
    svc = _service(quota_service=quota)

    balance = await svc.get_credits_balance(uuid.uuid4(), period="202605")

    assert balance.remaining == 180
    assert balance.addon_limit == 100


@pytest.mark.asyncio
async def test_initiate_purchase_does_not_create_or_reuse_customer():
    bc = AsyncMock()
    user_repo = AsyncMock()
    from domain.shared import Email, CPF
    from domain.identity import User, UserRole
    user = User(
        name="Carlos",
        email=Email("c@test.com"),
        cpf=CPF("12345678901"),
        password_hash="hash",
        role=UserRole.OWNER,
        is_active=True,
        asaas_customer_id="asaas-existing-123"
    )
    user_repo.get_by_id.return_value = user
    svc = _service(bc_client=bc, user_repo=user_repo)

    await svc.initiate_purchase(user.id, uuid.uuid4(), "pack_100", "idem-ensure")

    bc.create_customer.assert_not_called()
    bc.create_payment.assert_not_called()
    payload = bc.create_payment_link.call_args.kwargs
    assert "customer_provider_id" not in payload


@pytest.mark.asyncio
async def test_missing_document_does_not_block_payment_link_checkout():
    user_repo = AsyncMock()
    from domain.shared import Email
    from domain.identity import User, UserRole
    user = User(
        name="Carlos",
        email=Email("c@test.com"),
        cpf=None,
        password_hash="hash",
        role=UserRole.OWNER,
        is_active=True,
    )

    user_repo.get_by_id.return_value = user
    bc = AsyncMock()
    bc.create_payment_link.return_value = {"job_id": "job-nodoc", "message": "created"}
    svc = _service(user_repo=user_repo, bc_client=bc)

    result = await svc.initiate_purchase(user.id, uuid.uuid4(), "pack_100", "idem-nodoc")

    assert result.job_id == "job-nodoc"
    bc.create_customer.assert_not_called()
    bc.create_payment_link.assert_called_once()


@pytest.mark.asyncio
async def test_initiate_custom_purchase_uses_payment_link_without_customer():
    user_repo = AsyncMock()
    from domain.shared import Email
    from domain.identity import User, UserRole
    user = User(
        name="Carlos",
        email=Email("c@test.com"),
        cpf=None,
        password_hash="hash",
        role=UserRole.OWNER,
        is_active=True,
    )
    user_repo.get_by_id.return_value = user
    bc = AsyncMock()
    bc.create_payment_link.return_value = {"job_id": "job-custom", "message": "created"}
    svc = _service(user_repo=user_repo, bc_client=bc)

    result = await svc.initiate_custom_purchase(
        owner_id=user.id,
        market_id=uuid.uuid4(),
        quantity=42,
        price_gross=Decimal("30.24"),
        idempotency_key="idem-custom",
    )

    assert result.job_id == "job-custom"
    payload = bc.create_payment_link.call_args.kwargs
    assert payload["value"] == "30.24"
    assert payload["description"] == "Creditos NF-e - custom_42"
    assert payload["due_date_limit_days"] == 3
    assert "customer_provider_id" not in payload
    bc.create_customer.assert_not_called()
    bc.create_payment.assert_not_called()


