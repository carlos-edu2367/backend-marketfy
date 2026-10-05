"""Liga sessões de funil a usuários e marca os marcos de conversão.

Usa sessão de banco própria (não participa da transação do chamador) e nunca
levanta exceção: atribuição de funil jamais pode derrubar cadastro ou billing.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from domain.funnels import MILESTONE_FIELDS
from infra.config.logger import get_logger
from infra.repositories.funnel_repo import FunnelRepository

logger = get_logger("funnel_attribution")


class FunnelAttributionService:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    async def link_registration(self, fsid: uuid.UUID, user_id: uuid.UUID) -> bool:
        return await self._link(fsid, user_id, milestone="registered")

    async def claim(self, fsid: uuid.UUID, user_id: uuid.UUID) -> bool:
        return await self._link(fsid, user_id, milestone=None)

    async def _link(self, fsid, user_id, *, milestone: Optional[str]) -> bool:
        try:
            async with self._session_factory() as db:
                repo = FunnelRepository(db)
                session = await repo.get_session(fsid)
                if session is None or session.user_id is not None:
                    logger.info("funnel_link_skipped", extra={"extra_data": {"fsid": str(fsid)}})
                    return False
                session.user_id = user_id
                if milestone:
                    setattr(session, MILESTONE_FIELDS[milestone], datetime.now(timezone.utc))
                    await repo.add_event(session, milestone)
                await db.commit()
                return True
        except Exception:
            logger.exception("funnel_link_failed", extra={"extra_data": {"fsid": str(fsid)}})
            return False

    async def mark(self, user_id: uuid.UUID, event: str, amount: Optional[Decimal] = None) -> bool:
        field = MILESTONE_FIELDS.get(event)
        if field is None:
            return False
        try:
            async with self._session_factory() as db:
                repo = FunnelRepository(db)
                session = await repo.latest_session_for_user(user_id)
                if session is None or getattr(session, field) is not None:
                    return False
                setattr(session, field, datetime.now(timezone.utc))
                if event == "invoice_paid" and amount is not None:
                    session.first_payment_amount = amount
                await repo.add_event(session, event)
                await db.commit()
                return True
        except Exception:
            logger.exception("funnel_mark_failed", extra={"extra_data": {"event": event}})
            return False
