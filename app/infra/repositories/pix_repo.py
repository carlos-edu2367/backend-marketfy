"""Repositórios para a integração Pix (Mercado Pago)."""

from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.models import (
    MercadoPagoConnectionModel, MercadoPagoOAuthStateModel,
)


class MercadoPagoConnectionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_market(self, market_id: uuid.UUID) -> Optional[MercadoPagoConnectionModel]:
        res = await self.db.execute(
            select(MercadoPagoConnectionModel).where(MercadoPagoConnectionModel.market_id == market_id)
        )
        return res.scalar_one_or_none()

    async def save(self, model: MercadoPagoConnectionModel, commit: bool = True) -> MercadoPagoConnectionModel:
        self.db.add(model)
        if commit:
            await self.db.commit()
            await self.db.refresh(model)
        else:
            await self.db.flush()
        return model


class MercadoPagoOAuthStateRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, *, state, market_id, initiated_by_user_id,
                     code_verifier_ciphertext, redirect_uri, expires_at) -> MercadoPagoOAuthStateModel:
        model = MercadoPagoOAuthStateModel(
            state=state, market_id=market_id, initiated_by_user_id=initiated_by_user_id,
            code_verifier_ciphertext=code_verifier_ciphertext, redirect_uri=redirect_uri,
            expires_at=expires_at,
        )
        self.db.add(model)
        await self.db.commit()
        await self.db.refresh(model)
        return model

    async def consume(self, state: str, now: datetime) -> Optional[MercadoPagoOAuthStateModel]:
        """Consumo atômico: marca used_at só se ainda não usado e não expirado."""
        stmt = (
            update(MercadoPagoOAuthStateModel)
            .where(
                MercadoPagoOAuthStateModel.state == state,
                MercadoPagoOAuthStateModel.used_at.is_(None),
                MercadoPagoOAuthStateModel.expires_at > now,
            )
            .values(used_at=now)
            .returning(MercadoPagoOAuthStateModel.id)
        )
        res = await self.db.execute(stmt)
        row = res.first()
        if row is None:
            # Nenhuma linha casou (state ausente, já usado ou expirado). Nada
            # foi alterado — não há necessidade (e seria prejudicial) de
            # fazer rollback(), pois isso expiraria outros objetos já
            # carregados nesta mesma sessão.
            await self.db.commit()
            return None
        await self.db.commit()
        loaded = await self.db.execute(
            select(MercadoPagoOAuthStateModel).where(MercadoPagoOAuthStateModel.id == row[0])
        )
        return loaded.scalar_one()
