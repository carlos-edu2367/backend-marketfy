"""Regras puras de funis de venda (sem I/O)."""
from __future__ import annotations

import math
import random
import re
import uuid
from dataclasses import dataclass
from typing import Optional, Sequence

from domain.shared import BusinessRuleException

SLUG_MAX_LEN = 80
STEP_HTML_MAX_BYTES = 200_000
TRACKING_HTML_MAX_BYTES = 50_000
FUNNEL_STATUSES = ("draft", "published", "archived")
PUBLIC_EVENT_TYPES = frozenset({"step_view", "step_next", "funnel_completed"})
MILESTONE_FIELDS = {
    "registered": "registered_at",
    "trial_activated": "trial_at",
    "subscription_created": "subscribed_at",
    "invoice_paid": "paid_at",
}
LOW_SAMPLE_MIN_SESSIONS = 100
LOW_SAMPLE_MIN_CONVERSIONS = 10

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class FunnelError(BusinessRuleException):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class FunnelNotFound(FunnelError):
    def __init__(self, what: str = "Funil"):
        super().__init__("funnel.not_found", f"{what} não encontrado.")


def validate_slug(slug: str) -> str:
    if not slug or len(slug) > SLUG_MAX_LEN or not _SLUG_RE.match(slug):
        raise FunnelError(
            "funnel.invalid_slug",
            "Slug inválido: use letras minúsculas, números e hífens (até 80 caracteres).",
        )
    return slug


def validate_html_size(html: Optional[str], *, max_bytes: int, field: str) -> None:
    if html is not None and len(html.encode("utf-8")) > max_bytes:
        raise FunnelError("funnel.html_too_large", f"{field} excede {max_bytes // 1000} KB.")


@dataclass(frozen=True)
class VariantWeight:
    id: uuid.UUID
    weight: int
    is_active: bool


def pick_variant(variants: Sequence[VariantWeight], rng: Optional[random.Random] = None) -> Optional[uuid.UUID]:
    eligible = [v for v in variants if v.is_active and v.weight > 0]
    if not eligible:
        return None
    chooser = rng or random
    return chooser.choices([v.id for v in eligible], weights=[v.weight for v in eligible], k=1)[0]


@dataclass(frozen=True)
class VariantPublishInfo:
    name: str
    weight: int
    is_active: bool
    step_count: int


def publish_errors(*, plan_is_active: bool, variants: Sequence[VariantPublishInfo]) -> list[str]:
    errors: list[str] = []
    if not plan_is_active:
        errors.append("Selecione um plano de destino ativo.")
    if not any(v.is_active and v.weight > 0 for v in variants):
        errors.append("Ative ao menos uma variante com peso maior que zero.")
    for v in variants:
        if v.is_active and v.step_count == 0:
            errors.append(f"A variante '{v.name}' não tem etapas.")
    return errors


def ab_confidence(control_conv: int, control_n: int, conv: int, n: int) -> tuple[Optional[float], bool]:
    """Teste z de duas proporções (bicaudal). Retorna (confiança 0–1, amostra_pequena)."""
    if (
        control_n < LOW_SAMPLE_MIN_SESSIONS
        or n < LOW_SAMPLE_MIN_SESSIONS
        or (control_conv + conv) < LOW_SAMPLE_MIN_CONVERSIONS
    ):
        return None, True
    pooled = (control_conv + conv) / (control_n + n)
    se = math.sqrt(pooled * (1 - pooled) * (1 / control_n + 1 / n))
    if se == 0:
        return 0.0, False
    z = (conv / n - control_conv / control_n) / se
    return math.erf(abs(z) / math.sqrt(2)), False
