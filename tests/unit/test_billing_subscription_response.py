from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.dtos import BillingSubscriptionResponseDTO


def test_response_dto_has_new_fields():
    dto = BillingSubscriptionResponseDTO(
        status="active",
        billing_mode="invoice",
        locked=False,
        invoice_pending=True,
        pending_invoice={"invoice_id": "x", "amount": "50.00"},
    )
    assert dto.billing_mode == "invoice"
    assert dto.locked is False
    assert dto.invoice_pending is True
    assert dto.pending_invoice["amount"] == "50.00"
