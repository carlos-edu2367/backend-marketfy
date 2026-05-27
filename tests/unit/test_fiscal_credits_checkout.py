from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.fiscal.fiscal_credits_service import FiscalCreditsService
from domain.fiscal import EMISSION_PACKAGES, FiscalEmissionPackage, PurchaseInitResult
from domain.identity import User, UserRole
from domain.shared import CPF, CNPJ, Email
from infra.web.main import app

@dataclass
class FakeSettings:
    BILLING_CORE_ENABLED: bool = True
    BILLING_CORE_SYSTEM: str = "marketfy"
    BILLING_CORE_PAYMENT_DUE_DAYS: int = 3
    BILLING_CORE_WEBHOOK_CALLBACK_URL: str = "https://api.marketfy.test/webhooks/billing-core"


def FakeUser(**kwargs) -> User:
    defaults = {
        "name": "Carlos",
        "email": Email("carlos@marketfy.test"),
        "cpf": CPF("12345678901"),
        "password_hash": "hash",
        "role": UserRole.OWNER,
        "is_active": True,
        "cnpj": None,
        "asaas_customer_id": None,
    }
    defaults.update(kwargs)
    u = User(**defaults)
    u.id = kwargs.get("id", uuid.uuid4())
    return u


def FakePackage(**kwargs) -> FiscalEmissionPackage:
    defaults = {
        "owner_id": uuid.uuid4(),
        "package_slug": "pack_100",
        "quantity": 100,
        "remaining": 100,
        "payment_status": "pending",
        "bc_job_id": None,
        "bc_payment_id": None,
        "bc_idempotency_key": "idem-1",
        "price_gross": Decimal("41.99"),
        "price_net_target": Decimal("39.90"),
    }
    defaults.update(kwargs)
    return FiscalEmissionPackage(**defaults)


def _repo(existing=None):
    repo = AsyncMock()
    repo.get_package_by_idempotency_key.return_value = existing
    repo.get_package_by_job_id.return_value = existing
    repo.create_package.side_effect = lambda **kw: FakePackage(
        owner_id=kw["owner_id"],
        market_id=kw["market_id"],
        package_slug=kw["package_slug"],
        quantity=kw["quantity"],
        remaining=kw["remaining"],
        payment_status=kw["payment_status"],
        bc_idempotency_key=kw["bc_idempotency_key"],
        price_gross=kw["price_gross"],
        price_net_target=kw["price_net_target"],
    )
    repo.update_package_job_id.return_value = None
    repo.update_package_payment_id.return_value = None
    return repo


def _service(repo=None, bc_client=None, user_repo=None, settings=None):
    return FiscalCreditsService(
        credits_repo=repo or _repo(),
        mp_client=AsyncMock(),
        bc_client=bc_client or AsyncMock(),
        user_repo=user_repo or AsyncMock(),
        quota_service=AsyncMock(),
        notification_service=AsyncMock(),
        audit_service=AsyncMock(),
        settings=settings or FakeSettings(),
    )


@pytest.mark.asyncio
async def test_ensure_customer_provider_id_reuses_existing():
    user = FakeUser(asaas_customer_id="cus_existing123")
    bc_client = AsyncMock()
    user_repo = AsyncMock()
    svc = _service(bc_client=bc_client, user_repo=user_repo)

    result = await svc._ensure_customer_provider_id(user)

    assert result == "cus_existing123"
    bc_client.create_customer.assert_not_called()
    user_repo.update_asaas_customer_id.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_customer_provider_id_creates_new():
    user = FakeUser(asaas_customer_id=None, name="Carlos Souza", email=Email("carlos@test.com"), cpf=CPF("98765432109"))
    bc_client = AsyncMock()
    bc_client.create_customer.return_value = {"provider_customer_id": "cus_new987"}
    user_repo = AsyncMock()
    svc = _service(bc_client=bc_client, user_repo=user_repo)

    result = await svc._ensure_customer_provider_id(user)

    assert result == "cus_new987"
    bc_client.create_customer.assert_called_once_with(
        nome_completo="Carlos Souza",
        email="carlos@test.com",
        system_customer_id=str(user.id),
        system="marketfy",
        cpf="98765432109",
        cnpj=None,
    )
    user_repo.update_asaas_customer_id.assert_called_once_with(user.id, "cus_new987")


