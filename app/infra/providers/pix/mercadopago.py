from __future__ import annotations
from decimal import Decimal
from domain.pix import PixItem, PixOrderResult
from infra.clients.mercadopago_client import MercadoPagoClient


class MercadoPagoPixProvider:
    def __init__(self, client: MercadoPagoClient | None = None):
        self.client = client or MercadoPagoClient()

    async def create_qr_payment(self, *, access_token, amount, external_reference, external_pos_id,
                                description, items, expiration, idempotency_key) -> PixOrderResult:
        return await self.client.create_qr_order(
            access_token=access_token, amount=amount, external_reference=external_reference,
            external_pos_id=external_pos_id, description=description, items=items,
            expiration=expiration, idempotency_key=idempotency_key)

    async def get_payment(self, *, access_token, order_id) -> PixOrderResult:
        return await self.client.get_order(access_token=access_token, order_id=order_id)

    async def cancel_payment(self, *, access_token, order_id, idempotency_key) -> PixOrderResult:
        return await self.client.cancel_order(access_token=access_token, order_id=order_id,
                                              idempotency_key=idempotency_key)
