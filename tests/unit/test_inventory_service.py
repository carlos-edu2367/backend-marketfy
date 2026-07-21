import uuid
from decimal import Decimal
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from application.services.inventory_service import InventoryService
from domain.inventory import Product


@pytest.mark.asyncio
async def test_delete_product_soft_deletes_only_product_from_requested_market():
    market_id = uuid.uuid4()
    product = Product(
        market_id=market_id,
        name="Duplicado",
        code="DUP-1",
        barcode="7891234567890",
        price=Decimal("10.00"),
    )
    product_repo = AsyncMock()
    product_repo.get_by_id.return_value = product
    product_repo.save.return_value = product
    service = InventoryService(product_repo, AsyncMock())

    result = await service.delete_product(market_id, product.id)

    assert result == {"message": "Produto removido com sucesso."}
    assert product.active is False
    assert product.deleted_at is not None
    product_repo.save.assert_awaited_once_with(product)
