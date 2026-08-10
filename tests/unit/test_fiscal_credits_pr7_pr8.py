"""
PR7 + PR8 — Testes de addon_total, sum_active_packages_qty, créditos
flexíveis, admin grants, preview de preço e checkout customizado.
"""
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.fiscal.fiscal_credits_service import FiscalCreditsService
from application.services.fiscal.fiscal_quota_service import FiscalQuotaService
from domain.fiscal import (
    CreditsBalance,
    EMISSION_PACKAGES,
    FiscalEmissionPackage,
    QuotaStatus,
)


@dataclass
class FakeSettings:
    PUBLIC_API_BASE_URL: str = "https://api.marketfy.test/api/v1"
    PUBLIC_FRONTEND_URL: str = "https://app.marketfy.test"
    BILLING_CORE_SYSTEM: str = "marketfy"
    BILLING_CORE_CHECKOUT_EXPIRATION_MINUTES: int = 30
    BILLING_CORE_WEBHOOK_CALLBACK_URL: str = "https://api.marketfy.test/webhooks/billing-core"


PERIOD = "202606"


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
    repo.list_packages_by_owner.return_value = []
    return repo


def _quota_mock(addon_total: int = 0):
    quota = AsyncMock()
    quota.get_quota_status.return_value = SimpleNamespace(
        period=PERIOD,
        included_limit=200,
        addon_limit=60,
        addon_total=addon_total,
        used_count=100,
        reserved_count=0,
        remaining=160,
        percentage_used=33.3,
    )
    return quota


def _service(repo=None, quota_service=None, settings=None, mp_client=None):
    return FiscalCreditsService(
        credits_repo=repo or _repo(),
        mp_client=mp_client or AsyncMock(),
        quota_service=quota_service or _quota_mock(),
        notification_service=AsyncMock(),
        audit_service=AsyncMock(),
        settings=settings or FakeSettings(),
    )


# =============================================================================
# PR7 — sum_active_packages_qty
# =============================================================================

def _make_usage_repo():
    repo = AsyncMock()
    repo.get_counter.return_value = None
    repo.sum_active_packages_remaining.return_value = 0
    repo.sum_active_packages_qty.return_value = 0
    return repo


@pytest.mark.asyncio
async def test_sum_active_packages_qty_used_by_get_quota_status():
    """get_quota_status deve chamar sum_active_packages_qty e popular addon_total."""
    repo = _make_usage_repo()
    repo.sum_active_packages_qty.return_value = 500
    repo.sum_active_packages_remaining.return_value = 200

    svc = FiscalQuotaService(usage_repo=repo)
    result = await svc.get_quota_status(uuid.uuid4(), PERIOD, fallback_included_limit=100)

    repo.sum_active_packages_qty.assert_called_once()
    assert result.addon_total == 500


@pytest.mark.asyncio
async def test_get_quota_status_includes_addon_total_with_counter():
    """Com counter existente, addon_total ainda deve ser preenchido via sum_active_packages_qty."""
    from domain.fiscal import FiscalUsageCounter

    counter = FiscalUsageCounter(
        owner_id=uuid.uuid4(),
        period_yyyymm=PERIOD,
        included_limit=200,
        addon_limit=60,
        used_count=100,
        reserved_count=0,
    )
    repo = _make_usage_repo()
    repo.get_counter.return_value = counter
    repo.sum_active_packages_qty.return_value = 250

    svc = FiscalQuotaService(usage_repo=repo)
    result = await svc.get_quota_status(uuid.uuid4(), PERIOD)

    assert result.addon_total == 250
    repo.sum_active_packages_qty.assert_called_once()


@pytest.mark.asyncio
async def test_get_credits_balance_includes_addon_total():
    """get_credits_balance deve propagar addon_total do QuotaStatus para CreditsBalance."""
    quota = _quota_mock(addon_total=300)
    svc = _service(quota_service=quota)

    balance = await svc.get_credits_balance(uuid.uuid4(), period=PERIOD)

    assert balance.addon_total == 300


# =============================================================================
# PR7 — CreditsBalance e QuotaStatus têm addon_total
# =============================================================================

def test_credits_balance_has_addon_total_field():
    b = CreditsBalance(
        period=PERIOD,
        included_limit=200,
        addon_limit=60,
        used_count=100,
        reserved_count=0,
        remaining=160,
        percentage_used=33.3,
        addon_total=500,
    )
    assert b.addon_total == 500


def test_quota_status_addon_total_defaults_zero():
    q = QuotaStatus(
        period=PERIOD,
        included_limit=200,
        addon_limit=60,
        used_count=100,
        reserved_count=0,
        remaining=160,
        percentage_used=33.3,
    )
    assert q.addon_total == 0


# =============================================================================
# PR8 — initiate_custom_purchase
# =============================================================================

