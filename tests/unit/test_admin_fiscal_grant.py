"""
Testes da concessão administrativa de créditos NFC-e.

Cobre:
- Domínio: categorias, rótulos, campos de grant
- FiscalCreditsService.grant_credits: criação de pacote, idempotência,
  included_limit correto, auditoria, notificação
"""
import os
import sys
import uuid

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.fiscal import (
    GRANT_REASON_LABELS,
    PACKAGE_TYPE_ADMIN_GRANT,
    FiscalEmissionPackage,
    GrantReasonCode,
    PackageHistoryItem,
)


def test_package_type_admin_grant_constant():
    assert PACKAGE_TYPE_ADMIN_GRANT == "nfce_admin_grant"


def test_grant_reason_codes():
    assert {c.value for c in GrantReasonCode} == {
        "courtesy",
        "compensation",
        "bonus",
        "migration",
    }


def test_every_reason_code_has_a_user_facing_label():
    for code in GrantReasonCode:
        assert GRANT_REASON_LABELS[code.value]
        assert isinstance(GRANT_REASON_LABELS[code.value], str)


def test_package_carries_grant_fields():
    admin_id = uuid.uuid4()
    package = FiscalEmissionPackage(
        owner_id=uuid.uuid4(),
        package_type=PACKAGE_TYPE_ADMIN_GRANT,
        quantity=500,
        remaining=500,
        payment_status="paid",
        grant_reason_code="courtesy",
        grant_note="nota interna",
        granted_by_id=admin_id,
    )
    assert package.grant_reason_code == "courtesy"
    assert package.grant_note == "nota interna"
    assert package.granted_by_id == admin_id


def test_history_item_defaults_to_purchase_shape():
    """Pacotes antigos (compras) continuam válidos sem os campos novos."""
    package = FiscalEmissionPackage(
        owner_id=uuid.uuid4(),
        quantity=100,
        remaining=40,
        payment_status="paid",
    )
    item = PackageHistoryItem.from_package(package)
    assert item.package_type == "nfce_addon"
    assert item.grant_reason_code is None


def test_history_item_exposes_grant_metadata():
    package = FiscalEmissionPackage(
        owner_id=uuid.uuid4(),
        package_type=PACKAGE_TYPE_ADMIN_GRANT,
        quantity=500,
        remaining=500,
        payment_status="paid",
        grant_reason_code="compensation",
        grant_note="segredo interno",
        granted_by_id=uuid.uuid4(),
    )
    item = PackageHistoryItem.from_package(package)
    assert item.package_type == PACKAGE_TYPE_ADMIN_GRANT
    assert item.grant_reason_code == "compensation"
    # grant_note e granted_by_id NAO podem vazar para o item de historico
    assert not hasattr(item, "grant_note")
    assert not hasattr(item, "granted_by_id")


from unittest.mock import AsyncMock, MagicMock

from infra.repositories.fiscal_repo import _to_package


def test_to_package_maps_grant_columns():
    admin_id = uuid.uuid4()
    model = MagicMock()
    model.id = uuid.uuid4()
    model.owner_id = uuid.uuid4()
    model.package_type = PACKAGE_TYPE_ADMIN_GRANT
    model.quantity = 500
    model.remaining = 500
    model.valid_from = None
    model.valid_until = None
    model.billing_subscription_id = None
    model.payment_status = "paid"
    model.market_id = None
    model.package_slug = "admin_grant"
    model.bc_job_id = None
    model.bc_payment_id = None
    model.bc_idempotency_key = "key-12345678"
    model.price_gross = None
    model.price_net_target = None
    model.purchased_at_market_id = None
    model.created_at = None
    model.grant_reason_code = "courtesy"
    model.grant_note = "nota interna"
    model.granted_by_id = admin_id

    package = _to_package(model)

    assert package.package_type == PACKAGE_TYPE_ADMIN_GRANT
    assert package.grant_reason_code == "courtesy"
    assert package.grant_note == "nota interna"
    assert package.granted_by_id == admin_id


from datetime import datetime, timedelta

from infra.repositories.fiscal_repo import SQLAlchemyFiscalUsageRepository


@pytest.mark.asyncio
async def test_create_grant_package_sets_owner_scoped_paid_package():
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    repo = SQLAlchemyFiscalUsageRepository(session)

    owner_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    now = datetime(2026, 8, 10, 12, 0, 0)

    package = await repo.create_grant_package(
        owner_id=owner_id,
        quantity=500,
        valid_from=now,
        valid_until=now + timedelta(days=365),
        grant_reason_code="courtesy",
        grant_note="nota interna",
        granted_by_id=admin_id,
        idempotency_key="idem-12345678",
    )

    assert package.owner_id == owner_id
    assert package.package_type == PACKAGE_TYPE_ADMIN_GRANT
    assert package.package_slug == "admin_grant"
    assert package.payment_status == "paid"
    assert package.quantity == 500
    assert package.remaining == 500
    assert package.price_gross == 0
    assert package.valid_until == now + timedelta(days=365)
    # Credito e do owner, nao de uma loja
    assert package.market_id is None
    assert package.purchased_at_market_id is None
    # Sem cobranca no Billing Core
    assert package.bc_job_id is None
    assert package.bc_payment_id is None
    assert package.bc_idempotency_key == "idem-12345678"
    session.flush.assert_awaited_once()


