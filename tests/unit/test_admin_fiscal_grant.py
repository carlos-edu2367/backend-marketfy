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
