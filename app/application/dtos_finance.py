"""DTOs específicos do módulo finance (Fase 3 / PR 11).

Movidos para um arquivo próprio para reduzir a explosão de `application/dtos.py`
e organizar contratos por domínio. Imports antigos seguem funcionando: estes
DTOs continuam reexportados a partir de `application/dtos.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import List

from pydantic import BaseModel


class FinancialTransactionResponseDTO(BaseModel):
    id: uuid.UUID
    type: str
    amount: Decimal
    description: str
    category: str
    created_at: datetime

    class Config:
        from_attributes = True


class FinanceDashboardDTO(BaseModel):
    balance: Decimal
    total_revenue: Decimal
    total_expense: Decimal
    accounts_receivable: Decimal
    recent_transactions: List[FinancialTransactionResponseDTO]
