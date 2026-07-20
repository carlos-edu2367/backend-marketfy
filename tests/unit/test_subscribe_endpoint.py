from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.dtos import SubscribeRequestDTO


def test_subscribe_dto_requires_mode_and_period():
    import uuid
    dto = SubscribeRequestDTO(
        plan_id=uuid.uuid4(),
        subscription_type="monthly",
        billing_mode="invoice",
    )
    assert dto.billing_mode == "invoice"
    assert dto.subscription_type == "monthly"
    assert dto.document is None
