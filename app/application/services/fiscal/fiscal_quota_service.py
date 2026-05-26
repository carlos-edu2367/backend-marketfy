"""
FiscalQuotaService — controla cotas mensais de emissão fiscal (owner-scoped).

Fluxo de cota:
1. check_and_reserve() — antes de enfileirar; lança FiscalQuotaExceededError se sem cota
2. consume()           — após SEFAZ autorizar; registra uso e decrementa addon FIFO se aplicável
3. release()           — se falha antes/durante provider; devolve reserva

Regras:
- Counter é owner-scoped: todos os mercados compartilham o mesmo pool.
- Consumo de included primeiro; addon (FIFO por valid_until) depois.
- Reserva atômica via SELECT FOR UPDATE.
- Ledger preserva market_id para rastreio de qual mercado gerou cada emissão.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from domain.fiscal import (
    FiscalQuotaExceededError,
    FiscalUsageLedger,
    QuotaReserveResult,
    QuotaStatus,
    UsageLedgerEventType,
)
from infra.config.logger import get_logger
from infra.repositories.fiscal_repo import SQLAlchemyFiscalUsageRepository

logger = get_logger("fiscal_quota_service")


def _current_period() -> str:
    now = datetime.utcnow()
    return f"{now.year}{now.month:02d}"


class FiscalQuotaService:
    def __init__(self, usage_repo: SQLAlchemyFiscalUsageRepository):
        self.repo = usage_repo

    async def check_and_reserve(
        self,
        owner_id: uuid.UUID,
        market_id: uuid.UUID,
        period: str,
        included_limit: int = 0,
    ) -> QuotaReserveResult:
        """
        Verifica disponibilidade e reserva 1 unidade de cota.

        included_limit: passado pelo caller (de PlanModel.fiscal_monthly_limit) para
        criar o counter se ainda não existir para este período.

        Raises:
            FiscalQuotaExceededError: sem cota included nem addon disponível.
        """
        counter = await self.repo.get_or_create_counter(
            owner_id=owner_id,
            period=period,
            included_limit=included_limit,
        )

        available_included = max(0, counter.included_limit - counter.used_count - counter.reserved_count)
        available_addon = counter.addon_limit
        total_available = available_included + available_addon

        if total_available == 0:
            raise FiscalQuotaExceededError(
                used=counter.used_count,
                included_limit=counter.included_limit,
                addon_limit=counter.addon_limit,
            )

        reserved = await self.repo.increment_reserved(owner_id, period)
        if not reserved:
            raise FiscalQuotaExceededError(
                used=counter.used_count,
                included_limit=counter.included_limit,
                addon_limit=counter.addon_limit,
            )

        consuming_addon = counter.used_count >= counter.included_limit

        logger.info(
            "fiscal_quota_reserved",
            extra={"extra_data": {
                "owner_id": str(owner_id),
                "market_id": str(market_id),
                "period": period,
                "consuming_addon": consuming_addon,
                "available_after": total_available - 1,
            }},
        )
        return QuotaReserveResult(consuming_addon=consuming_addon)

    async def consume(
        self,
        owner_id: uuid.UUID,
        market_id: uuid.UUID,
        period: str,
        consuming_addon: bool,
        sale_id: Optional[uuid.UUID] = None,
        fiscal_document_id: Optional[uuid.UUID] = None,
    ) -> None:
        """Confirma consumo após emissão bem-sucedida."""
        await self.repo.increment_used(owner_id, period)
        await self.repo.decrement_reserved(owner_id, period)

        if consuming_addon:
            oldest_package = await self.repo.get_oldest_active_package(owner_id)
            if oldest_package:
                await self.repo.decrement_package_remaining(oldest_package.id)
                await self.repo.decrement_addon_limit(owner_id, period, amount=1)

        idempotency_key = (
            f"consume:{owner_id}:{period}:{fiscal_document_id}"
            if fiscal_document_id
            else None
        )
        entry = FiscalUsageLedger(
            owner_id=owner_id,
            period_yyyymm=period,
            event_type=UsageLedgerEventType.CONSUMED,
            market_id=market_id,
            sale_id=sale_id,
            fiscal_document_id=fiscal_document_id,
            quantity=1,
            idempotency_key=idempotency_key,
        )
        try:
            await self.repo.append_ledger(entry)
        except Exception:
            pass  # ledger é best-effort

    async def release(
        self,
        owner_id: uuid.UUID,
        period: str,
        market_id: Optional[uuid.UUID] = None,
        reason: str = "local_failure",
    ) -> None:
        """Libera reserva quando a emissão falha antes ou durante o provider."""
        await self.repo.decrement_reserved(owner_id, period)

        entry = FiscalUsageLedger(
            owner_id=owner_id,
            period_yyyymm=period,
            event_type=UsageLedgerEventType.RELEASED,
            market_id=market_id,
            quantity=1,
            reason=reason,
        )
        try:
            await self.repo.append_ledger(entry)
        except Exception:
            pass

    async def add_addon_credits(
        self,
        owner_id: uuid.UUID,
        period: str,
        amount: int,
        idempotency_key: str,
    ) -> None:
        """Ativa créditos addon após pagamento confirmado. Idempotente."""
        existing = await self.repo.get_ledger_entry_by_idempotency(idempotency_key)
        if existing:
            return

        await self.repo.increment_addon_limit(owner_id, period, amount)
        entry = FiscalUsageLedger(
            owner_id=owner_id,
            period_yyyymm=period,
            event_type=UsageLedgerEventType.ADDON_PURCHASED,
            quantity=amount,
            idempotency_key=idempotency_key,
        )
        try:
            await self.repo.append_ledger(entry)
        except Exception:
            pass

    async def get_quota_status(self, owner_id: uuid.UUID, period: str) -> QuotaStatus:
        counter = await self.repo.get_counter(owner_id, period)
        if not counter:
            return QuotaStatus(
                period=period,
                included_limit=0,
                addon_limit=0,
                used_count=0,
                reserved_count=0,
                remaining=0,
                percentage_used=0.0,
            )
        remaining = counter.available_quota()
        total = counter.included_limit + counter.addon_limit
        pct = (counter.used_count / max(1, total)) * 100 if total > 0 else 0.0
        return QuotaStatus(
            period=period,
            included_limit=counter.included_limit,
            addon_limit=counter.addon_limit,
            used_count=counter.used_count,
            reserved_count=counter.reserved_count,
            remaining=remaining,
            percentage_used=round(pct, 1),
        )

    async def get_usage_percentage(
        self, owner_id: uuid.UUID, period: Optional[str] = None
    ) -> float:
        if period is None:
            period = _current_period()
        counter = await self.repo.get_counter(owner_id, period)
        if not counter:
            return 0.0
        return counter.usage_percentage()