from decimal import Decimal

from application.services.fiscal.fiscal_credits_service import FiscalCreditsService


def _make_credits_service(**overrides):
    """FiscalCreditsService com todas as dependências mockadas."""
    credits_repo = AsyncMock()
    credits_repo.session = AsyncMock()
    quota_service = AsyncMock()
    plan_access = AsyncMock()
    plan_access.get_fiscal_monthly_limit.return_value = 200

    kwargs = dict(
        credits_repo=credits_repo,
        quota_repo=credits_repo,
        quota_service=quota_service,
        notification_service=AsyncMock(),
        audit_service=AsyncMock(),
        settings=MagicMock(),
        plan_access_service=plan_access,
        user_repo=AsyncMock(),
        bc_client=AsyncMock(),
    )
    kwargs.update(overrides)
    return FiscalCreditsService(**kwargs)


@pytest.mark.asyncio
async def test_activate_package_credits_with_real_plan_limit():
    """Regressivo: ativar addon nao pode criar counter com included_limit=0."""
    svc = _make_credits_service()
    owner_id = uuid.uuid4()
    package = FiscalEmissionPackage(
        owner_id=owner_id,
        quantity=100,
        remaining=100,
        payment_status="pending",
        package_slug="pack_100",
        price_gross=Decimal("41.99"),
    )
    svc.credits_repo.get_package.return_value = package
    svc.quota_repo.get_ledger_entry_by_idempotency.return_value = None
    svc.credits_repo.activate_package.return_value = 1

    await svc.activate_package(package.id, "bc-pay-1", {})

    svc.plan_access_service.get_fiscal_monthly_limit.assert_awaited_once_with(owner_id)
    kwargs = svc.quota_service.add_addon_credits.await_args.kwargs
    assert kwargs["included_limit"] == 200


def _grant_service():
    svc = _make_credits_service()
    svc.credits_repo.get_package_by_idempotency_key.return_value = None
    return svc


@pytest.mark.asyncio
async def test_grant_credits_creates_paid_package_valid_for_one_year():
    svc = _grant_service()
    owner_id = uuid.uuid4()
    admin_id = uuid.uuid4()

    created_package = FiscalEmissionPackage(
        owner_id=owner_id,
        package_type=PACKAGE_TYPE_ADMIN_GRANT,
        quantity=500,
        remaining=500,
        payment_status="paid",
        grant_reason_code="courtesy",
        granted_by_id=admin_id,
    )
    svc.credits_repo.create_grant_package.return_value = created_package

    result = await svc.grant_credits(
        owner_id=owner_id,
        amount=500,
        reason_code="courtesy",
        granted_by_id=admin_id,
        idempotency_key="idem-12345678",
        note="nota interna",
    )

    assert result.created is True
    assert result.package is created_package

    kwargs = svc.credits_repo.create_grant_package.await_args.kwargs
    assert kwargs["owner_id"] == owner_id
    assert kwargs["quantity"] == 500
    assert kwargs["grant_reason_code"] == "courtesy"
    assert kwargs["grant_note"] == "nota interna"
    assert kwargs["granted_by_id"] == admin_id
    assert (kwargs["valid_until"] - kwargs["valid_from"]).days == 365


@pytest.mark.asyncio
async def test_grant_credits_honours_custom_validity():
    svc = _grant_service()
    svc.credits_repo.create_grant_package.return_value = FiscalEmissionPackage(
        owner_id=uuid.uuid4(), quantity=10, remaining=10, payment_status="paid"
    )

    await svc.grant_credits(
        owner_id=uuid.uuid4(),
        amount=10,
        reason_code="bonus",
        granted_by_id=uuid.uuid4(),
        idempotency_key="idem-12345678",
        valid_days=30,
    )

    kwargs = svc.credits_repo.create_grant_package.await_args.kwargs
    assert (kwargs["valid_until"] - kwargs["valid_from"]).days == 30


