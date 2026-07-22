import os
import sys
import uuid
from decimal import Decimal

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.sales_service import SalesService
from domain.sales import Payment, PaymentMethod, Sale


def test_sale_response_preserves_dynamic_pix_modality_and_confirmation():
    sale = Sale(
        market_id=uuid.uuid4(),
        box_id=uuid.uuid4(),
        operator_id=uuid.uuid4(),
        total_amount=Decimal("16.90"),
    )
    sale.payments.append(Payment(
        sale_id=sale.id,
        method=PaymentMethod.PIX,
        amount=Decimal("16.90"),
        modality="qr_dynamic",
    ))

    response = SalesService._map_sale_to_response(object(), sale)

    assert response.modality == "qr_dynamic"
    assert response.pix_status == "approved"
