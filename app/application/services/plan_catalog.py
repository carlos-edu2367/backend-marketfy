"""Planos que podem aparecer para visitantes (landing, /precos, /plans)."""

from typing import Iterable, List

from domain.identity import Plan, PlanType

PUBLIC_PLAN_TYPES = (PlanType.PAID, PlanType.TRIAL)


def select_public_plans(plans: Iterable[Plan]) -> List[Plan]:
    """Só planos ativos pagos/trial, na ordem definida pelo admin e depois pelo preço mensal."""
    visible = [p for p in plans if p.is_active and p.type in PUBLIC_PLAN_TYPES]
    return sorted(visible, key=lambda p: (p.display_order, p.price_monthly))