@pytest.mark.asyncio
async def test_grant_credits_adds_addon_with_package_scoped_ledger_key():
    svc = _grant_service()
    package = FiscalEmissionPackage(
        owner_id=uuid.uuid4(), quantity=500, remaining=500, payment_status="paid"
    )
    svc.credits_repo.create_grant_package.return_value = package

    await svc.grant_credits(
        owner_id=package.owner_id,
        amount=500,
        reason_code="courtesy",
        granted_by_id=uuid.uuid4(),
        idempotency_key="idem-12345678",
    )

    kwargs = svc.quota_service.add_addon_credits.await_args.kwargs
    assert kwargs["idempotency_key"] == f"admin_grant:{package.id}"
    assert kwargs["amount"] == 500
    assert kwargs["included_limit"] == 200  # limite real do plano, nao 0
    assert kwargs["commit"] is False
    svc.credits_repo.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_grant_credits_is_idempotent_on_replay():
    svc = _make_credits_service()
    existing = FiscalEmissionPackage(
        owner_id=uuid.uuid4(), quantity=500, remaining=500, payment_status="paid"
    )
    svc.credits_repo.get_package_by_idempotency_key.return_value = existing

    result = await svc.grant_credits(
        owner_id=existing.owner_id,
        amount=500,
        reason_code="courtesy",
        granted_by_id=uuid.uuid4(),
        idempotency_key="idem-12345678",
    )

    assert result.created is False
    assert result.package is existing
    svc.credits_repo.create_grant_package.assert_not_awaited()
    svc.quota_service.add_addon_credits.assert_not_awaited()


@pytest.mark.asyncio
async def test_grant_credits_notifies_user_with_friendly_label():
    svc = _grant_service()
    package = FiscalEmissionPackage(
        owner_id=uuid.uuid4(), quantity=500, remaining=500, payment_status="paid"
    )
    svc.credits_repo.create_grant_package.return_value = package

    await svc.grant_credits(
        owner_id=package.owner_id,
        amount=500,
        reason_code="compensation",
        granted_by_id=uuid.uuid4(),
        idempotency_key="idem-12345678",
        note="segredo interno",
    )

    kwargs = svc.notification_service.create_notification.await_args.kwargs
    assert "500" in kwargs["title"]
    assert GRANT_REASON_LABELS["compensation"] in kwargs["message"]
    assert kwargs["dedupe_key"] == f"admin_grant:{package.id}"
    # A nota interna nunca pode chegar ao usuario
    assert "segredo interno" not in kwargs["message"]


@pytest.mark.asyncio
async def test_grant_credits_audits_with_actor_and_note():
    svc = _grant_service()
    admin_id = uuid.uuid4()
    package = FiscalEmissionPackage(
        owner_id=uuid.uuid4(), quantity=500, remaining=500, payment_status="paid"
    )
    svc.credits_repo.create_grant_package.return_value = package

    await svc.grant_credits(
        owner_id=package.owner_id,
        amount=500,
        reason_code="courtesy",
        granted_by_id=admin_id,
        idempotency_key="idem-12345678",
        note="nota interna",
        ip_address="203.0.113.7",
        user_agent="Mozilla/5.0",
    )

    kwargs = svc.audit_service.record.await_args.kwargs
    assert kwargs["action"] == "admin_fiscal_credits_granted"
    assert kwargs["actor_user_id"] == admin_id
    assert kwargs["ip_address"] == "203.0.113.7"
    assert kwargs["metadata"]["amount"] == 500
    assert kwargs["metadata"]["note"] == "nota interna"
    assert kwargs["commit"] is False


from pydantic import ValidationError

from infra.web.routers.admin_fiscal import GrantCreditsRequest


def test_grant_request_rejects_unknown_reason_code():
    with pytest.raises(ValidationError):
        GrantCreditsRequest(
            owner_id=uuid.uuid4(),
            amount=100,
            reason_code="whatever",
            idempotency_key="idem-12345678",
        )


def test_grant_request_defaults_to_one_year():
    payload = GrantCreditsRequest(
        owner_id=uuid.uuid4(),
        amount=100,
        reason_code="courtesy",
        idempotency_key="idem-12345678",
    )
    assert payload.valid_days == 365
    assert payload.note is None


def test_grant_request_rejects_validity_out_of_range():
    for bad in (0, 1096):
        with pytest.raises(ValidationError):
            GrantCreditsRequest(
                owner_id=uuid.uuid4(),
                amount=100,
                reason_code="courtesy",
                valid_days=bad,
                idempotency_key="idem-12345678",
            )


def test_grant_request_rejects_amount_out_of_range():
    for bad in (0, 50_001):
        with pytest.raises(ValidationError):
            GrantCreditsRequest(
                owner_id=uuid.uuid4(),
                amount=bad,
                reason_code="courtesy",
                idempotency_key="idem-12345678",
            )


def test_grant_request_rejects_oversized_note():
    with pytest.raises(ValidationError):
        GrantCreditsRequest(
            owner_id=uuid.uuid4(),
            amount=100,
            reason_code="courtesy",
            note="x" * 501,
            idempotency_key="idem-12345678",
        )
