"""Repositório para vínculos usuário-loja (Fase 3 / PR 10).

Mantém o contrato mínimo necessário para a política central de autorização
em `infra/security/market_access.py`. Outras operações (CRUD completo,
listagem por loja, etc.) podem ser adicionadas conforme novas features.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.identity import UserRole
from infra.database.models import MarketMemberModel


@dataclass
class MarketMember:
    """Entidade leve para uso na camada de autorização."""

    id: uuid.UUID
    market_id: uuid.UUID
    user_id: uuid.UUID
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


def _to_entity(model: MarketMemberModel) -> MarketMember:
    return MarketMember(
        id=model.id,
        market_id=model.market_id,
        user_id=model.user_id,
        role=UserRole(model.role),
        is_active=bool(model.is_active),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SQLAlchemyMarketMemberRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_active_member(
        self, market_id: uuid.UUID, user_id: uuid.UUID
    ) -> Optional[MarketMember]:
        stmt = select(MarketMemberModel).where(
            MarketMemberModel.market_id == market_id,
            MarketMemberModel.user_id == user_id,
            MarketMemberModel.is_active == True,  # noqa: E712
        )
        result = await self.session.execute(stmt)
        model = result.scalars().first()
        return _to_entity(model) if model else None

    async def list_for_market(self, market_id: uuid.UUID) -> List[MarketMember]:
        stmt = select(MarketMemberModel).where(MarketMemberModel.market_id == market_id)
        result = await self.session.execute(stmt)
        return [_to_entity(m) for m in result.scalars().all()]

    async def add_member(
        self,
        market_id: uuid.UUID,
        user_id: uuid.UUID,
        role: UserRole,
        is_active: bool = True,
        commit: bool = True,
    ) -> MarketMember:
        # Garante idempotência: se já existir, atualiza role/is_active.
        existing_stmt = select(MarketMemberModel).where(
            MarketMemberModel.market_id == market_id,
            MarketMemberModel.user_id == user_id,
        )
        existing = (await self.session.execute(existing_stmt)).scalars().first()

        if existing:
            existing.role = role.value if hasattr(role, "value") else str(role)
            existing.is_active = is_active
            model = existing
        else:
            model = MarketMemberModel(
                id=uuid.uuid4(),
                market_id=market_id,
                user_id=user_id,
                role=role.value if hasattr(role, "value") else str(role),
                is_active=is_active,
            )
            self.session.add(model)

        if commit:
            await self.session.commit()
            await self.session.refresh(model)
        return _to_entity(model)

    async def deactivate(
        self, market_id: uuid.UUID, user_id: uuid.UUID, commit: bool = True
    ) -> bool:
        stmt = select(MarketMemberModel).where(
            MarketMemberModel.market_id == market_id,
            MarketMemberModel.user_id == user_id,
        )
        model = (await self.session.execute(stmt)).scalars().first()
        if not model:
            return False
        model.is_active = False
        if commit:
            await self.session.commit()
        return True
