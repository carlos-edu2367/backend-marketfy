"""Cliente HTTP para a Capture API do PostHog (eventos de produto/funil, D9).

Nunca levanta exceção para o chamador — falha de rede ou PostHog fora do ar
não pode derrubar uma operação de negócio real. Timeout curto (2s) para
limitar o pior caso de latência.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from infra.config.logger import get_logger
from infra.config.settings import get_settings

logger = get_logger("analytics")


class PostHogClient:
    def __init__(self):
        settings = get_settings()
        self._api_key = settings.POSTHOG_API_KEY or ""
        self._host = settings.POSTHOG_HOST
        self._enabled = settings.ANALYTICS_ENABLED and bool(self._api_key)
        self._timeout = 2.0

    async def track_event(self, distinct_id: str, event: str, properties: Optional[dict] = None) -> None:
        if not self._enabled:
            return
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._host.rstrip('/')}/capture/",
                    json={
                        "api_key": self._api_key,
                        "event": event,
                        "distinct_id": distinct_id,
                        "properties": properties or {},
                    },
                )
                if response.status_code >= 400:
                    logger.warning(
                        "posthog_capture_failed",
                        extra={"extra_data": {"event": event, "status": response.status_code}},
                    )
        except Exception:
            logger.exception("posthog_capture_error", extra={"extra_data": {"event": event}})


_default_client: Optional[PostHogClient] = None


def _get_default_client() -> PostHogClient:
    global _default_client
    if _default_client is None:
        _default_client = PostHogClient()
    return _default_client


async def track_event(distinct_id: str, event: str, properties: Optional[dict] = None) -> None:
    """Conveniência para call sites sem injeção de serviço. Os 5 pontos de
    instrumentação desta rodada usam PostHogClient via construtor — esta
    função fica disponível para uso futuro fora desse padrão."""
    await _get_default_client().track_event(distinct_id, event, properties)
