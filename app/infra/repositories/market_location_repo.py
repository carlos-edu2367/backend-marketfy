"""Persistence adapters for structured market location and Store sync state."""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.market_location import MarketLocation
from infra.database.models import (
    MarketLocationModel,
    MercadoPagoStoreRegistrationModel,
)


class MarketLocationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_market(self, market_id: uuid.UUID) -> Optional[MarketLocationModel]:
        result = await self.db.execute(
            select(MarketLocationModel).where(MarketLocationModel.market_id == market_id)
        )
        return result.scalar_one_or_none()

    async def get_store_registration(self, market_id: uuid.UUID):
        result = await self.db.execute(
            select(MercadoPagoStoreRegistrationModel).where(
                MercadoPagoStoreRegistrationModel.market_id == market_id
            )
        )
        return result.scalar_one_or_none()

    async def save_location(self, location: MarketLocation) -> MarketLocationModel:
        model = await self.get_by_market(location.market_id)
        if model is None:
            model = MarketLocationModel(id=uuid.uuid4(), market_id=location.market_id)
            self.db.add(model)
        for field in (
            "postal_code", "street_name", "street_number", "district", "complement",
            "city_name", "state_code", "state_name", "country_code", "latitude",
            "longitude", "source", "location_version",
        ):
            setattr(model, field, getattr(location, field))
        await self.db.commit()
        await self.db.refresh(model)
        return model


class MercadoPagoStoreRegistrationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_market(self, market_id: uuid.UUID):
        result = await self.db.execute(
            select(MercadoPagoStoreRegistrationModel).where(
                MercadoPagoStoreRegistrationModel.market_id == market_id
            )
        )
        return result.scalar_one_or_none()

    async def save(self, model, commit: bool = True):
        self.db.add(model)
        if commit:
            await self.db.commit()
            await self.db.refresh(model)
        else:
            await self.db.flush()
        return model
