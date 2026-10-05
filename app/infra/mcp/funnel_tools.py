"""Tools MCP dos funis de venda: criar, listar, detalhar, métricas por etapa e overview."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Awaitable, Callable, Literal, Optional

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.services.audit_service import AuditService
from application.services.funnel_admin_service import FunnelAdminService
from application.services.funnel_reporting import funnel_metrics_report, list_funnels_with_kpis
from domain.funnels import LOW_SAMPLE_MIN_SESSIONS, STEP_HTML_MAX_BYTES, FunnelNotFound
from infra.config.settings import get_settings
from infra.database.models import PlanModel
from infra.observability.audit import record_audit_event
from infra.repositories.audit_repo import SQLAlchemyAuditLogRepository
from infra.repositories.funnel_repo import FunnelRepository

STEP_HTML_HELP = (
    "HTML completo da etapa (CSS/JS inline permitidos, até 200 KB), exibido em iframe sandbox. "
    "Para navegar use atributos em botões/links: data-funnel-next (avança), data-funnel-back (volta), "
    "data-funnel-finish (encerra e leva ao cadastro/checkout do plano), ou Funnel.next()/back()/finish() no JS."
)


@dataclass
class ToolContext:
    db: AsyncSession
    admin: Any
    request: Request


@dataclass
class Tool:
    name: str
    title: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], Awaitable[Any]]
    read_only: bool = True
    annotations: dict = field(default_factory=dict)

    def definition(self) -> dict:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": schema,
            "annotations": {"title": self.title, "readOnlyHint": self.read_only, "destructiveHint": False,
                            "idempotentHint": self.read_only, "openWorldHint": False, **self.annotations},
        }


# -- Entradas -----------------------------------------------------------------------
class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StepInput(_Input):
    name: str = Field(min_length=1, max_length=120, description="Nome interno da etapa, ex.: 'Promessa', 'Oferta'.")
    html: str = Field(default="", description=STEP_HTML_HELP)


class CreateFunnelInput(_Input):
    name: str = Field(min_length=1, max_length=120, description="Nome interno do funil.")
    slug: str = Field(description="Slug da URL pública /f/<slug>: minúsculas, números e hífens (até 80).")
    plan_id: Optional[uuid.UUID] = Field(
        default=None, description="Plano vendido ao final do funil (veja funnel_list_plans). Obrigatório para publicar.")
    steps: list[StepInput] = Field(default_factory=list, max_length=30,
                                   description="Etapas da variante A, na ordem em que o visitante as vê.")
    tracking_html: Optional[str] = Field(
        default=None, description="Scripts de rastreamento (pixels) injetados em todas as etapas, até 50 KB.")


class ListFunnelsInput(_Input):
    status: Optional[Literal["draft", "published", "archived"]] = Field(
        default=None, description="Filtra por status. Omita para todos.")


class ListPlansInput(_Input):
    include_inactive: bool = Field(default=False, description="Inclui planos inativos (não publicáveis).")


class FunnelRefInput(_Input):
    funnel_id: Optional[uuid.UUID] = Field(default=None, description="ID do funil (de funnel_list).")
    slug: Optional[str] = Field(default=None, description="Alternativa ao funnel_id: slug do funil.")

    @model_validator(mode="after")
    def _one_ref(self):
        if (self.funnel_id is None) == (self.slug is None):
            raise ValueError("Informe exatamente um entre funnel_id e slug.")
        return self


class GetFunnelInput(FunnelRefInput):
    include_html: bool = Field(default=False, description="Inclui o HTML de cada etapa (pode ser grande).")


class MetricsInput(FunnelRefInput):
    date_from: Optional[date] = Field(default=None, description="Início da coorte (AAAA-MM-DD). Padrão: 30 dias até date_to.")
    date_to: Optional[date] = Field(default=None, description="Fim da coorte, inclusivo (AAAA-MM-DD). Padrão: hoje (UTC).")
    variant_id: Optional[uuid.UUID] = Field(default=None, description="Restringe a uma variante do teste A/B.")
    utm_source: Optional[str] = Field(default=None, description="Restringe a um utm_source, ex.: 'meta'.")
    utm_campaign: Optional[str] = Field(default=None, description="Restringe a um utm_campaign.")


# -- Helpers ------------------------------------------------------------------------
async def _resolve_funnel(ctx: ToolContext, ref: FunnelRefInput):
    repo = FunnelRepository(ctx.db)
    funnel = await (repo.get_funnel(ref.funnel_id) if ref.funnel_id else repo.get_funnel_by_slug(ref.slug))
    if funnel is None:
        raise FunnelNotFound()
    return funnel


def _public_url(slug: str) -> Optional[str]:
    base = get_settings().PUBLIC_FRONTEND_URL
    return f"{base.rstrip('/')}/f/{slug}" if base else None


async def _plan_summary(db: AsyncSession, plan_id) -> Optional[dict]:
    if plan_id is None:
        return None
    plan = await db.get(PlanModel, plan_id)
    if plan is None:
        return {"id": plan_id, "missing": True}
    return {"id": plan.id, "name": plan.name, "is_active": bool(plan.is_active)}


async def _funnel_detail(db: AsyncSession, funnel, *, include_html: bool) -> dict:
    detail = await FunnelAdminService(FunnelRepository(db)).get_detail(funnel.id)
    variants = []
    for item in detail["variants"]:
        v = item["variant"]
        steps = []
        for s in item["steps"]:
            step = {"id": s.id, "position": s.position, "name": s.name,
                    "html_bytes": len((s.html or "").encode("utf-8"))}
            if include_html:
                step["html"] = s.html
            steps.append(step)
        variants.append({"id": v.id, "name": v.name, "weight": v.weight, "is_active": v.is_active,
                         "position": v.position, "steps": steps})
    return {
        "id": funnel.id, "name": funnel.name, "slug": funnel.slug, "status": funnel.status,
        "public_url": _public_url(funnel.slug), "plan": await _plan_summary(db, funnel.plan_id),
        "has_tracking_html": bool(funnel.tracking_html), "created_at": funnel.created_at,
        "updated_at": funnel.updated_at, "session_count": detail["session_count"], "variants": variants,
    }


async def _metrics(ctx: ToolContext, args: MetricsInput):
    funnel = await _resolve_funnel(ctx, args)
    data = await funnel_metrics_report(ctx.db, funnel.id, from_=args.date_from, to=args.date_to,
                                       variant_id=args.variant_id, utm_source=args.utm_source,
                                       utm_campaign=args.utm_campaign)
    filters = {k: v for k, v in (("variant_id", args.variant_id), ("utm_source", args.utm_source),
                                 ("utm_campaign", args.utm_campaign)) if v is not None}
    return funnel, data, filters


def _notes(data: dict) -> list[str]:
    notes = []
    sessions = data["summary"]["sessions"]
    if sessions == 0:
        notes.append("Nenhuma sessão no período/filtros escolhidos.")
    elif sessions < LOW_SAMPLE_MIN_SESSIONS:
        notes.append(f"Amostra pequena ({sessions} sessões < {LOW_SAMPLE_MIN_SESSIONS}): trate as taxas como indicativas.")
    if data["maturing"]:
        notes.append("Coorte recente (menos de 14 dias): cadastro, trial e pagamento ainda podem crescer.")
    notes.append("Coorte por data de entrada da sessão; sessões de administradores são excluídas.")
    return notes


# -- Handlers -----------------------------------------------------------------------
async def create_funnel(ctx: ToolContext, args: CreateFunnelInput):
    for step in args.steps:
        if len(step.html.encode("utf-8")) > STEP_HTML_MAX_BYTES:
            raise ValueError(f"O HTML da etapa '{step.name}' excede {STEP_HTML_MAX_BYTES // 1000} KB.")
    svc = FunnelAdminService(FunnelRepository(ctx.db))
    funnel = await svc.create_funnel(name=args.name, slug=args.slug, plan_id=args.plan_id, created_by=ctx.admin.id)
    if args.tracking_html is not None:
        await svc.update_funnel(funnel.id, tracking_html=args.tracking_html)
    variant = (await svc._repo.list_variants(funnel.id))[0]
    for step in args.steps:
        await svc.add_step(variant.id, name=step.name, html=step.html)
    await record_audit_event(AuditService(SQLAlchemyAuditLogRepository(ctx.db)), ctx.request, actor=ctx.admin,
                             action="funnel.created", resource_type="funnel", resource_id=str(funnel.id),
                             result="success", metadata={"source": "mcp"})
    next_steps = ["O funil nasce como rascunho (draft) e só fica público após publicar no painel admin."]
    if args.plan_id is None:
        next_steps.append("Defina um plano (plan_id) antes de publicar.")
    if not args.steps:
        next_steps.append("Adicione etapas à variante A no painel admin antes de publicar.")
    return {"funnel": await _funnel_detail(ctx.db, funnel, include_html=False), "next_steps": next_steps}


async def list_funnels(ctx: ToolContext, args: ListFunnelsInput):
    funnels = await list_funnels_with_kpis(ctx.db)
    if args.status:
        funnels = [f for f in funnels if f["status"] == args.status]
    for f in funnels:
        f["public_url"] = _public_url(f["slug"])
    return {"count": len(funnels), "kpi_window": "últimos 30 dias", "funnels": funnels}


async def list_plans(ctx: ToolContext, args: ListPlansInput):
    stmt = select(PlanModel).order_by(PlanModel.display_order, PlanModel.name)
    if not args.include_inactive:
        stmt = stmt.where(PlanModel.is_active.is_(True))
    plans = (await ctx.db.execute(stmt)).scalars().all()
    return {"plans": [{"id": p.id, "name": p.name, "type": p.type, "is_active": bool(p.is_active),
                       "price_monthly": p.price_monthly, "price_180days": p.price_180days,
                       "price_annual": p.price_annual, "description": p.description} for p in plans]}


async def get_funnel(ctx: ToolContext, args: GetFunnelInput):
    funnel = await _resolve_funnel(ctx, args)
    return await _funnel_detail(ctx.db, funnel, include_html=args.include_html)


async def step_metrics(ctx: ToolContext, args: MetricsInput):
    funnel, data, filters = await _metrics(ctx, args)
    return {
        "funnel": {"id": funnel.id, "name": funnel.name, "status": funnel.status},
        "period": data["period"], "filters": filters, "maturing": data["maturing"],
        "sessions": data["summary"]["sessions"],
        "steps": data["steps"],
        "legend": "count = sessões que chegaram ao estágio; pct_of_total = count/sessões; "
                  "drop_from_previous = fração perdida em relação ao estágio anterior.",
        "notes": _notes(data),
    }


async def overview(ctx: ToolContext, args: MetricsInput):
    funnel, data, filters = await _metrics(ctx, args)
    summary = data["summary"]
    sessions = summary["sessions"]
    ratio = lambda n: round(n / sessions, 4) if sessions else 0.0  # noqa: E731
    drops = [s for s in data["steps"] if s["drop_from_previous"] is not None]
    bottleneck = max(drops, key=lambda s: s["drop_from_previous"], default=None)
    return {
        "funnel": {"id": funnel.id, "name": funnel.name, "slug": funnel.slug, "status": funnel.status,
                   "public_url": _public_url(funnel.slug), "plan": await _plan_summary(ctx.db, funnel.plan_id)},
        "period": data["period"], "filters": filters, "maturing": data["maturing"],
        "summary": {**summary, "completion_rate": ratio(summary["completed"]),
                    "registration_rate": ratio(summary["registered"]), "trial_rate": ratio(summary["trials"])},
        "biggest_drop": None if bottleneck is None or not bottleneck["drop_from_previous"] else {
            "stage": bottleneck["label"], "key": bottleneck["key"], "drop": bottleneck["drop_from_previous"]},
        "variants": data["variants"],
        "top_sources": data["sources"][:10],
        "timeseries": data["timeseries"],
        "notes": _notes(data),
    }


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool(
        name="funnel_create",
        title="Criar funil de vendas",
        description=(
            "Cria um funil de vendas em rascunho (status draft) com a variante A e, opcionalmente, suas etapas em HTML. "
            "Não publica: a publicação é feita no painel admin. Use funnel_list_plans para obter o plan_id. "
            "Retorna o funil criado (ids do funil, variante e etapas) e os próximos passos."
        ),
        input_model=CreateFunnelInput, handler=create_funnel, read_only=False,
    ),
    Tool(
        name="funnel_list",
        title="Listar funis",
        description=("Lista os funis de venda com status, slug, URL pública e KPIs dos últimos 30 dias "
                     "(sessões, pagamentos e conversão em pagamento). Ponto de partida para obter funnel_id."),
        input_model=ListFunnelsInput, handler=list_funnels,
    ),
    Tool(
        name="funnel_get",
        title="Ver detalhes de um funil",
        description=("Detalhes de um funil: status, plano, URL pública, variantes A/B (peso, ativa) e etapas "
                     "(nome, posição, tamanho do HTML). Use include_html=true para ler o HTML das etapas."),
        input_model=GetFunnelInput, handler=get_funnel,
    ),
    Tool(
        name="funnel_step_metrics",
        title="Ver métricas por etapa",
        description=(
            "Funil etapa a etapa de uma coorte de sessões: visualizações de cada etapa HTML, conclusão, cadastro, "
            "trial, assinatura e pagamento, com % do total e queda em relação ao estágio anterior. "
            "Filtros opcionais: período, variante e UTM. Padrão: últimos 30 dias."
        ),
        input_model=MetricsInput, handler=step_metrics,
    ),
    Tool(
        name="funnel_overview",
        title="Overview de funil",
        description=(
            "Visão geral do desempenho de um funil: resumo (sessões, cadastros, trials, assinaturas, pagamentos, "
            "receita e taxas), maior gargalo, comparação A/B por variante (conversão, receita por sessão, confiança, "
            "amostra pequena), principais origens UTM e série diária. Filtros opcionais: período, variante e UTM."
        ),
        input_model=MetricsInput, handler=overview,
    ),
    Tool(
        name="funnel_list_plans",
        title="Listar planos",
        description="Lista os planos do Marketfy (id, nome, preços) que podem ser vendidos por um funil.",
        input_model=ListPlansInput, handler=list_plans,
    ),
]}