@pytest.mark.asyncio
async def test_ensure_customer_provider_id_raises_value_error_if_no_doc():
    user = FakeUser(asaas_customer_id=None, cpf=None, cnpj=None)
    # Force user document to be empty
    object.__setattr__(user, "cpf", None)
    object.__setattr__(user, "cnpj", None)

    svc = _service()
    with pytest.raises(ValueError) as exc:
        await svc._ensure_customer_provider_id(user)
    assert str(exc.value) == "customer_document_missing"


@pytest.mark.asyncio
async def test_initiate_purchase_calls_create_payment():
    user = FakeUser(asaas_customer_id="cus_123")
    user_repo = AsyncMock()
    user_repo.get_by_id.return_value = user

    bc_client = AsyncMock()
    bc_client.create_payment.return_value = {"job_id": "job_123", "message": "created"}

    repo = _repo()
    svc = _service(repo=repo, bc_client=bc_client, user_repo=user_repo)

    res = await svc.initiate_purchase(user.id, uuid.uuid4(), "pack_100", "idem-1")

    assert res.job_id == "job_123"
    bc_client.create_payment.assert_called_once()
    repo.update_package_job_id.assert_called_once_with(res.package_id, job_id="job_123")


@pytest.mark.asyncio
async def test_initiate_purchase_idempotent_same_key():
    user = FakeUser(asaas_customer_id="cus_123")
    user_repo = AsyncMock()
    user_repo.get_by_id.return_value = user

    existing = FakePackage(bc_job_id="job_existing", bc_idempotency_key="idem-same")
    repo = _repo(existing=existing)
    bc_client = AsyncMock()
    svc = _service(repo=repo, bc_client=bc_client, user_repo=user_repo)

    res = await svc.initiate_purchase(user.id, uuid.uuid4(), "pack_100", "idem-same")

    assert res.job_id == "job_existing"
    bc_client.create_payment.assert_not_called()


@pytest.mark.asyncio
async def test_get_checkout_status_completed():
    bc_client = AsyncMock()
    bc_client.get_job.return_value = {
        "job_id": "job-1",
        "status": "completed",
        "result": {
            "checkout_url": "https://www.asaas.com/c/123",
            "payment_id": "pay_abc",
        }
    }
    existing = FakePackage(bc_job_id="job-1")
    repo = _repo(existing=existing)
    svc = _service(repo=repo, bc_client=bc_client)

    status_res = await svc.get_checkout_status("job-1")

    assert status_res["status"] == "completed"
    assert status_res["checkout_url"] == "https://www.asaas.com/c/123"
    assert status_res["bc_payment_id"] == "pay_abc"
    repo.get_package_by_job_id.assert_called_once_with("job-1")
    repo.update_package_payment_id.assert_called_once_with(existing.id, "pay_abc")


@pytest.mark.asyncio
async def test_get_checkout_status_processing():
    bc_client = AsyncMock()
    bc_client.get_job.return_value = {
        "job_id": "job-1",
        "status": "processing",
        "result": None,
    }
    repo = _repo()
    svc = _service(repo=repo, bc_client=bc_client)

    status_res = await svc.get_checkout_status("job-1")

    assert status_res["status"] == "processing"
    assert status_res["checkout_url"] is None
    assert status_res["bc_payment_id"] is None
    repo.update_package_payment_id.assert_not_called()


@pytest.mark.asyncio
async def test_get_checkout_status_done_mapped_to_completed():
    bc_client = AsyncMock()
    bc_client.get_job.return_value = {
        "job_id": "job-1",
        "status": "done",
        "result": {
            "checkout_url": "https://www.asaas.com/c/123",
            "payment_id": "pay_abc",
        }
    }
    existing = FakePackage(bc_job_id="job-1")
    repo = _repo(existing=existing)
    svc = _service(repo=repo, bc_client=bc_client)

    status_res = await svc.get_checkout_status("job-1")

    assert status_res["status"] == "completed"
    assert status_res["checkout_url"] == "https://www.asaas.com/c/123"
    assert status_res["bc_payment_id"] == "pay_abc"
