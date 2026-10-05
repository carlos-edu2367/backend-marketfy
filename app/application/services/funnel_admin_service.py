"""Casos de uso do admin para funis de venda."""
from __future__ import annotations

import uuid
from typing import Any

from domain.funnels import (
    STEP_HTML_MAX_BYTES, TRACKING_HTML_MAX_BYTES, FunnelError, FunnelNotFound, VariantPublishInfo,
    publish_errors, validate_html_size, validate_slug,
)
from infra.repositories.funnel_repo import FunnelRepository

WARN_STEPS = "Este funil já tem tráfego: alterar etapas afeta as métricas por etapa."
WARN_WEIGHTS = "Este funil já tem tráfego: alterar variantes enviesa as comparações anteriores."


class _Unset:
    pass


UNSET: Any = _Unset()


class FunnelAdminService:
    def __init__(self, repo: FunnelRepository):
        self._repo = repo

    # -- helpers ------------------------------------------------------------
    async def _funnel(self, funnel_id):
        funnel = await self._repo.get_funnel(funnel_id)
        if funnel is None:
            raise FunnelNotFound()
        return funnel

    async def _variant(self, variant_id):
        variant = await self._repo.get_variant(variant_id)
        if variant is None:
            raise FunnelNotFound("Variante")
        return variant

    async def _step(self, step_id):
        step = await self._repo.get_step(step_id)
        if step is None:
            raise FunnelNotFound("Etapa")
        return step

    async def _step_warnings(self, funnel_id) -> list[str]:
        funnel = await self._funnel(funnel_id)
        if funnel.status == "published" and await self._repo.count_sessions(funnel_id) > 0:
            return [WARN_STEPS]
        return []

    async def _weight_warnings(self, funnel_id) -> list[str]:
        return [WARN_WEIGHTS] if await self._repo.count_sessions(funnel_id) > 0 else []

    async def _ensure_slug_free(self, slug, exclude_id=None):
        validate_slug(slug)
        if await self._repo.slug_exists(slug, exclude_id=exclude_id):
            raise FunnelError("funnel.slug_taken", "Já existe um funil com esse slug.")

    async def _renumber(self, variant_id, ordered_steps):
        # Duas passadas para não violar uq(variant_id, position) no meio da troca.
        for i, step in enumerate(ordered_steps):
            step.position = -(i + 1)
        await self._repo._db.flush()
        for i, step in enumerate(ordered_steps):
            step.position = i
        await self._repo._db.flush()

    # -- funil --------------------------------------------------------------
    async def create_funnel(self, *, name, slug, plan_id, created_by):
        await self._ensure_slug_free(slug)
        funnel = await self._repo.create_funnel(name=name, slug=slug, plan_id=plan_id, created_by=created_by)
        await self._repo.add_variant(funnel_id=funnel.id, name="A", weight=100, position=0)
        return funnel

    async def update_funnel(self, funnel_id, *, name=None, slug=None, plan_id=UNSET, tracking_html=UNSET):
        funnel = await self._funnel(funnel_id)
        if name is not None:
            funnel.name = name
        if slug is not None and slug != funnel.slug:
            await self._ensure_slug_free(slug, exclude_id=funnel.id)
            funnel.slug = slug
        if plan_id is not UNSET:
            funnel.plan_id = plan_id
        if tracking_html is not UNSET:
            validate_html_size(tracking_html, max_bytes=TRACKING_HTML_MAX_BYTES, field="Scripts de rastreamento")
            funnel.tracking_html = tracking_html
        await self._repo._db.flush()
        return funnel

    async def duplicate_funnel(self, funnel_id, *, created_by):
        source = await self._funnel(funnel_id)
        base = f"{source.slug}-copia"[:80]
        slug, n = base, 2
        while await self._repo.slug_exists(slug):
            slug = f"{base}-{n}"[:80]
            n += 1
        copy = await self._repo.create_funnel(
            name=f"{source.name} (cópia)", slug=slug, plan_id=source.plan_id,
            tracking_html=source.tracking_html, created_by=created_by,
        )
        for variant in await self._repo.list_variants(source.id):
            await self._copy_variant(variant, funnel_id=copy.id, name=variant.name, position=variant.position)
        return copy

    async def _copy_variant(self, variant, *, funnel_id, name, position):
        new_variant = await self._repo.add_variant(
            funnel_id=funnel_id, name=name, weight=variant.weight, is_active=variant.is_active, position=position,
        )
        for step in await self._repo.list_steps(variant.id):
            await self._repo.add_step(variant_id=new_variant.id, position=step.position, name=step.name, html=step.html)
        return new_variant

    async def publish(self, funnel_id):
        funnel = await self._funnel(funnel_id)
        infos = [
            VariantPublishInfo(v.name, v.weight, v.is_active, await self._repo.count_steps(v.id))
            for v in await self._repo.list_variants(funnel.id)
        ]
        errors = publish_errors(plan_is_active=await self._repo.plan_is_active(funnel.plan_id), variants=infos)
        if errors:
            exc = FunnelError("funnel.publish_invalid", "O funil não pode ser publicado.")
            exc.errors = errors
            raise exc
        funnel.status = "published"
        await self._repo._db.flush()
        return funnel

    async def unpublish(self, funnel_id):
        funnel = await self._funnel(funnel_id)
        funnel.status = "draft"
        await self._repo._db.flush()
        return funnel

    async def archive(self, funnel_id):
        funnel = await self._funnel(funnel_id)
        funnel.status = "archived"
        await self._repo._db.flush()
        return funnel

    async def get_detail(self, funnel_id) -> dict:
        funnel = await self._funnel(funnel_id)
        variants = [
            {"variant": v, "steps": await self._repo.list_steps(v.id)}
            for v in await self._repo.list_variants(funnel.id)
        ]
        return {"funnel": funnel, "variants": variants, "session_count": await self._repo.count_sessions(funnel.id)}

    # -- variantes ----------------------------------------------------------
    async def add_variant(self, funnel_id, *, name, weight):
        await self._funnel(funnel_id)
        self._check_weight(weight)
        variants = await self._repo.list_variants(funnel_id)
        position = max((v.position for v in variants), default=-1) + 1
        variant = await self._repo.add_variant(funnel_id=funnel_id, name=name, weight=weight, position=position)
        return variant, await self._weight_warnings(funnel_id)

    async def update_variant(self, variant_id, *, name=None, weight=None, is_active=None):
        variant = await self._variant(variant_id)
        changes_traffic = False
        if name is not None:
            variant.name = name
        if weight is not None and weight != variant.weight:
            self._check_weight(weight)
            variant.weight = weight
            changes_traffic = True
        if is_active is not None and is_active != variant.is_active:
            variant.is_active = is_active
            changes_traffic = True
        await self._repo._db.flush()
        warnings = await self._weight_warnings(variant.funnel_id) if changes_traffic else []
        return variant, warnings

    async def delete_variant(self, variant_id) -> list[str]:
        variant = await self._variant(variant_id)
        if len(await self._repo.list_variants(variant.funnel_id)) <= 1:
            raise FunnelError("funnel.last_variant", "O funil precisa de ao menos uma variante.")
        warnings = await self._weight_warnings(variant.funnel_id)
        await self._repo.delete(variant)
        return warnings

    async def duplicate_variant(self, variant_id):
        variant = await self._variant(variant_id)
        variants = await self._repo.list_variants(variant.funnel_id)
        position = max(v.position for v in variants) + 1
        copy = await self._copy_variant(
            variant, funnel_id=variant.funnel_id, name=f"{variant.name} (cópia)"[:60], position=position,
        )
        copy.weight = 0  # a cópia nasce sem tráfego; o admin define o peso
        await self._repo._db.flush()
        return copy, []

    @staticmethod
    def _check_weight(weight):
        if not isinstance(weight, int) or weight < 0:
            raise FunnelError("funnel.invalid_weight", "O peso deve ser um inteiro maior ou igual a zero.")

    # -- etapas -------------------------------------------------------------
    async def add_step(self, variant_id, *, name, html):
        variant = await self._variant(variant_id)
        validate_html_size(html, max_bytes=STEP_HTML_MAX_BYTES, field="HTML da etapa")
        warnings = await self._step_warnings(variant.funnel_id)
        step = await self._repo.add_step(
            variant_id=variant.id, position=await self._repo.next_step_position(variant.id), name=name, html=html or "",
        )
        return step, warnings

    async def update_step(self, step_id, *, name=None, html=None):
        step = await self._step(step_id)
        variant = await self._variant(step.variant_id)
        if name is not None:
            step.name = name
        if html is not None:
            validate_html_size(html, max_bytes=STEP_HTML_MAX_BYTES, field="HTML da etapa")
            step.html = html
        await self._repo._db.flush()
        return step, await self._step_warnings(variant.funnel_id)

    async def delete_step(self, step_id) -> list[str]:
        step = await self._step(step_id)
        variant = await self._variant(step.variant_id)
        warnings = await self._step_warnings(variant.funnel_id)
        await self._repo.delete(step)
        await self._renumber(variant.id, await self._repo.list_steps(variant.id))
        return warnings

    async def reorder_steps(self, variant_id, ordered_ids: list[uuid.UUID]) -> list[str]:
        variant = await self._variant(variant_id)
        steps = await self._repo.list_steps(variant.id)
        by_id = {s.id: s for s in steps}
        if len(ordered_ids) != len(steps) or set(ordered_ids) != set(by_id):
            raise FunnelError("funnel.invalid_order", "A nova ordem deve conter todas as etapas da variante.")
        warnings = await self._step_warnings(variant.funnel_id)
        await self._renumber(variant.id, [by_id[i] for i in ordered_ids])
        return warnings
