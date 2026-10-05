"""Fluxo público do visitante: início/retomada de sessão e eventos de navegação."""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone
from typing import Optional

from domain.funnels import PUBLIC_EVENT_TYPES, FunnelError, FunnelNotFound, VariantWeight, pick_variant
from infra.repositories.funnel_repo import FunnelRepository

UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")


class FunnelGone(FunnelError):
    def __init__(self):
        super().__init__("funnel.gone", "Este funil não está mais disponível.")


def _clip(value: Optional[str], size: int) -> Optional[str]:
    return value[:size] if value else None


class FunnelPublicService:
    def __init__(self, repo: FunnelRepository, rng: Optional[random.Random] = None):
        self._repo = repo
        self._rng = rng

    async def start_session(self, slug: str, *, fsid: Optional[uuid.UUID], utm: dict, referrer: Optional[str]) -> dict:
        funnel = await self._repo.get_funnel_by_slug(slug)
        if funnel is None or funnel.status != "published":
            raise FunnelNotFound()
        variants = await self._repo.list_variants(funnel.id)
        by_id = {v.id: v for v in variants}

        session = await self._repo.get_session(fsid) if fsid else None
        if session is not None and (session.funnel_id != funnel.id or session.variant_id not in by_id):
            session = None
        if session is None:
            variant_id = pick_variant([VariantWeight(v.id, v.weight, v.is_active) for v in variants], self._rng)
            if variant_id is None:
                raise FunnelNotFound()
            session = await self._repo.create_session(
                funnel_id=funnel.id, variant_id=variant_id, referrer=_clip(referrer, 500),
                **{k: _clip(utm.get(k), 200) for k in UTM_KEYS},
            )

        steps = await self._repo.list_steps(session.variant_id)
        positions = [s.position for s in steps]
        resume = session.last_step_position if session.last_step_position in positions else (positions[0] if positions else 0)
        return {
            "fsid": session.id,
            "funnel": {"name": funnel.name, "plan_id": funnel.plan_id},
            "variant": {"id": session.variant_id},
            "tracking_html": funnel.tracking_html or "",
            "steps": [{"position": s.position, "name": s.name, "html": s.html} for s in steps],
            "resume_position": resume,
        }

    async def record_event(self, fsid: uuid.UUID, type: str, step_position: Optional[int]) -> None:
        if type not in PUBLIC_EVENT_TYPES:
            raise FunnelError("funnel.invalid_event", "Tipo de evento não permitido.")
        session = await self._repo.get_session(fsid)
        if session is None:
            raise FunnelNotFound("Sessão")
        funnel = await self._repo.get_funnel(session.funnel_id)
        if funnel is None or funnel.status != "published":
            raise FunnelGone()
        await self._repo.add_event(session, type, step_position)
        if type == "step_view" and step_position is not None:
            session.last_step_position = step_position
        if type == "funnel_completed" and session.completed_at is None:
            session.completed_at = datetime.now(timezone.utc)
        await self._repo._db.flush()
