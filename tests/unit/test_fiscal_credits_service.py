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
from infra.clients.mercado_pago_client import (
    MercadoPagoAuthError,
    MercadoPagoClient,
    MercadoPagoUnavailableError,
    MercadoPagoValidationError,
)


@dataclass
class FakeSettings:
    MP_SANDBOX: bool = True
    PUBLIC_API_BASE_URL: str = "https://api.marketfy.test/api/v1"
    FISCAL_CREDITS_BACK_URL_SUCCESS: str = "https://app.marketfy.test/success"
    FISCAL_CREDITS_BACK_URL_FAILURE: str = "https://app.marketfy.test/failure"
    FISCAL_CREDITS_BACK_URL_PENDING: str = "https://app.marketfy.test/pending"


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
    repo.get_package_by_external_ref.return_value = existing
    repo.create_package.side_effect = lambda **kw: FakePackage(
        owner_id=kw["owner_id"],
        market_id=kw["market_id"],
        purchased_at_market_id=kw["market_id"],
        package_slug=kw["package_slug"],
        quantity=kw["quantity"],
        remaining=kw["remaining"],
        payment_status=kw["payment_status"],
        mp_external_reference=kw["mp_external_reference"],
        price_gross=kw["price_gross"],
        price_net_target=kw["price_net_target"],
    )
    repo.update_package_preference.return_value = None
    repo.list_packages_by_owner.return_value = []
    return repo


