from __future__ import annotations
from application.services.pix.payment_service import PixLocationNotConfiguredError
from domain.market_location import MarketLocation


class UnconfiguredPosLocationProvider:
    """Placeholder até a decisão de produto sobre a origem do location (rua/cidade/
    estado/latitude/longitude) ser tomada. Nunca geocodifica nem inventa coordenadas —
    levanta um erro claro para que o tenant configure explicitamente antes de habilitar
    o QR dinâmico."""

    async def get_location(self, market_id) -> dict:
        raise PixLocationNotConfiguredError(
            "Localização da loja não configurada para registro Mercado Pago. "
            "Configure endereço estruturado (rua/cidade/estado/latitude/longitude) antes de habilitar o QR dinâmico."
        )


class DatabasePosLocationProvider:
    """Loads a market location that was explicitly validated and persisted."""

    def __init__(self, repository):
        self.repository = repository

    async def get_location(self, market_id) -> dict:
        model = await self.repository.get_by_market(market_id)
        if model is None:
            raise PixLocationNotConfiguredError(
                "Localização da loja não configurada para registro Mercado Pago."
            )
        location = MarketLocation(
            market_id=model.market_id,
            postal_code=model.postal_code,
            street_name=model.street_name,
            street_number=model.street_number,
            district=model.district,
            complement=model.complement,
            city_name=model.city_name,
            state_code=model.state_code,
            state_name=model.state_name,
            country_code=model.country_code,
            latitude=model.latitude,
            longitude=model.longitude,
            source=model.source,
            version=model.location_version,
        )
        return location.to_mp_location()

    async def get_location_version(self, market_id) -> int:
        model = await self.repository.get_by_market(market_id)
        if model is None:
            raise PixLocationNotConfiguredError(
                "Localização da loja não configurada para registro Mercado Pago."
            )
        return model.location_version
