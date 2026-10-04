from __future__ import annotations

import os
import random
import sys
import uuid

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.funnels import (
    FunnelError, VariantPublishInfo, VariantWeight, ab_confidence, pick_variant,
    publish_errors, validate_html_size, validate_slug,
)


@pytest.mark.parametrize("slug", ["oferta", "black-friday-2026", "a1-b2"])
def test_validate_slug_accepts(slug):
    assert validate_slug(slug) == slug


@pytest.mark.parametrize("slug", ["", "Oferta", "-x", "x-", "a--b", "com espaço", "a" * 81, "ação"])
def test_validate_slug_rejects(slug):
    with pytest.raises(FunnelError) as exc:
        validate_slug(slug)
    assert exc.value.code == "funnel.invalid_slug"


def test_validate_html_size_limits_bytes_not_chars():
    validate_html_size("a" * 10, max_bytes=10, field="html")
    validate_html_size(None, max_bytes=10, field="html")
    with pytest.raises(FunnelError) as exc:
        validate_html_size("é" * 6, max_bytes=10, field="html")  # 12 bytes
    assert exc.value.code == "funnel.html_too_large"


def test_pick_variant_respects_weights():
    a, b = uuid.uuid4(), uuid.uuid4()
    variants = [VariantWeight(a, 75, True), VariantWeight(b, 25, True)]
    rng = random.Random(42)
    picks = [pick_variant(variants, rng) for _ in range(4000)]
    share_a = picks.count(a) / len(picks)
    assert 0.72 < share_a < 0.78


def test_pick_variant_ignores_inactive_and_zero_weight():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    variants = [VariantWeight(a, 50, False), VariantWeight(b, 0, True), VariantWeight(c, 10, True)]
    assert {pick_variant(variants, random.Random(i)) for i in range(50)} == {c}


def test_pick_variant_returns_none_without_eligible():
    assert pick_variant([VariantWeight(uuid.uuid4(), 0, True)]) is None
    assert pick_variant([]) is None


def test_publish_errors_ok():
    info = [VariantPublishInfo("A", 100, True, 2), VariantPublishInfo("B", 0, False, 0)]
    assert publish_errors(plan_is_active=True, variants=info) == []


def test_publish_errors_lists_every_problem():
    info = [VariantPublishInfo("A", 100, True, 0), VariantPublishInfo("B", 0, True, 1)]
    errors = publish_errors(plan_is_active=False, variants=info)
    assert "Selecione um plano de destino ativo." in errors
    assert "A variante 'A' não tem etapas." in errors


def test_publish_errors_requires_weighted_active_variant():
    info = [VariantPublishInfo("A", 0, True, 1)]
    assert "Ative ao menos uma variante com peso maior que zero." in publish_errors(
        plan_is_active=True, variants=info
    )


def test_ab_confidence_detects_difference():
    confidence, low = ab_confidence(10, 1000, 30, 1000)
    assert low is False
    assert confidence > 0.99


def test_ab_confidence_equal_rates_is_zero():
    confidence, low = ab_confidence(20, 1000, 20, 1000)
    assert low is False
    assert confidence == pytest.approx(0.0)


@pytest.mark.parametrize("args", [(5, 99, 9, 1000), (2, 500, 3, 500)])
def test_ab_confidence_low_sample(args):
    assert ab_confidence(*args) == (None, True)
