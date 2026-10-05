"""Cliente de analytics que, além do PostHog, marca marcos de funil de venda."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from infra.config.logger import get_logger
from infra.config.settings import get_settings
from infra.observability.analytics import PostHogClient

logger = get_logger("funnel_analytics")

_TRACKED_MILESTONES = frozenset({"trial_activated", "subscription_created", "invoice_paid"})


class FunnelTrackingAnalytics:
    def __init__(self, inner, attribution):
        self._inner = inner
        self._attribution = attribution

    async def track_event(self, distinct_id: str, event: str, properties: Optional[dict] = None) -> None:
        await self._inner.track_event(distinct_id, event, properties)
        if event not in _TRACKED_MILESTONES:
            return
        try:
            amount = None
            if event == "invoice_paid" and properties and properties.get("amount") is not None:
                amount = Decimal(str(properties["amount"]))
            await self._attribution.mark(uuid.UUID(str(distinct_id)), event, amount)
        except Exception:
            logger.exception("funnel_tracking_failed", extra={"extra_data": {"event": event}})


def build_analytics():
    """Default dos services de billing: PostHog + marcação de funil (se habilitada)."""
    if not get_settings().FUNNEL_ATTRIBUTION_ENABLED:
        return PostHogClient()
    from application.services.funnel_attribution_service import FunnelAttributionService
    from infra.database.setup import async_session_factory

    return FunnelTrackingAnalytics(PostHogClient(), FunnelAttributionService(async_session_factory))
