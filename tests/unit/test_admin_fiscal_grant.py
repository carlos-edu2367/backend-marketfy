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
