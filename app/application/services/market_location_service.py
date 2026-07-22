"""Use cases for configuring a market's physical location."""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from domain.market_location import MarketLocation, MarketLocationValidationError
from infra.observability.metrics import metrics_registry


class MarketLocationService:
    def __init__(self, repository):
        self.repository = repository

    async def get(self, market_id: uuid.UUID) -> dict:
        model = await self.repository.get_by_market(market_id)
        if model is None:
            return {"status": "not_configured", "market_id": str(market_id)}
        registration = await self.repository.get_store_registration(market_id)
        status = "ready"
        if registration is not None and (
            registration.location_version_synced != model.location_version
            or registration.sync_status != "synced"
        ):
            status = "sync_required"
        return self._public_model(model, status)

    async def save(self, market_id: uuid.UUID, payload: dict) -> dict:
        current = await self.repository.get_by_market(market_id)
        version = (current.location_version + 1) if current is not None else 1
        latitude = self._decimal_field(payload.get("latitude"), "latitude")
        longitude = self._decimal_field(payload.get("longitude"), "longitude")
        location = MarketLocation(
            market_id=market_id,
            postal_code=payload.get("postal_code", ""),
            street_name=payload.get("street_name", ""),
            street_number=payload.get("street_number", ""),
            district=payload.get("district"),
            complement=payload.get("complement"),
            city_name=payload.get("city_name", ""),
            state_code=payload.get("state_code", ""),
            state_name=payload.get("state_name", ""),
            country_code=payload.get("country_code", "BR"),
            latitude=latitude,
            longitude=longitude,
            source=payload.get("source", "manual"),
            version=version,
        )
        location.validate()
        model = await self.repository.save_location(location)
        metrics_registry.record_pix_location_event("location_saved")
        return self._public_model(model, "ready")

    @staticmethod
    def _decimal_field(value, field: str) -> Decimal:
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise MarketLocationValidationError(f"{field} inválida") from exc

    @staticmethod
    def _public_model(model, status: str) -> dict:
        version = getattr(model, "location_version", getattr(model, "version", 1))
        return {
            "status": status,
            "market_id": str(model.market_id),
            "location_version": version,
            "postal_code": model.postal_code,
            "street_name": model.street_name,
            "street_number": model.street_number,
            "district": model.district,
            "complement": model.complement,
            "city_name": model.city_name,
            "state_code": model.state_code,
            "state_name": model.state_name,
            "country_code": model.country_code,
            "latitude": float(model.latitude),
            "longitude": float(model.longitude),
        }
