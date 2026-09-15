from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.database.models import BillingSubscriptionModel


def test_billing_subscription_has_cancel_and_provisional_columns():
    columns = BillingSubscriptionModel.__table__.columns
    assert columns["cancel_at_period_end"].type.python_type is bool
    assert columns["cancel_at_period_end"].nullable is False
    assert columns["canceled_at"].nullable is True
    assert columns["provisional"].type.python_type is bool
    assert columns["provisional"].nullable is False
