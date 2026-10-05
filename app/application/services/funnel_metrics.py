"""Agregação pura das métricas de funil (coorte por data de entrada da sessão)."""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from domain.funnels import ab_confidence

_MONEY = Decimal("0.01")


@dataclass
class SessionRow:
    id: uuid.UUID
    variant_id: uuid.UUID
    utm_source: Optional[str]
    utm_campaign: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]
    registered_at: Optional[datetime]
    trial_at: Optional[datetime]
    subscribed_at: Optional[datetime]
    paid_at: Optional[datetime]
    first_payment_amount: Optional[Decimal]


def _ratio(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def _money(value: Decimal) -> str:
    return str(value.quantize(_MONEY))


def _revenue(rows) -> Decimal:
    return sum((r.first_payment_amount or Decimal("0") for r in rows if r.paid_at), Decimal("0"))


def aggregate_metrics(*, rows, step_views: dict, variants: list, step_names: dict) -> dict:
    total = len(rows)
    count = lambda attr, subset=rows: sum(1 for r in subset if getattr(r, attr) is not None)  # noqa: E731
    paid = count("paid_at")

    summary = {
        "sessions": total,
        "completed": count("completed_at"),
        "registered": count("registered_at"),
        "trials": count("trial_at"),
        "subscribed": count("subscribed_at"),
        "paid": paid,
        "paid_conversion": _ratio(paid, total),
        "revenue": _money(_revenue(rows)),
    }

    stages = [(f"step:{p}", step_names.get(p, f"Etapa {p + 1}"), step_views[p]) for p in sorted(step_views)]
    stages += [
        ("completed", "Concluiu o funil", summary["completed"]),
        ("registered", "Cadastro", summary["registered"]),
        ("trial", "Trial", summary["trials"]),
        ("subscribed", "Assinatura", summary["subscribed"]),
        ("paid", "Pagamento", paid),
    ]
    steps, previous = [], None
    for key, label, value in stages:
        drop = None if previous is None else (round(1 - value / previous, 4) if previous else 0.0)
        steps.append({"key": key, "label": label, "count": value,
                      "pct_of_total": _ratio(value, total), "drop_from_previous": drop})
        previous = value

    by_variant = defaultdict(list)
    for r in rows:
        by_variant[r.variant_id].append(r)
    variant_out = []
    control_conv = control_n = None
    for index, (variant_id, name, weight) in enumerate(variants):
        subset = by_variant.get(variant_id, [])
        n, conv = len(subset), count("paid_at", subset)
        is_control = index == 0
        if is_control:
            control_conv, control_n = conv, n
            confidence, low = None, n < 100
        else:
            confidence, low = ab_confidence(control_conv, control_n, conv, n)
        revenue = _revenue(subset)
        variant_out.append({
            "variant_id": variant_id, "name": name, "weight": weight, "sessions": n,
            "registered": count("registered_at", subset), "paid": conv,
            "conversion": _ratio(conv, n),
            "revenue_per_session": _money(revenue / n) if n else "0.00",
            "confidence": None if confidence is None else round(confidence, 4),
            "low_sample": low, "is_control": is_control,
        })

    by_source = defaultdict(list)
    for r in rows:
        by_source[(r.utm_source, r.utm_campaign)].append(r)
    sources = sorted(
        (
            {"utm_source": src, "utm_campaign": camp, "sessions": len(sub),
             "registered": count("registered_at", sub), "paid": count("paid_at", sub),
             "conversion": _ratio(count("paid_at", sub), len(sub))}
            for (src, camp), sub in by_source.items()
        ),
        key=lambda s: (-s["sessions"], s["utm_source"] is None, s["utm_source"] or ""),
    )

    series = defaultdict(lambda: {"sessions": 0, "paid": 0})
    for r in rows:
        series[r.created_at.date().isoformat()]["sessions"] += 1
        if r.paid_at:
            series[r.paid_at.date().isoformat()]["paid"] += 1
    timeseries = [{"date": d, **series[d]} for d in sorted(series)]

    return {"summary": summary, "steps": steps, "variants": variant_out, "sources": sources, "timeseries": timeseries}
