"""Repositórios para a integração Pix (Mercado Pago)."""

from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update, or_
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.models import (
    MercadoPagoConnectionModel, MercadoPagoOAuthStateModel,
    PixPaymentAttemptModel, PixStatusQueryModel, MercadoPagoPosRegistrationModel,
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

    async def list_expiring(self, before: datetime, limit: int = 100):
        """Conexões ativas cujo access token vence antes de `before` (ou nunca teve expiração registrada)."""
        res = await self.db.execute(
            select(MercadoPagoConnectionModel).where(
                MercadoPagoConnectionModel.status.in_(("connected", "refresh_required")),
                or_(
                    MercadoPagoConnectionModel.access_token_expires_at < before,
                    MercadoPagoConnectionModel.access_token_expires_at.is_(None),
                ),
            ).limit(limit)
        )
        return res.scalars().all()


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


_ACTIVE = ("pending", "in_analysis", "confirmation_pending")


class PixPaymentAttemptRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active_by_sale(self, sale_id):
        res = await self.db.execute(
            select(PixPaymentAttemptModel).where(
                PixPaymentAttemptModel.sale_id == sale_id,
                PixPaymentAttemptModel.status.in_(_ACTIVE),
            )
        )
        return res.scalar_one_or_none()

    async def get_by_id_for_update(self, attempt_id, market_id):
        res = await self.db.execute(
            select(PixPaymentAttemptModel).where(
                PixPaymentAttemptModel.id == attempt_id,
                PixPaymentAttemptModel.market_id == market_id,
            ).with_for_update()
        )
        return res.scalar_one_or_none()

    async def get_by_order_id(self, order_id):
        res = await self.db.execute(
            select(PixPaymentAttemptModel).where(PixPaymentAttemptModel.order_id == order_id)
        )
        return res.scalar_one_or_none()

    async def save(self, model, commit: bool = True):
        self.db.add(model)
        if commit:
            await self.db.commit()
            await self.db.refresh(model)
        else:
            await self.db.flush()
        return model

    async def list_active_stale(self, older_than: datetime, limit: int = 50):
        """Tentativas ativas cuja última consulta de status está velha (ou nunca ocorreu)."""
        res = await self.db.execute(
            select(PixPaymentAttemptModel).where(
                PixPaymentAttemptModel.status.in_(_ACTIVE),
                or_(
                    PixPaymentAttemptModel.last_status_query_at < older_than,
                    PixPaymentAttemptModel.last_status_query_at.is_(None),
                ),
            ).order_by(PixPaymentAttemptModel.created_at.asc()).limit(limit)
        )
        return res.scalars().all()

    async def list_active_expired(self, before: datetime, limit: int = 50):
        """Tentativas ativas cujo QR já venceu (expires_at < before)."""
        res = await self.db.execute(
            select(PixPaymentAttemptModel).where(
                PixPaymentAttemptModel.status.in_(_ACTIVE),
                PixPaymentAttemptModel.expires_at.isnot(None),
                PixPaymentAttemptModel.expires_at < before,
            ).order_by(PixPaymentAttemptModel.expires_at.asc()).limit(limit)
        )
        return res.scalars().all()

    async def record_query(self, *, attempt_id, market_id, source, received_status=None,
                           latency_ms=None, http_status=None, error_code=None):
        q = PixStatusQueryModel(attempt_id=attempt_id, market_id=market_id, source=source,
            received_status=received_status, latency_ms=latency_ms, http_status=http_status,
            error_code=error_code)
        self.db.add(q)
        await self.db.flush()
        return q


class MercadoPagoPosRegistrationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_market_and_terminal(self, market_id, terminal_id):
        res = await self.db.execute(
            select(MercadoPagoPosRegistrationModel).where(
                MercadoPagoPosRegistrationModel.market_id == market_id,
                MercadoPagoPosRegistrationModel.terminal_id == terminal_id,
            )
        )
        return res.scalar_one_or_none()

    async def get_store_id_for_market(self, market_id):
        res = await self.db.execute(
            select(MercadoPagoPosRegistrationModel).where(
                MercadoPagoPosRegistrationModel.market_id == market_id,
            )
        )
        existing = res.scalars().first()
        return existing.mp_store_id if existing else None

    async def save(self, model, commit: bool = True):
        self.db.add(model)
        if commit:
            await self.db.commit()
            await self.db.refresh(model)
        else:
            await self.db.flush()
        return model
