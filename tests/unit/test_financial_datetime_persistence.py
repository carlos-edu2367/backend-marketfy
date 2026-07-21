import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from domain.finance import FinancialTransaction, TransactionType
from infra.repositories.sqlalchemy_repos import SQLAlchemyFinancialTransactionRepository


@pytest.mark.asyncio
async def test_financial_repository_persists_aware_paid_at_as_naive_utc():
    session = AsyncMock()
    session.get.return_value = None
    session.add = MagicMock()
    paid_at = datetime(2026, 7, 21, 12, 54, 3, tzinfo=timezone.utc)
    transaction = FinancialTransaction(
        market_id=uuid.uuid4(),
        description="Venda realizada",
        amount=Decimal("10.00"),
        type=TransactionType.CREDIT,
        due_date=paid_at,
        paid_at=paid_at,
    )

    await SQLAlchemyFinancialTransactionRepository(session).save(transaction, commit=False)

    model = session.add.call_args.args[0]
    assert model.due_date == paid_at.replace(tzinfo=None)
    assert model.paid_at == paid_at.replace(tzinfo=None)
