"""Escolhe, entre as assinaturas locais de um owner, a que deve governar o
acesso — nunca simplesmente a mais recente por updated_at (um checkout
recorrente aberto e abandonado nao pode derrubar quem ja tinha acesso)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence


def _priority_rank(sub: Any, *, now: datetime) -> int:
    if sub.status in ("active", "trialing"):
        return 0
    if sub.cancel_at_period_end and sub.expires_at is not None and sub.expires_at >= now:
        return 1
    if sub.status == "past_due":
        return 2
    if sub.status == "pending":
        return 3
    return 4


def select_current_subscription(subs: Sequence[Any]) -> Optional[Any]:
    if not subs:
        return None
    now = datetime.utcnow()
    return min(
        subs,
        key=lambda sub: (_priority_rank(sub, now=now), -sub.updated_at.timestamp()),
    )
