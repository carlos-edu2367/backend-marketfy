from __future__ import annotations

import pytest

from funnel_fixtures import db, engine, seed_admin, seed_funnel, seed_plan, session_factory  # noqa: F401
from application.services.funnel_admin_service import WARN_STEPS, WARN_WEIGHTS, FunnelAdminService
from domain.funnels import FunnelError
from infra.repositories.funnel_repo import FunnelRepository


def _svc(db):
    repo = FunnelRepository(db)
    return FunnelAdminService(repo), repo


@pytest.mark.asyncio
async def test_create_funnel_creates_default_variant(db):
    svc, repo = _svc(db)
    admin = await seed_admin(db)
    plan = await seed_plan(db)
    funnel = await svc.create_funnel(name="Oferta", slug="oferta", plan_id=plan.id, created_by=admin.id)
    variants = await repo.list_variants(funnel.id)
    assert funnel.status == "draft"
    assert [(v.name, v.weight, v.position) for v in variants] == [("A", 100, 0)]


@pytest.mark.asyncio
async def test_create_funnel_rejects_duplicate_slug(db):
    svc, _ = _svc(db)
    await seed_funnel(db, slug="oferta")
    admin = await seed_admin(db)
    with pytest.raises(FunnelError) as exc:
        await svc.create_funnel(name="X", slug="oferta", plan_id=None, created_by=admin.id)
    assert exc.value.code == "funnel.slug_taken"


@pytest.mark.asyncio
async def test_publish_validates_and_lists_errors(db):
    svc, repo = _svc(db)
    funnel, variant = await seed_funnel(db, steps=0)
    with pytest.raises(FunnelError) as exc:
        await svc.publish(funnel.id)
    assert exc.value.code == "funnel.publish_invalid"
    assert "A variante 'A' não tem etapas." in exc.value.errors
    await svc.add_step(variant.id, name="Intro", html="<p>oi</p>")
    assert (await svc.publish(funnel.id)).status == "published"


@pytest.mark.asyncio
async def test_unpublish_and_archive(db):
    svc, _ = _svc(db)
    funnel, _ = await seed_funnel(db, status="published")
    assert (await svc.unpublish(funnel.id)).status == "draft"
    assert (await svc.archive(funnel.id)).status == "archived"


@pytest.mark.asyncio
async def test_step_changes_warn_only_when_published_with_traffic(db):
    svc, repo = _svc(db)
    funnel, variant = await seed_funnel(db, status="published")
    _, warnings = await svc.add_step(variant.id, name="Nova", html="")
    assert warnings == []
    await repo.create_session(funnel_id=funnel.id, variant_id=variant.id)
    _, warnings = await svc.add_step(variant.id, name="Outra", html="")
    assert warnings == [WARN_STEPS]


@pytest.mark.asyncio
async def test_weight_change_warns_with_traffic(db):
    svc, repo = _svc(db)
    funnel, variant = await seed_funnel(db)
    await repo.create_session(funnel_id=funnel.id, variant_id=variant.id)
    _, warnings = await svc.update_variant(variant.id, weight=50)
    assert warnings == [WARN_WEIGHTS]
    _, warnings = await svc.update_variant(variant.id, name="Controle")
    assert warnings == []


@pytest.mark.asyncio
async def test_reorder_and_delete_step_keep_positions_contiguous(db):
    svc, repo = _svc(db)
    _, variant = await seed_funnel(db, steps=3)
    s0, s1, s2 = await repo.list_steps(variant.id)
    await svc.reorder_steps(variant.id, [s2.id, s0.id, s1.id])
    assert [s.id for s in await repo.list_steps(variant.id)] == [s2.id, s0.id, s1.id]
    await svc.delete_step(s2.id)
    steps = await repo.list_steps(variant.id)
    assert [(s.id, s.position) for s in steps] == [(s0.id, 0), (s1.id, 1)]


@pytest.mark.asyncio
async def test_reorder_rejects_mismatched_ids(db):
    svc, repo = _svc(db)
    _, variant = await seed_funnel(db, steps=2)
    s0, _ = await repo.list_steps(variant.id)
    with pytest.raises(FunnelError) as exc:
        await svc.reorder_steps(variant.id, [s0.id])
    assert exc.value.code == "funnel.invalid_order"


@pytest.mark.asyncio
async def test_cannot_delete_last_variant(db):
    svc, _ = _svc(db)
    _, variant = await seed_funnel(db)
    with pytest.raises(FunnelError) as exc:
        await svc.delete_variant(variant.id)
    assert exc.value.code == "funnel.last_variant"


@pytest.mark.asyncio
async def test_duplicate_funnel_copies_variants_and_steps(db):
    svc, repo = _svc(db)
    funnel, variant = await seed_funnel(db, slug="oferta", status="published", steps=2)
    await svc.add_variant(funnel.id, name="B", weight=50)
    admin = await seed_admin(db)
    copy = await svc.duplicate_funnel(funnel.id, created_by=admin.id)
    assert copy.slug == "oferta-copia"
    assert copy.status == "draft"
    variants = await repo.list_variants(copy.id)
    assert [v.name for v in variants] == ["A", "B"]
    assert len(await repo.list_steps(variants[0].id)) == 2
    copy2 = await svc.duplicate_funnel(funnel.id, created_by=admin.id)
    assert copy2.slug == "oferta-copia-2"


@pytest.mark.asyncio
async def test_html_size_enforced(db):
    svc, _ = _svc(db)
    _, variant = await seed_funnel(db)
    with pytest.raises(FunnelError) as exc:
        await svc.add_step(variant.id, name="grande", html="a" * 200_001)
    assert exc.value.code == "funnel.html_too_large"