def _service(repo=None, mp_client=None, quota_service=None, settings=None):
    return FiscalCreditsService(
        credits_repo=repo or _repo(),
        mp_client=mp_client or AsyncMock(),
        quota_service=quota_service or AsyncMock(),
        notification_service=AsyncMock(),
        audit_service=AsyncMock(),
        settings=settings or FakeSettings(),
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
    mp = AsyncMock()
    mp.create_preference.return_value = {
        "id": "pref-123",
        "init_point": "https://mp/init",
        "sandbox_init_point": "https://mp/sandbox",
    }
    repo = _repo()
    svc = _service(repo=repo, mp_client=mp)

    result = await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-1")

    assert result.package.slug == "pack_100"
    assert result.init_point == "https://mp/sandbox"
    mp.create_preference.assert_called_once()
    repo.update_package_preference.assert_called_once()


@pytest.mark.asyncio
async def test_initiate_purchase_external_reference_is_package_id():
    mp = AsyncMock()
    mp.create_preference.return_value = {"id": "pref-1", "init_point": "i", "sandbox_init_point": "s"}
    svc = _service(mp_client=mp)

    result = await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-2")

    payload = mp.create_preference.call_args.args[0]
    assert payload["external_reference"] == str(result.package_id)


@pytest.mark.asyncio
async def test_initiate_purchase_notification_url_correct():
    mp = AsyncMock()
    mp.create_preference.return_value = {"id": "pref-1", "init_point": "i", "sandbox_init_point": "s"}
    svc = _service(mp_client=mp)

    await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-3")

    payload = mp.create_preference.call_args.args[0]
    assert payload["notification_url"] == "https://api.marketfy.test/api/v1/webhooks/mercado-pago"


@pytest.mark.asyncio
async def test_initiate_purchase_back_urls_from_settings():
    mp = AsyncMock()
    mp.create_preference.return_value = {"id": "pref-1", "init_point": "i", "sandbox_init_point": "s"}
    svc = _service(mp_client=mp)

    await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_250", "idem-4")

    payload = mp.create_preference.call_args.args[0]
    assert payload["back_urls"]["success"] == "https://app.marketfy.test/success"
    assert payload["back_urls"]["failure"] == "https://app.marketfy.test/failure"
    assert payload["back_urls"]["pending"] == "https://app.marketfy.test/pending"


@pytest.mark.asyncio
async def test_initiate_purchase_idempotent_same_key():
    existing = FakePackage(
        owner_id=uuid.uuid4(),
        market_id=uuid.uuid4(),
        package_slug="pack_100",
        quantity=100,
        remaining=100,
        payment_status="pending",
        mp_preference_id="pref-existing",
    )
    mp = AsyncMock()
    svc = _service(repo=_repo(existing=existing), mp_client=mp)

    result = await svc.initiate_purchase(existing.owner_id, existing.market_id, "pack_100", "idem-same")

    assert result.package_id == existing.id
    assert "pref-existing" in result.init_point
    mp.create_preference.assert_not_called()


@pytest.mark.asyncio
async def test_initiate_purchase_different_key_new_preference():
    mp = AsyncMock()
    mp.create_preference.return_value = {"id": "pref-new", "init_point": "i", "sandbox_init_point": "s"}
    repo = _repo()
    svc = _service(repo=repo, mp_client=mp)

    await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-a")
    await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-b")

    assert mp.create_preference.call_count == 2


@pytest.mark.asyncio
async def test_invalid_package_slug_raises_error():
    svc = _service()
    with pytest.raises(ValueError):
        await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_999", "idem-x")


@pytest.mark.asyncio
async def test_sandbox_returns_sandbox_init_point():
    mp = AsyncMock()
    mp.create_preference.return_value = {"id": "pref-1", "init_point": "prod", "sandbox_init_point": "sandbox"}
    svc = _service(mp_client=mp, settings=FakeSettings(MP_SANDBOX=True))
    result = await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-sandbox")
    assert result.init_point == "sandbox"


@pytest.mark.asyncio
async def test_production_returns_init_point():
    mp = AsyncMock()
    mp.create_preference.return_value = {"id": "pref-1", "init_point": "prod", "sandbox_init_point": "sandbox"}
    svc = _service(mp_client=mp, settings=FakeSettings(MP_SANDBOX=False))
    result = await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-prod")
    assert result.init_point == "prod"


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
async def test_preference_expiration_24h():
    mp = AsyncMock()
    mp.create_preference.return_value = {"id": "pref-1", "init_point": "i", "sandbox_init_point": "s"}
    svc = _service(mp_client=mp)

    await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-exp")

    payload = mp.create_preference.call_args.args[0]
    expiration = datetime.fromisoformat(payload["expiration_date_to"].replace("Z", ""))
    delta = expiration - datetime.utcnow()
    assert timedelta(hours=23, minutes=59) <= delta <= timedelta(hours=24, minutes=1)


@pytest.mark.asyncio
async def test_statement_descriptor_is_marketfy():
    mp = AsyncMock()
    mp.create_preference.return_value = {"id": "pref-1", "init_point": "i", "sandbox_init_point": "s"}
    svc = _service(mp_client=mp)

    await svc.initiate_purchase(uuid.uuid4(), uuid.uuid4(), "pack_100", "idem-desc")

    payload = mp.create_preference.call_args.args[0]
    assert payload["statement_descriptor"] == "MARKETFY"


@pytest.mark.asyncio
async def test_mp_client_auth_error_propagated():
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"message": "bad token"}))
    client = MercadoPagoClient("secret", transport=transport)
    with pytest.raises(MercadoPagoAuthError):
        await client.create_preference({"items": []})


@pytest.mark.asyncio
async def test_mp_client_validation_error_propagated():
    transport = httpx.MockTransport(lambda request: httpx.Response(422, json={"message": "invalid"}))
    client = MercadoPagoClient("secret", transport=transport)
    with pytest.raises(MercadoPagoValidationError):
        await client.create_preference({"items": []})


@pytest.mark.asyncio
async def test_mp_client_timeout_propagated():
    async def handler(request):
        raise httpx.TimeoutException("timeout", request=request)

    client = MercadoPagoClient("secret", transport=httpx.MockTransport(handler))
    with pytest.raises(MercadoPagoUnavailableError):
        await client.create_preference({"items": []})
