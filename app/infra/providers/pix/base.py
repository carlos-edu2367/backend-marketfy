from __future__ import annotations
from decimal import Decimal
from typing import Protocol
from domain.pix import PixItem, PixOrderResult


class PixPaymentProvider(Protocol):
    async def create_qr_payment(self, *, access_token: str, amount: Decimal, external_reference: str,
                                external_pos_id: str, description: str, items: list[PixItem],
                                expiration: str, idempotency_key: str) -> PixOrderResult: ...
    async def get_payment(self, *, access_token: str, order_id: str) -> PixOrderResult: ...
    async def cancel_payment(self, *, access_token: str, order_id: str,
                             idempotency_key: str) -> PixOrderResult: ...
