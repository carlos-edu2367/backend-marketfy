# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from decimal import Decimal
from domain.pix import (
    PixAttemptStatus, PixPaymentModality, PixItem, PixOrderResult, map_order_status,
)


def test_map_known_statuses():
    assert map_order_status("created", "created") is PixAttemptStatus.PENDING
    assert map_order_status("processed", "accredited") is PixAttemptStatus.APPROVED
    assert map_order_status("canceled", "canceled") is PixAttemptStatus.CANCELED
    assert map_order_status("expired", "expired") is PixAttemptStatus.EXPIRED


def test_map_unknown_status_is_none_failsafe():
    # status desconhecido NAO avanca o estado: retorna None
    assert map_order_status("weird_new_status", None) is None


def test_pix_item_and_result_are_frozen():
    item = PixItem(title="X", unit_price=Decimal("10.00"), quantity=2)
    assert item.unit_measure == "unit"
    res = PixOrderResult(order_id="ORD1", external_status="created", status_detail="created",
                         mapped_status=PixAttemptStatus.PENDING, total_amount=Decimal("20.00"),
                         currency="BRL", qr_data="000201...", receiver_account_id="42")
    assert res.order_id == "ORD1"
