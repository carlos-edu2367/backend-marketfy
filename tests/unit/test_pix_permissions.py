# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.security.market_access import (
    MarketPermission, role_has_permission,
)
from domain.identity import UserRole


def test_payments_permissions_exist():
    assert MarketPermission.PAYMENTS_READ.value == "payments.read"
    assert MarketPermission.PAYMENTS_WRITE.value == "payments.write"


def test_manager_can_write_payments():
    assert role_has_permission(UserRole.MANAGER, MarketPermission.PAYMENTS_WRITE)


def test_cashier_can_read_but_not_write_payments():
    assert role_has_permission(UserRole.CASHIER, MarketPermission.PAYMENTS_READ)
    assert not role_has_permission(UserRole.CASHIER, MarketPermission.PAYMENTS_WRITE)