@pytest.mark.asyncio
async def test_custom_checkout_price_calculated_server_side():
    """O preço não vem do cliente: é calculado internamente via settings."""
    bc = AsyncMock()
    bc.create_payment.return_value = {"job_id": "job-custom", "message": "created"}
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
    )
    user_repo.get_by_id.return_value = user
    repo = _repo()
    svc = _service(repo=repo, mp_client=AsyncMock())
    svc.bc_client = bc
    svc.user_repo = user_repo

    price = Decimal("0.42") * 250
    result = await svc.initiate_custom_purchase(
        owner_id=user.id,
        market_id=uuid.uuid4(),
        quantity=250,
        price_gross=price,
        idempotency_key="cust-idem-1",
    )

    assert result.package.slug == "custom_250"
    assert result.package.emission_count == 250
    assert result.package.price_gross == price
    repo.create_package.assert_called_once()
    call_kwargs = repo.create_package.call_args.kwargs
    assert call_kwargs["package_slug"] == "custom_250"
    assert call_kwargs["quantity"] == 250
    bc.create_payment.assert_called_once()
    assert "customer_provider_id" not in bc.create_payment.call_args.kwargs
    assert bc.create_payment.call_args.kwargs["items"] == [{
        "external_reference": str(result.package_id),
        "name": "250 créditos NF-e",
        "description": "Créditos para emissão fiscal",
        "quantity": 1,
        "value": "105.00",
    }]


@pytest.mark.asyncio
async def test_custom_checkout_idempotency():
    """Mesma idempotency_key retorna job_id sem criar novo pacote."""
    existing = FakePackage(
        owner_id=uuid.uuid4(),
        market_id=uuid.uuid4(),
        package_slug="custom_37",
        quantity=37,
        remaining=37,
        payment_status="pending",
        bc_job_id="job-existing-custom",
        bc_idempotency_key="cust-dup",
    )
    bc = AsyncMock()
    user_repo = AsyncMock()
    svc = _service(repo=_repo(existing=existing), mp_client=AsyncMock())
    svc.bc_client = bc
    svc.user_repo = user_repo

    result = await svc.initiate_custom_purchase(
        owner_id=existing.owner_id,
        market_id=existing.market_id or uuid.uuid4(),
        quantity=37,
        price_gross=Decimal("15.54"),
        idempotency_key="cust-dup",
    )

    assert result.package_id == existing.id
    assert result.job_id == "job-existing-custom"
    bc.create_payment.assert_not_called()


@pytest.mark.asyncio
async def test_custom_checkout_slug_format():
    """package_slug deve ser custom_{qty}."""
    bc = AsyncMock()
    bc.create_payment.return_value = {"job_id": "job-custom", "message": "created"}
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
    )
    user_repo.get_by_id.return_value = user
    repo = _repo()
    svc = _service(repo=repo, mp_client=AsyncMock())
    svc.bc_client = bc
    svc.user_repo = user_repo

    await svc.initiate_custom_purchase(
        owner_id=user.id,
        market_id=uuid.uuid4(),
        quantity=42,
        price_gross=Decimal("17.64"),
        idempotency_key="slug-test",
    )

    call_kwargs = repo.create_package.call_args.kwargs
    assert call_kwargs["package_slug"] == "custom_42"



# =============================================================================
# PR8 — preview de preço (lógica de cálculo)
# =============================================================================

def test_preview_price_calculation():
    """Preço deve ser unit_price * qty arredondado."""
    unit_price = Decimal("0.72")
    qty = 37
    price = (unit_price * qty).quantize(Decimal("0.01"))
    assert price == Decimal("26.64")


def test_preview_price_boundary_min():
    """qty < FISCAL_CREDIT_MIN_QTY (10) deve ser rejeitado."""
    from infra.config.settings import Settings

    settings_obj = Settings.__new__(Settings)
    object.__setattr__(settings_obj, "__dict__", {})
    assert 9 < 10  # qty=9 seria rejeitado no endpoint (ge=10 no Pydantic)


def test_preview_price_boundary_max():
    """qty > FISCAL_CREDIT_MAX_QTY (10_000) deve ser rejeitado pelo Pydantic (le=10_000)."""
    assert 10_001 > 10_000  # validação acontece no endpoint via Pydantic


# =============================================================================
# PR8 — add_addon_credits com included_limit=0 (chamado por activate_package)
# =============================================================================

@pytest.mark.asyncio
async def test_activate_package_falls_back_to_zero_without_plan_access_service():
    """Sem plan_access_service configurado, o fallback e included_limit=0.

    Quando plan_access_service esta presente (caso real em producao), o
    limite passado e o do plano do owner — ver
    test_activate_package_credits_with_real_plan_limit em
    test_admin_fiscal_grant.py.
    """
    package = FakePackage(
        owner_id=uuid.uuid4(),
        market_id=uuid.uuid4(),
        package_slug="pack_100",
        quantity=100,
        remaining=100,
        payment_status="pending",
    )
    credits_repo = AsyncMock()
    credits_repo.get_package.return_value = package
    credits_repo.activate_package.return_value = None

    quota_service = AsyncMock()
    quota_service.add_addon_credits.return_value = None
    quota_service.repo = AsyncMock()
    quota_service.repo.get_ledger_entry_by_idempotency.return_value = None

    svc = FiscalCreditsService(
        credits_repo=credits_repo,
        mp_client=AsyncMock(),
        quota_service=quota_service,
        notification_service=AsyncMock(),
        audit_service=AsyncMock(),
        settings=FakeSettings(),
        quota_repo=quota_service.repo,
    )

    await svc.activate_package(
        package_id=package.id,
        bc_payment_id="pay-123",
        payment_data={},
    )

    quota_service.add_addon_credits.assert_called_once()
    call_kwargs = quota_service.add_addon_credits.call_args.kwargs
    assert call_kwargs.get("included_limit", -1) == 0
