from __future__ import annotations
from application.services.pix.payment_service import PixLocationNotConfiguredError


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
