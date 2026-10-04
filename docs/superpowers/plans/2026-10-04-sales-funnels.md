# Funis de Venda — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que o admin crie funis de venda (HTML por etapa, A/B por variantes com peso, captura de UTM) para os planos do Marketfy e acompanhe a conversão da visita até a primeira fatura paga.

**Architecture:** Backend FastAPI ganha 5 tabelas (`funnels`, `funnel_variants`, `funnel_steps`, `funnel_sessions`, `funnel_events`), um módulo de domínio puro, um repositório, três serviços (admin, público, atribuição), um serviço de métricas e dois routers (público e admin). A atribuição se pluga nos services de billing por um *decorator* do cliente de analytics (`FunnelTrackingAnalytics`) — os pontos que já disparam `trial_activated` / `subscription_created` / `invoice_paid` ao PostHog passam a marcar a sessão de funil, em sessão de banco própria e sem nunca propagar erro. O frontend React ganha a página pública `/f/:slug` (etapas em `<iframe sandbox>` sem `allow-same-origin`, protocolo `postMessage`), a passagem do `fsid` pelo cadastro/checkout e as telas de admin (lista, editor com prévia ao vivo, métricas).

**Tech Stack:** Python 3.11, FastAPI 0.109, SQLAlchemy 2 async, Alembic, pytest + pytest-asyncio + aiosqlite; React 18, Vite, react-router v6, axios, recharts, Vitest + Testing Library.

**Spec:** `backend/docs/superpowers/specs/2026-10-04-sales-funnels-design.md`

## Global Constraints

- Slug: regex `^[a-z0-9]+(-[a-z0-9]+)*$`, até 80 caracteres, único.
- HTML de etapa ≤ 200 KB (200_000 bytes UTF-8); `tracking_html` ≤ 50 KB (50_000 bytes).
- Peso de variante: inteiro ≥ 0; sorteio proporcional à soma das variantes ativas.
- Tipos de evento aceitos no endpoint público: somente `step_view`, `step_next`, `funnel_completed`.
- Marcos gravados só pelo backend: `registered`, `trial_activated`, `subscription_created`, `invoice_paid`.
- A/B: "amostra pequena" quando qualquer variante < 100 sessões ou conversões somadas < 10.
- Iframe: `sandbox="allow-scripts allow-forms allow-popups"` — **nunca** `allow-same-origin` nem `allow-top-navigation`.
- Mensagens `postMessage` aceitas: `{source: 'marketfy-funnel', type: 'next'|'back'|'finish'}`, e só de `iframe.contentWindow`.
- Atribuição nunca propaga exceção para a operação de negócio.
- Sessões não guardam IP nem user agent.
- Métricas: coorte por `funnel_sessions.created_at`; sessões de usuários `admin` excluídas.
- Backend e frontend são **repositórios git separados** (`marketfy/backend`, `marketfy/frontend`); cada commit é feito no repo correspondente.
- Todo commit termina com a linha `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Refinamentos em relação à spec (decididos durante o planejamento)

1. **Endpoint de prévia:** `GET /api/v1/admin/funnels/variants/{variant_id}/preview` (a URL pública de prévia só carrega o `variant_id`: `/f/<slug>?preview=<variant_id>`).
2. **Atribuição via decorator de analytics** em vez de chamadas espalhadas: os services continuam chamando `self._analytics.track_event(...)`; o default deles passa de `PostHogClient()` para `build_analytics()`. Flag `FUNNEL_ATTRIBUTION_ENABLED` (default `True`, desligada em `tests/conftest.py`).
3. **Pagamento recorrente:** `SubscriptionService.process_recurring_event` passa a emitir `invoice_paid` (com `billing_mode: recurring`) em `PAYMENT_RECEIVED`; sem isso, funis de planos recorrentes nunca registrariam pagamento.
4. **Métricas:** filtragem de coorte e contagem por etapa em SQL; agregação final (resumo, variantes, origens, série) numa função pura em Python, fácil de testar. Suficiente para o volume atual.

---

## Mapa de arquivos

### Backend (`marketfy/backend`)
| Arquivo | Responsabilidade |
|---|---|
| `app/domain/funnels.py` (novo) | Constantes, erros, `validate_slug`, `validate_html_size`, `pick_variant`, `publish_errors`, `ab_confidence` |
| `app/infra/database/models.py` (mod.) | 5 modelos novos no fim do arquivo |
| `alembic/versions/20261004_0023_sales_funnels.py` (novo) | Migration |
| `app/infra/repositories/funnel_repo.py` (novo) | Acesso a dados de funis, variantes, etapas, sessões, eventos |
| `app/application/services/funnel_admin_service.py` (novo) | CRUD, duplicação, publicação, avisos |
| `app/application/services/funnel_public_service.py` (novo) | Início/retomada de sessão, eventos públicos |
| `app/application/services/funnel_attribution_service.py` (novo) | `link_registration`, `claim`, `mark` |
| `app/infra/observability/funnel_analytics.py` (novo) | `FunnelTrackingAnalytics`, `build_analytics` |
| `app/application/services/funnel_metrics.py` (novo) | `aggregate_metrics` (puro) |
| `app/infra/repositories/funnel_metrics_repo.py` (novo) | Consultas de coorte e de etapas, lista com KPIs de 30 dias |
| `app/infra/web/routers/funnels_public.py` (novo) | `/api/v1/funnels/...` |
| `app/infra/web/routers/funnels_admin.py` (novo) | `/api/v1/admin/funnels/...` |
| `app/application/dtos_funnels.py` (novo) | DTOs Pydantic dos dois routers |
| `app/infra/web/main.py`, `app/infra/web/dependencies.py`, `app/infra/config/settings.py` (mod.) | Registro de routers, dependências, flag |
| `app/application/dtos.py`, `app/infra/web/routers/identity.py` (mod.) | `funnel_session_id` no cadastro |
| `app/application/services/{subscription,invoice,recurring}_service.py` (mod.) | Default `build_analytics()`; `invoice_paid` recorrente |
| `tests/conftest.py` (mod.) | `FUNNEL_ATTRIBUTION_ENABLED=false` |

### Frontend (`marketfy/frontend`)
| Arquivo | Responsabilidade |
|---|---|
| `src/lib/funnels.js` (novo) | Storage do fsid, UTMs, srcdoc + helper, parser de mensagens, chamadas de API |
| `src/components/funnels/FunnelStepFrame.jsx` (novo) | Iframe sandbox + escuta de `postMessage` |
| `src/pages/funnels/FunnelPlayer.jsx` (novo) | Página pública `/f/:slug` e modo prévia |
| `src/pages/admin/funnels/FunnelsList.jsx` (novo) | Lista de funis |
| `src/pages/admin/funnels/FunnelEditor.jsx` (novo) | Abas Configuração / Etapas / Métricas |
| `src/components/admin/funnels/FunnelConfigTab.jsx`, `FunnelStepsTab.jsx`, `FunnelMetricsTab.jsx` (novos) | Conteúdo das abas |
| `src/App.jsx`, `src/components/layout/SaaSLayout.jsx` (mod.) | Rotas e item de menu |
| `src/pages/auth/Register.jsx`, `src/pages/auth/Plans.jsx` (mod.) | `fsid` no cadastro e *claim* |

---

# BACKEND

Todos os comandos de backend rodam em `marketfy/backend`. Testes: `python -m pytest <caminho> -v`.

### Task 1: Domínio puro de funis

**Files:**
- Create: `app/domain/funnels.py`
- Test: `tests/unit/test_funnel_domain.py`

**Interfaces:**
- Produces:
  - `SLUG_MAX_LEN = 80`, `STEP_HTML_MAX_BYTES = 200_000`, `TRACKING_HTML_MAX_BYTES = 50_000`
  - `PUBLIC_EVENT_TYPES: frozenset[str]`, `MILESTONE_FIELDS: dict[str, str]` (evento → coluna da sessão)
  - `FUNNEL_STATUSES = ("draft", "published", "archived")`
  - `class FunnelError(BusinessRuleException)` com `.code: str`; `class FunnelNotFound(FunnelError)`
  - `validate_slug(slug: str) -> str`, `validate_html_size(html: str | None, *, max_bytes: int, field: str) -> None`
  - `@dataclass(frozen=True) class VariantWeight: id: uuid.UUID; weight: int; is_active: bool`
  - `pick_variant(variants: Sequence[VariantWeight], rng: random.Random | None = None) -> uuid.UUID | None`
  - `@dataclass(frozen=True) class VariantPublishInfo: name: str; weight: int; is_active: bool; step_count: int`
  - `publish_errors(*, plan_is_active: bool, variants: Sequence[VariantPublishInfo]) -> list[str]`
  - `ab_confidence(control_conv: int, control_n: int, conv: int, n: int) -> tuple[float | None, bool]` (confiança 0–1, low_sample)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_funnel_domain.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_funnel_domain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'domain.funnels'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/domain/funnels.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_funnel_domain.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add app/domain/funnels.py tests/unit/test_funnel_domain.py
git commit -m "feat(funnels): domínio puro (slug, sorteio de variante, publicação, A/B)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Modelos e migration

**Files:**
- Modify: `app/infra/database/models.py` (append no fim do arquivo)
- Create: `alembic/versions/20261004_0023_sales_funnels.py`
- Test: `tests/unit/test_funnel_models.py`

**Interfaces:**
- Produces: `FunnelModel`, `FunnelVariantModel`, `FunnelStepModel`, `FunnelSessionModel`, `FunnelEventModel` (tabelas `funnels`, `funnel_variants`, `funnel_steps`, `funnel_sessions`, `funnel_events`); revision Alembic `20261004_0023`.

- [ ] **Step 1: Confirm the Alembic head**

Run (em `marketfy/backend`): `alembic heads`
Expected: uma única linha `20260914_0022 (head)`. Se houver outro head, **pare** e use-o como `down_revision` (ou crie merge) — informe no relatório da task.

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_funnel_models.py
from __future__ import annotations

import os
import sys
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.database.setup import Base
import infra.database.models  # noqa: F401
from infra.database.models import (
    FunnelEventModel, FunnelModel, FunnelSessionModel, FunnelStepModel, FunnelVariantModel, UserModel,
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _funnel(session):
    admin = UserModel(id=uuid.uuid4(), name="adm", email=f"{uuid.uuid4()}@t.com", password_hash="h", role="admin")
    session.add(admin)
    await session.flush()
    funnel = FunnelModel(slug="oferta", name="Oferta", created_by=admin.id)
    session.add(funnel)
    await session.flush()
    variant = FunnelVariantModel(funnel_id=funnel.id, name="A", weight=100, position=0)
    session.add(variant)
    await session.flush()
    return funnel, variant


@pytest.mark.asyncio
async def test_defaults_and_relations(session):
    funnel, variant = await _funnel(session)
    session.add(FunnelStepModel(variant_id=variant.id, position=0, name="Intro", html="<h1>Oi</h1>"))
    fs = FunnelSessionModel(funnel_id=funnel.id, variant_id=variant.id, utm_source="meta")
    session.add(fs)
    await session.flush()
    session.add(FunnelEventModel(session_id=fs.id, funnel_id=funnel.id, variant_id=variant.id,
                                 type="step_view", step_position=0))
    await session.flush()
    assert funnel.status == "draft"
    assert variant.is_active is True
    assert fs.created_at is not None


@pytest.mark.asyncio
async def test_step_position_unique_per_variant(session):
    _, variant = await _funnel(session)
    session.add(FunnelStepModel(variant_id=variant.id, position=0, name="1", html=""))
    session.add(FunnelStepModel(variant_id=variant.id, position=0, name="2", html=""))
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_step_view_unique_per_session_position(session):
    funnel, variant = await _funnel(session)
    fs = FunnelSessionModel(funnel_id=funnel.id, variant_id=variant.id)
    session.add(fs)
    await session.flush()
    common = dict(session_id=fs.id, funnel_id=funnel.id, variant_id=variant.id, step_position=0)
    session.add(FunnelEventModel(type="step_next", **common))
    session.add(FunnelEventModel(type="step_next", **common))
    await session.flush()  # step_next pode repetir
    session.add(FunnelEventModel(type="step_view", **common))
    session.add(FunnelEventModel(type="step_view", **common))
    with pytest.raises(IntegrityError):
        await session.flush()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_funnel_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'FunnelEventModel'`

- [ ] **Step 4: Append the models**

No fim de `app/infra/database/models.py` (os imports `CheckConstraint, Column, String, Boolean, Integer, ForeignKey, DateTime, Numeric, Text, UniqueConstraint, Index, text` já existem no topo do arquivo; acrescente `timezone` ao import de `datetime`: `from datetime import datetime, timezone`):

```python
# =============================================================================
# FUNIS DE VENDA (admin vende planos do Marketfy)
# =============================================================================

def _utcnow():
    return datetime.now(timezone.utc)


class FunnelModel(Base):
    __tablename__ = "funnels"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_funnels_status"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String(80), nullable=False, unique=True, index=True)
    name = Column(String(120), nullable=False)
    status = Column(String(20), nullable=False, default="draft", server_default="draft")
    plan_id = Column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=True)
    tracking_html = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class FunnelVariantModel(Base):
    __tablename__ = "funnel_variants"
    __table_args__ = (CheckConstraint("weight >= 0", name="ck_funnel_variants_weight"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    funnel_id = Column(UUID(as_uuid=True), ForeignKey("funnels.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(60), nullable=False)
    weight = Column(Integer, nullable=False, default=100, server_default="100")
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    position = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class FunnelStepModel(Base):
    __tablename__ = "funnel_steps"
    __table_args__ = (UniqueConstraint("variant_id", "position", name="uq_funnel_steps_variant_position"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    variant_id = Column(UUID(as_uuid=True), ForeignKey("funnel_variants.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    position = Column(Integer, nullable=False)
    name = Column(String(120), nullable=False)
    html = Column(Text, nullable=False, default="", server_default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class FunnelSessionModel(Base):
    __tablename__ = "funnel_sessions"
    __table_args__ = (Index("ix_funnel_sessions_funnel_created", "funnel_id", "created_at"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    funnel_id = Column(UUID(as_uuid=True), ForeignKey("funnels.id", ondelete="CASCADE"), nullable=False)
    variant_id = Column(UUID(as_uuid=True), ForeignKey("funnel_variants.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    utm_source = Column(String(200), nullable=True)
    utm_medium = Column(String(200), nullable=True)
    utm_campaign = Column(String(200), nullable=True)
    utm_content = Column(String(200), nullable=True)
    utm_term = Column(String(200), nullable=True)
    referrer = Column(String(500), nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    last_step_position = Column(Integer, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    registered_at = Column(DateTime(timezone=True), nullable=True)
    trial_at = Column(DateTime(timezone=True), nullable=True)
    subscribed_at = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    first_payment_amount = Column(Numeric(10, 2), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class FunnelEventModel(Base):
    __tablename__ = "funnel_events"
    __table_args__ = (
        Index("ix_funnel_events_funnel_occurred", "funnel_id", "occurred_at"),
        Index(
            "uq_funnel_events_step_view",
            "session_id", "step_position",
            unique=True,
            postgresql_where=text("type = 'step_view'"),
            sqlite_where=text("type = 'step_view'"),
        ),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("funnel_sessions.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    funnel_id = Column(UUID(as_uuid=True), ForeignKey("funnels.id", ondelete="CASCADE"), nullable=False)
    variant_id = Column(UUID(as_uuid=True), ForeignKey("funnel_variants.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(40), nullable=False)
    step_position = Column(Integer, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
```

- [ ] **Step 5: Run model tests**

Run: `python -m pytest tests/unit/test_funnel_models.py -v`
Expected: PASS (3 testes)

- [ ] **Step 6: Write the migration**

```python
# alembic/versions/20261004_0023_sales_funnels.py
"""Funis de venda: funnels, variants, steps, sessions, events."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261004_0023"
down_revision: Union[str, Sequence[str], None] = "20260914_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "funnels",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("plan_id", UUID, sa.ForeignKey("plans.id"), nullable=True),
        sa.Column("tracking_html", sa.Text(), nullable=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_funnels_status"),
    )
    op.create_index("ix_funnels_slug", "funnels", ["slug"], unique=True)

    op.create_table(
        "funnel_variants",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("funnel_id", UUID, sa.ForeignKey("funnels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("weight >= 0", name="ck_funnel_variants_weight"),
    )
    op.create_index("ix_funnel_variants_funnel_id", "funnel_variants", ["funnel_id"])

    op.create_table(
        "funnel_steps",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("variant_id", UUID, sa.ForeignKey("funnel_variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("html", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("variant_id", "position", name="uq_funnel_steps_variant_position"),
    )
    op.create_index("ix_funnel_steps_variant_id", "funnel_steps", ["variant_id"])

    op.create_table(
        "funnel_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("funnel_id", UUID, sa.ForeignKey("funnels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant_id", UUID, sa.ForeignKey("funnel_variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("utm_source", sa.String(200)),
        sa.Column("utm_medium", sa.String(200)),
        sa.Column("utm_campaign", sa.String(200)),
        sa.Column("utm_content", sa.String(200)),
        sa.Column("utm_term", sa.String(200)),
        sa.Column("referrer", sa.String(500)),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("last_step_position", sa.Integer()),
        sa.Column("completed_at", TS),
        sa.Column("registered_at", TS),
        sa.Column("trial_at", TS),
        sa.Column("subscribed_at", TS),
        sa.Column("paid_at", TS),
        sa.Column("first_payment_amount", sa.Numeric(10, 2)),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_funnel_sessions_funnel_created", "funnel_sessions", ["funnel_id", "created_at"])
    op.create_index("ix_funnel_sessions_variant_id", "funnel_sessions", ["variant_id"])
    op.create_index("ix_funnel_sessions_user_id", "funnel_sessions", ["user_id"])

    op.create_table(
        "funnel_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, sa.ForeignKey("funnel_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("funnel_id", UUID, sa.ForeignKey("funnels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant_id", UUID, sa.ForeignKey("funnel_variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("step_position", sa.Integer()),
        sa.Column("occurred_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_funnel_events_funnel_occurred", "funnel_events", ["funnel_id", "occurred_at"])
    op.create_index("ix_funnel_events_session_id", "funnel_events", ["session_id"])
    op.create_index(
        "uq_funnel_events_step_view", "funnel_events", ["session_id", "step_position"],
        unique=True, postgresql_where=sa.text("type = 'step_view'"),
    )


def downgrade() -> None:
    op.drop_table("funnel_events")
    op.drop_table("funnel_sessions")
    op.drop_table("funnel_steps")
    op.drop_table("funnel_variants")
    op.drop_table("funnels")
```

- [ ] **Step 7: Check the migration compiles to SQL**

Run: `alembic upgrade 20260914_0022:20261004_0023 --sql > NUL` (PowerShell: `alembic upgrade 20260914_0022:20261004_0023 --sql | Out-Null`)
Expected: sem erro (modo offline gera o DDL). A validação em Postgres real é a Task 11.

- [ ] **Step 8: Commit**

```bash
git add app/infra/database/models.py alembic/versions/20261004_0023_sales_funnels.py tests/unit/test_funnel_models.py
git commit -m "feat(funnels): modelos e migration das tabelas de funis

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Repositório de funis + fixture de testes compartilhada

**Files:**
- Create: `app/infra/repositories/funnel_repo.py`
- Create: `tests/unit/funnel_fixtures.py` (helpers de teste reutilizados nas Tasks 3–9)
- Test: `tests/unit/test_funnel_repo.py`

**Interfaces:**
- Consumes: modelos da Task 2.
- Produces (`FunnelRepository(db: AsyncSession)`, todos `async`, todos fazem `flush`, nenhum faz `commit`):
  - `create_funnel(**fields) -> FunnelModel`, `get_funnel(funnel_id) -> FunnelModel | None`, `get_funnel_by_slug(slug) -> FunnelModel | None`, `list_funnels() -> list[FunnelModel]` (ordem `created_at desc`), `slug_exists(slug, exclude_id=None) -> bool`, `plan_is_active(plan_id) -> bool`
  - `add_variant(**fields) -> FunnelVariantModel`, `get_variant(variant_id)`, `list_variants(funnel_id) -> list` (ordem `position`), `delete(obj) -> None`
  - `add_step(**fields) -> FunnelStepModel`, `get_step(step_id)`, `list_steps(variant_id) -> list` (ordem `position`), `next_step_position(variant_id) -> int`, `count_steps(variant_id) -> int`
  - `count_sessions(funnel_id) -> int`
  - `create_session(**fields) -> FunnelSessionModel`, `get_session(session_id)`, `latest_session_for_user(user_id) -> FunnelSessionModel | None`
  - `add_event(session: FunnelSessionModel, type: str, step_position: int | None = None) -> bool` (False quando `step_view` duplicado)
- Produces (`tests/unit/funnel_fixtures.py`): fixture `db` (AsyncSession em SQLite em memória com `StaticPool`), fixture `session_factory` (mesmo engine), `async def seed_admin(db) -> UserModel`, `async def seed_plan(db, *, active=True) -> PlanModel`, `async def seed_funnel(db, *, slug="oferta", status="draft", steps=2, plan=None) -> tuple[FunnelModel, FunnelVariantModel]`.

- [ ] **Step 1: Create the shared fixtures**

```python
# tests/unit/funnel_fixtures.py
"""Fixtures compartilhadas pelos testes de funis (importe com `from funnel_fixtures import *`)."""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.database.setup import Base
import infra.database.models  # noqa: F401
from infra.database.models import FunnelModel, FunnelStepModel, FunnelVariantModel, PlanModel, UserModel


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(session_factory):
    async with session_factory() as s:
        yield s


async def seed_admin(db, *, role="admin") -> UserModel:
    user = UserModel(id=uuid.uuid4(), name=role, email=f"{uuid.uuid4()}@t.com", password_hash="h", role=role)
    db.add(user)
    await db.flush()
    return user


async def seed_plan(db, *, active=True) -> PlanModel:
    plan = PlanModel(id=uuid.uuid4(), name="Pro", type="pago", max_markets=1, max_terminals=1,
                     price_monthly=Decimal("99.90"), is_active=active)
    db.add(plan)
    await db.flush()
    return plan


async def seed_funnel(db, *, slug="oferta", status="draft", steps=2, plan=None):
    admin = await seed_admin(db)
    plan = plan or await seed_plan(db)
    funnel = FunnelModel(slug=slug, name="Oferta", status=status, plan_id=plan.id, created_by=admin.id)
    db.add(funnel)
    await db.flush()
    variant = FunnelVariantModel(funnel_id=funnel.id, name="A", weight=100, position=0)
    db.add(variant)
    await db.flush()
    for i in range(steps):
        db.add(FunnelStepModel(variant_id=variant.id, position=i, name=f"Etapa {i + 1}", html=f"<p>{i}</p>"))
    await db.flush()
    return funnel, variant
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_funnel_repo.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from funnel_fixtures import db, engine, seed_admin, seed_funnel, seed_plan, session_factory  # noqa: F401
from infra.repositories.funnel_repo import FunnelRepository


@pytest.mark.asyncio
async def test_lookup_by_slug_and_slug_exists(db):
    funnel, _ = await seed_funnel(db, slug="black")
    repo = FunnelRepository(db)
    assert (await repo.get_funnel_by_slug("black")).id == funnel.id
    assert await repo.slug_exists("black") is True
    assert await repo.slug_exists("black", exclude_id=funnel.id) is False
    assert await repo.get_funnel_by_slug("nada") is None


@pytest.mark.asyncio
async def test_steps_ordered_and_next_position(db):
    _, variant = await seed_funnel(db, steps=3)
    repo = FunnelRepository(db)
    assert [s.position for s in await repo.list_steps(variant.id)] == [0, 1, 2]
    assert await repo.next_step_position(variant.id) == 3
    assert await repo.count_steps(variant.id) == 3


@pytest.mark.asyncio
async def test_plan_is_active(db):
    repo = FunnelRepository(db)
    assert await repo.plan_is_active((await seed_plan(db)).id) is True
    assert await repo.plan_is_active((await seed_plan(db, active=False)).id) is False
    assert await repo.plan_is_active(None) is False
    assert await repo.plan_is_active(uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_add_event_dedupes_step_view(db):
    funnel, variant = await seed_funnel(db)
    repo = FunnelRepository(db)
    fs = await repo.create_session(funnel_id=funnel.id, variant_id=variant.id)
    assert await repo.add_event(fs, "step_view", 0) is True
    assert await repo.add_event(fs, "step_view", 0) is False
    assert await repo.add_event(fs, "step_next", 0) is True
    assert await repo.add_event(fs, "step_next", 0) is True
    assert await repo.count_sessions(funnel.id) == 1


@pytest.mark.asyncio
async def test_latest_session_for_user(db):
    funnel, variant = await seed_funnel(db)
    repo = FunnelRepository(db)
    user = await seed_admin(db, role="owner")
    older = await repo.create_session(funnel_id=funnel.id, variant_id=variant.id, user_id=user.id)
    newer = await repo.create_session(funnel_id=funnel.id, variant_id=variant.id, user_id=user.id)
    older.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db.flush()
    assert (await repo.latest_session_for_user(user.id)).id == newer.id
    assert await repo.latest_session_for_user(uuid.uuid4()) is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_funnel_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'infra.repositories.funnel_repo'`

- [ ] **Step 4: Write the repository**

```python
# app/infra/repositories/funnel_repo.py
"""Repositório SQLAlchemy de funis de venda. Só faz flush; quem chama decide o commit."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.models import (
    FunnelEventModel, FunnelModel, FunnelSessionModel, FunnelStepModel, FunnelVariantModel, PlanModel,
)


class FunnelRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    # -- funis --------------------------------------------------------------
    async def create_funnel(self, **fields) -> FunnelModel:
        funnel = FunnelModel(id=uuid.uuid4(), **fields)
        self._db.add(funnel)
        await self._db.flush()
        return funnel

    async def get_funnel(self, funnel_id: uuid.UUID) -> Optional[FunnelModel]:
        return await self._db.get(FunnelModel, funnel_id)

    async def get_funnel_by_slug(self, slug: str) -> Optional[FunnelModel]:
        res = await self._db.execute(select(FunnelModel).where(FunnelModel.slug == slug))
        return res.scalar_one_or_none()

    async def list_funnels(self) -> list[FunnelModel]:
        res = await self._db.execute(select(FunnelModel).order_by(FunnelModel.created_at.desc()))
        return list(res.scalars())

    async def slug_exists(self, slug: str, exclude_id: Optional[uuid.UUID] = None) -> bool:
        stmt = select(func.count()).select_from(FunnelModel).where(FunnelModel.slug == slug)
        if exclude_id is not None:
            stmt = stmt.where(FunnelModel.id != exclude_id)
        return (await self._db.execute(stmt)).scalar_one() > 0

    async def plan_is_active(self, plan_id: Optional[uuid.UUID]) -> bool:
        if plan_id is None:
            return False
        plan = await self._db.get(PlanModel, plan_id)
        return bool(plan and plan.is_active)

    # -- variantes ----------------------------------------------------------
    async def add_variant(self, **fields) -> FunnelVariantModel:
        variant = FunnelVariantModel(id=uuid.uuid4(), **fields)
        self._db.add(variant)
        await self._db.flush()
        return variant

    async def get_variant(self, variant_id: uuid.UUID) -> Optional[FunnelVariantModel]:
        return await self._db.get(FunnelVariantModel, variant_id)

    async def list_variants(self, funnel_id: uuid.UUID) -> list[FunnelVariantModel]:
        res = await self._db.execute(
            select(FunnelVariantModel)
            .where(FunnelVariantModel.funnel_id == funnel_id)
            .order_by(FunnelVariantModel.position, FunnelVariantModel.created_at)
        )
        return list(res.scalars())

    async def delete(self, obj) -> None:
        await self._db.delete(obj)
        await self._db.flush()

    # -- etapas -------------------------------------------------------------
    async def add_step(self, **fields) -> FunnelStepModel:
        step = FunnelStepModel(id=uuid.uuid4(), **fields)
        self._db.add(step)
        await self._db.flush()
        return step

    async def get_step(self, step_id: uuid.UUID) -> Optional[FunnelStepModel]:
        return await self._db.get(FunnelStepModel, step_id)

    async def list_steps(self, variant_id: uuid.UUID) -> list[FunnelStepModel]:
        res = await self._db.execute(
            select(FunnelStepModel).where(FunnelStepModel.variant_id == variant_id).order_by(FunnelStepModel.position)
        )
        return list(res.scalars())

    async def next_step_position(self, variant_id: uuid.UUID) -> int:
        res = await self._db.execute(
            select(func.max(FunnelStepModel.position)).where(FunnelStepModel.variant_id == variant_id)
        )
        current = res.scalar_one()
        return 0 if current is None else current + 1

    async def count_steps(self, variant_id: uuid.UUID) -> int:
        res = await self._db.execute(
            select(func.count()).select_from(FunnelStepModel).where(FunnelStepModel.variant_id == variant_id)
        )
        return res.scalar_one()

    # -- sessões e eventos --------------------------------------------------
    async def count_sessions(self, funnel_id: uuid.UUID) -> int:
        res = await self._db.execute(
            select(func.count()).select_from(FunnelSessionModel).where(FunnelSessionModel.funnel_id == funnel_id)
        )
        return res.scalar_one()

    async def create_session(self, **fields) -> FunnelSessionModel:
        fs = FunnelSessionModel(id=uuid.uuid4(), **fields)
        self._db.add(fs)
        await self._db.flush()
        return fs

    async def get_session(self, session_id: uuid.UUID) -> Optional[FunnelSessionModel]:
        return await self._db.get(FunnelSessionModel, session_id)

    async def latest_session_for_user(self, user_id: uuid.UUID) -> Optional[FunnelSessionModel]:
        res = await self._db.execute(
            select(FunnelSessionModel)
            .where(FunnelSessionModel.user_id == user_id)
            .order_by(FunnelSessionModel.created_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def add_event(self, session: FunnelSessionModel, type: str, step_position: Optional[int] = None) -> bool:
        event = FunnelEventModel(
            id=uuid.uuid4(), session_id=session.id, funnel_id=session.funnel_id,
            variant_id=session.variant_id, type=type, step_position=step_position,
        )
        try:
            async with self._db.begin_nested():
                self._db.add(event)
            return True
        except IntegrityError:
            return False
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_funnel_repo.py -v`
Expected: PASS (5 testes)

- [ ] **Step 6: Commit**

```bash
git add app/infra/repositories/funnel_repo.py tests/unit/funnel_fixtures.py tests/unit/test_funnel_repo.py
git commit -m "feat(funnels): repositório de funis, sessões e eventos

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Serviço admin (CRUD, duplicação, publicação, avisos)

**Files:**
- Create: `app/application/services/funnel_admin_service.py`
- Test: `tests/unit/test_funnel_admin_service.py`

**Interfaces:**
- Consumes: `FunnelRepository` (Task 3); `validate_slug`, `validate_html_size`, `publish_errors`, `VariantPublishInfo`, `FunnelError`, `FunnelNotFound`, constantes (Task 1).
- Produces: `FunnelAdminService(repo: FunnelRepository)`; constantes `WARN_STEPS = "Este funil já tem tráfego: alterar etapas afeta as métricas por etapa."`, `WARN_WEIGHTS = "Este funil já tem tráfego: alterar variantes enviesa as comparações anteriores."`; métodos `async`:
  - `create_funnel(*, name, slug, plan_id, created_by) -> FunnelModel` (cria variante "A", peso 100, posição 0)
  - `update_funnel(funnel_id, *, name=None, slug=None, plan_id=..., tracking_html=...) -> FunnelModel` (sentinela `UNSET` para campos anuláveis)
  - `duplicate_funnel(funnel_id, *, created_by) -> FunnelModel`
  - `publish(funnel_id) -> FunnelModel` (levanta `FunnelError("funnel.publish_invalid", ...)` com `.errors: list[str]`)
  - `unpublish(funnel_id) -> FunnelModel`, `archive(funnel_id) -> FunnelModel`
  - `add_variant(funnel_id, *, name, weight) -> tuple[FunnelVariantModel, list[str]]`
  - `update_variant(variant_id, *, name=None, weight=None, is_active=None) -> tuple[FunnelVariantModel, list[str]]`
  - `delete_variant(variant_id) -> list[str]` (bloqueia a única variante: `funnel.last_variant`)
  - `duplicate_variant(variant_id) -> tuple[FunnelVariantModel, list[str]]`
  - `add_step(variant_id, *, name, html) -> tuple[FunnelStepModel, list[str]]`
  - `update_step(step_id, *, name=None, html=None) -> tuple[FunnelStepModel, list[str]]`
  - `delete_step(step_id) -> list[str]` (renumera as seguintes)
  - `reorder_steps(variant_id, ordered_ids: list[uuid.UUID]) -> list[str]` (`funnel.invalid_order` se o conjunto não bater)
  - `get_detail(funnel_id) -> dict` — `{"funnel": FunnelModel, "variants": [{"variant": FunnelVariantModel, "steps": [FunnelStepModel]}], "session_count": int}`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_funnel_admin_service.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_funnel_admin_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'application.services.funnel_admin_service'`

- [ ] **Step 3: Write the service**

```python
# app/application/services/funnel_admin_service.py
"""Casos de uso do admin para funis de venda."""
from __future__ import annotations

import uuid
from typing import Any, Optional

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
```

> Nota: o service usa `self._repo._db.flush()` para mutações simples de atributo — o repositório expõe a sessão por esse atributo e não vale criar um método só para `flush`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_funnel_admin_service.py -v`
Expected: PASS (11 testes)

- [ ] **Step 5: Commit**

```bash
git add app/application/services/funnel_admin_service.py tests/unit/test_funnel_admin_service.py
git commit -m "feat(funnels): serviço admin com publicação, duplicação e avisos

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Serviço público (sessão e eventos)

**Files:**
- Create: `app/application/services/funnel_public_service.py`
- Test: `tests/unit/test_funnel_public_service.py`

**Interfaces:**
- Consumes: `FunnelRepository` (Task 3); `pick_variant`, `VariantWeight`, `PUBLIC_EVENT_TYPES`, `FunnelError`, `FunnelNotFound` (Task 1).
- Produces: `FunnelPublicService(repo, rng: random.Random | None = None)`; `class FunnelGone(FunnelError)` (code `funnel.gone`); métodos `async`:
  - `start_session(slug: str, *, fsid: uuid.UUID | None, utm: dict[str, str | None], referrer: str | None) -> dict` — retorna `{"fsid": UUID, "funnel": {"name", "plan_id"}, "variant": {"id"}, "tracking_html", "steps": [{"position","name","html"}], "resume_position": int}`
  - `record_event(fsid: uuid.UUID, type: str, step_position: int | None) -> None`
  - `UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_funnel_public_service.py
from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import select

from funnel_fixtures import db, engine, seed_funnel, session_factory  # noqa: F401
from application.services.funnel_public_service import FunnelGone, FunnelPublicService
from domain.funnels import FunnelError, FunnelNotFound
from infra.database.models import FunnelEventModel
from infra.repositories.funnel_repo import FunnelRepository


def _svc(db):
    repo = FunnelRepository(db)
    return FunnelPublicService(repo, rng=random.Random(1)), repo


@pytest.mark.asyncio
async def test_new_session_captures_utms_and_returns_steps(db):
    svc, repo = _svc(db)
    funnel, variant = await seed_funnel(db, status="published", steps=2)
    data = await svc.start_session(
        "oferta", fsid=None, utm={"utm_source": "meta", "utm_campaign": "bf", "utm_medium": None},
        referrer="https://instagram.com",
    )
    fs = await repo.get_session(data["fsid"])
    assert fs.utm_source == "meta" and fs.utm_campaign == "bf" and fs.referrer == "https://instagram.com"
    assert data["variant"]["id"] == variant.id
    assert [s["position"] for s in data["steps"]] == [0, 1]
    assert data["resume_position"] == 0
    assert data["funnel"]["plan_id"] == funnel.plan_id


@pytest.mark.asyncio
async def test_resume_keeps_session_and_variant(db):
    svc, repo = _svc(db)
    await seed_funnel(db, status="published", steps=3)
    first = await svc.start_session("oferta", fsid=None, utm={}, referrer=None)
    await svc.record_event(first["fsid"], "step_view", 2)
    again = await svc.start_session("oferta", fsid=first["fsid"], utm={"utm_source": "outra"}, referrer=None)
    assert again["fsid"] == first["fsid"]
    assert again["resume_position"] == 2
    assert (await repo.get_session(first["fsid"])).utm_source is None  # UTM só na criação


@pytest.mark.asyncio
async def test_unknown_fsid_creates_new_session(db):
    svc, _ = _svc(db)
    await seed_funnel(db, status="published")
    bogus = uuid.uuid4()
    data = await svc.start_session("oferta", fsid=bogus, utm={}, referrer=None)
    assert data["fsid"] != bogus


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "archived"])
async def test_unpublished_funnel_is_not_found(db, status):
    svc, _ = _svc(db)
    await seed_funnel(db, status=status)
    with pytest.raises(FunnelNotFound):
        await svc.start_session("oferta", fsid=None, utm={}, referrer=None)


@pytest.mark.asyncio
async def test_no_eligible_variant_is_not_found(db):
    svc, _ = _svc(db)
    _, variant = await seed_funnel(db, status="published")
    variant.weight = 0
    await db.flush()
    with pytest.raises(FunnelNotFound):
        await svc.start_session("oferta", fsid=None, utm={}, referrer=None)


@pytest.mark.asyncio
async def test_record_event_rules(db):
    svc, repo = _svc(db)
    funnel, _ = await seed_funnel(db, status="published")
    data = await svc.start_session("oferta", fsid=None, utm={}, referrer=None)
    await svc.record_event(data["fsid"], "step_view", 1)
    await svc.record_event(data["fsid"], "step_view", 1)  # dedupe
    await svc.record_event(data["fsid"], "funnel_completed", None)
    rows = (await db.execute(select(FunnelEventModel.type))).scalars().all()
    assert sorted(rows) == ["funnel_completed", "step_view"]
    fs = await repo.get_session(data["fsid"])
    assert fs.last_step_position == 1 and fs.completed_at is not None

    with pytest.raises(FunnelError) as exc:
        await svc.record_event(data["fsid"], "invoice_paid", None)
    assert exc.value.code == "funnel.invalid_event"
    with pytest.raises(FunnelNotFound):
        await svc.record_event(uuid.uuid4(), "step_view", 0)

    funnel.status = "draft"
    await db.flush()
    with pytest.raises(FunnelGone):
        await svc.record_event(data["fsid"], "step_view", 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_funnel_public_service.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the service**

```python
# app/application/services/funnel_public_service.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_funnel_public_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/application/services/funnel_public_service.py tests/unit/test_funnel_public_service.py
git commit -m "feat(funnels): serviço público de sessão e eventos

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Atribuição (serviço + decorator de analytics + flag)

**Files:**
- Create: `app/application/services/funnel_attribution_service.py`
- Create: `app/infra/observability/funnel_analytics.py`
- Modify: `app/infra/config/settings.py` (junto de `ANALYTICS_ENABLED`, linha ~109)
- Modify: `tests/conftest.py` (bloco de `os.environ.setdefault`)
- Test: `tests/unit/test_funnel_attribution.py`

**Interfaces:**
- Consumes: `FunnelRepository` (Task 3), `MILESTONE_FIELDS` (Task 1), `PostHogClient` (existente).
- Produces:
  - `FunnelAttributionService(session_factory)` — `async link_registration(fsid: uuid.UUID, user_id: uuid.UUID) -> bool`; `async claim(fsid, user_id) -> bool`; `async mark(user_id: uuid.UUID, event: str, amount: Decimal | None = None) -> bool`. Cada chamada abre a própria sessão, faz `commit` e **nunca** levanta exceção.
  - `FunnelTrackingAnalytics(inner, attribution)` com `async track_event(distinct_id: str, event: str, properties: dict | None = None) -> None`
  - `build_analytics() -> PostHogClient | FunnelTrackingAnalytics`
  - Setting `FUNNEL_ATTRIBUTION_ENABLED: bool = True`

- [ ] **Step 1: Add the setting and the test default**

Em `app/infra/config/settings.py`, logo abaixo de `POSTHOG_HOST: str = "https://us.i.posthog.com"`:

```python
    # Funis de venda: marca marcos (trial/assinatura/pagamento) na sessão de funil do usuário
    FUNNEL_ATTRIBUTION_ENABLED: bool = True
```

Em `tests/conftest.py`, junto dos outros `setdefault`:

```python
os.environ.setdefault("FUNNEL_ATTRIBUTION_ENABLED", "false")
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_funnel_attribution.py
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from funnel_fixtures import db, engine, seed_admin, seed_funnel, session_factory  # noqa: F401
from application.services.funnel_attribution_service import FunnelAttributionService
from infra.database.models import FunnelEventModel, FunnelSessionModel
from infra.observability.funnel_analytics import FunnelTrackingAnalytics


async def _session(db, *, user_id=None):
    funnel, variant = await seed_funnel(db, status="published")
    fs = FunnelSessionModel(funnel_id=funnel.id, variant_id=variant.id, user_id=user_id)
    db.add(fs)
    await db.commit()
    return fs


async def _reload(session_factory, fs_id):
    async with session_factory() as s:
        return await s.get(FunnelSessionModel, fs_id)


@pytest.mark.asyncio
async def test_link_registration_sets_user_and_event(db, session_factory):
    fs = await _session(db)
    user = await seed_admin(db, role="owner")
    await db.commit()
    svc = FunnelAttributionService(session_factory)
    assert await svc.link_registration(fs.id, user.id) is True
    stored = await _reload(session_factory, fs.id)
    assert stored.user_id == user.id and stored.registered_at is not None
    async with session_factory() as s:
        types = (await s.execute(select(FunnelEventModel.type))).scalars().all()
    assert types == ["registered"]


@pytest.mark.asyncio
async def test_link_registration_refuses_already_linked_or_unknown(db, session_factory):
    owner = await seed_admin(db, role="owner")
    other = await seed_admin(db, role="owner")
    fs = await _session(db, user_id=owner.id)
    svc = FunnelAttributionService(session_factory)
    assert await svc.link_registration(fs.id, other.id) is False
    assert await svc.link_registration(uuid.uuid4(), other.id) is False
    assert (await _reload(session_factory, fs.id)).user_id == owner.id


@pytest.mark.asyncio
async def test_claim_links_without_registered_milestone(db, session_factory):
    fs = await _session(db)
    user = await seed_admin(db, role="owner")
    await db.commit()
    svc = FunnelAttributionService(session_factory)
    assert await svc.claim(fs.id, user.id) is True
    stored = await _reload(session_factory, fs.id)
    assert stored.user_id == user.id and stored.registered_at is None


@pytest.mark.asyncio
async def test_mark_is_idempotent_and_stores_amount(db, session_factory):
    user = await seed_admin(db, role="owner")
    fs = await _session(db, user_id=user.id)
    svc = FunnelAttributionService(session_factory)
    assert await svc.mark(user.id, "invoice_paid", Decimal("99.90")) is True
    first = (await _reload(session_factory, fs.id)).paid_at
    assert await svc.mark(user.id, "invoice_paid", Decimal("10.00")) is False
    stored = await _reload(session_factory, fs.id)
    assert stored.paid_at == first and stored.first_payment_amount == Decimal("99.90")


@pytest.mark.asyncio
async def test_mark_without_session_or_unknown_event_is_noop(db, session_factory):
    user = await seed_admin(db, role="owner")
    await db.commit()
    svc = FunnelAttributionService(session_factory)
    assert await svc.mark(user.id, "trial_activated") is False
    assert await svc.mark(user.id, "qualquer_coisa") is False


@pytest.mark.asyncio
async def test_mark_swallows_errors():
    def broken_factory():
        raise RuntimeError("db fora")
    svc = FunnelAttributionService(broken_factory)
    assert await svc.mark(uuid.uuid4(), "trial_activated") is False


class _Inner:
    def __init__(self):
        self.calls = []

    async def track_event(self, distinct_id, event, properties=None):
        self.calls.append((distinct_id, event, properties))


class _Attribution:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def mark(self, user_id, event, amount=None):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((user_id, event, amount))
        return True


@pytest.mark.asyncio
async def test_tracking_analytics_forwards_and_marks_milestones():
    inner, attribution = _Inner(), _Attribution()
    analytics = FunnelTrackingAnalytics(inner, attribution)
    uid = uuid.uuid4()
    await analytics.track_event(str(uid), "invoice_paid", {"amount": "49.90"})
    await analytics.track_event(str(uid), "fiscal_credits_purchased", {"quantity": 10})
    assert [c[1] for c in inner.calls] == ["invoice_paid", "fiscal_credits_purchased"]
    assert attribution.calls == [(uid, "invoice_paid", Decimal("49.90"))]


@pytest.mark.asyncio
async def test_tracking_analytics_never_raises():
    analytics = FunnelTrackingAnalytics(_Inner(), _Attribution(fail=True))
    await analytics.track_event(str(uuid.uuid4()), "trial_activated", {})
    await analytics.track_event("nao-e-uuid", "trial_activated", {})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_funnel_attribution.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Write the attribution service**

```python
# app/application/services/funnel_attribution_service.py
"""Liga sessões de funil a usuários e marca os marcos de conversão.

Usa sessão de banco própria (não participa da transação do chamador) e nunca
levanta exceção: atribuição de funil jamais pode derrubar cadastro ou billing.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from domain.funnels import MILESTONE_FIELDS
from infra.config.logger import get_logger
from infra.repositories.funnel_repo import FunnelRepository

logger = get_logger("funnel_attribution")


class FunnelAttributionService:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    async def link_registration(self, fsid: uuid.UUID, user_id: uuid.UUID) -> bool:
        return await self._link(fsid, user_id, milestone="registered")

    async def claim(self, fsid: uuid.UUID, user_id: uuid.UUID) -> bool:
        return await self._link(fsid, user_id, milestone=None)

    async def _link(self, fsid, user_id, *, milestone: Optional[str]) -> bool:
        try:
            async with self._session_factory() as db:
                repo = FunnelRepository(db)
                session = await repo.get_session(fsid)
                if session is None or session.user_id is not None:
                    logger.info("funnel_link_skipped", extra={"extra_data": {"fsid": str(fsid)}})
                    return False
                session.user_id = user_id
                if milestone:
                    setattr(session, MILESTONE_FIELDS[milestone], datetime.now(timezone.utc))
                    await repo.add_event(session, milestone)
                await db.commit()
                return True
        except Exception:
            logger.exception("funnel_link_failed", extra={"extra_data": {"fsid": str(fsid)}})
            return False

    async def mark(self, user_id: uuid.UUID, event: str, amount: Optional[Decimal] = None) -> bool:
        field = MILESTONE_FIELDS.get(event)
        if field is None:
            return False
        try:
            async with self._session_factory() as db:
                repo = FunnelRepository(db)
                session = await repo.latest_session_for_user(user_id)
                if session is None or getattr(session, field) is not None:
                    return False
                setattr(session, field, datetime.now(timezone.utc))
                if event == "invoice_paid" and amount is not None:
                    session.first_payment_amount = amount
                await repo.add_event(session, event)
                await db.commit()
                return True
        except Exception:
            logger.exception("funnel_mark_failed", extra={"extra_data": {"event": event}})
            return False
```

- [ ] **Step 5: Write the analytics decorator**

```python
# app/infra/observability/funnel_analytics.py
"""Cliente de analytics que, além do PostHog, marca marcos de funil de venda."""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
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
        except (ValueError, InvalidOperation, Exception):
            logger.exception("funnel_tracking_failed", extra={"extra_data": {"event": event}})


def build_analytics():
    """Default dos services de billing: PostHog + marcação de funil (se habilitada)."""
    if not get_settings().FUNNEL_ATTRIBUTION_ENABLED:
        return PostHogClient()
    from application.services.funnel_attribution_service import FunnelAttributionService
    from infra.database.setup import async_session_factory

    return FunnelTrackingAnalytics(PostHogClient(), FunnelAttributionService(async_session_factory))
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_funnel_attribution.py -v`
Expected: PASS (8 testes)

- [ ] **Step 7: Commit**

```bash
git add app/application/services/funnel_attribution_service.py app/infra/observability/funnel_analytics.py app/infra/config/settings.py tests/conftest.py tests/unit/test_funnel_attribution.py
git commit -m "feat(funnels): atribuição de marcos e decorator de analytics

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Plugar a atribuição no billing e no cadastro

**Files:**
- Modify: `app/application/services/subscription_service.py` (construtor ~linha 41; `process_recurring_event` ~linha 310–380)
- Modify: `app/application/services/invoice_service.py` (construtor ~linha 28)
- Modify: `app/application/services/recurring_service.py` (construtor ~linha 33)
- Modify: `app/application/dtos.py` (`UserCreateDTO`, linha 20)
- Modify: `app/infra/web/dependencies.py` (nova dependência)
- Modify: `app/infra/web/routers/identity.py` (`register_user`, linhas 19–27)
- Test: `tests/unit/test_funnel_wiring.py`

**Interfaces:**
- Consumes: `build_analytics` (Task 6), `FunnelAttributionService` (Task 6).
- Produces: `get_funnel_attribution_service()` em `infra/web/dependencies.py`; `UserCreateDTO.funnel_session_id: Optional[uuid.UUID]`; evento `invoice_paid` com `{"amount", "subscription_type", "billing_mode": "recurring"}` em `PAYMENT_RECEIVED`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_funnel_wiring.py
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.dtos import UserCreateDTO, UserResponseDTO
from application.services.subscription_service import SubscriptionService
from infra.web.routers import identity as identity_router


class _Attribution:
    def __init__(self):
        self.links = []

    async def link_registration(self, fsid, user_id):
        self.links.append((fsid, user_id))
        return True


class _Identity:
    def __init__(self, user_id):
        self.user_id = user_id

    async def register_user(self, dto):
        return UserResponseDTO(id=self.user_id, name=dto.name, email=dto.email, role="owner")


def _client(attribution, user_id):
    app = FastAPI()
    app.include_router(identity_router.router, prefix="/api/v1/identity")
    app.dependency_overrides[identity_router.get_identity_service] = lambda: _Identity(user_id)
    app.dependency_overrides[identity_router.get_funnel_attribution_service] = lambda: attribution
    return TestClient(app)


def test_user_create_dto_accepts_optional_funnel_session_id():
    fsid = uuid.uuid4()
    dto = UserCreateDTO(name="A", email="a@t.com", password="123456", funnel_session_id=fsid)
    assert dto.funnel_session_id == fsid
    assert UserCreateDTO(name="A", email="a@t.com", password="123456").funnel_session_id is None


def test_register_links_funnel_session():
    attribution, user_id, fsid = _Attribution(), uuid.uuid4(), uuid.uuid4()
    resp = _client(attribution, user_id).post("/api/v1/identity/register", json={
        "name": "A", "email": "a@t.com", "password": "123456", "funnel_session_id": str(fsid),
    })
    assert resp.status_code == 201
    assert attribution.links == [(fsid, user_id)]


def test_register_without_fsid_does_not_link():
    attribution = _Attribution()
    resp = _client(attribution, uuid.uuid4()).post("/api/v1/identity/register", json={
        "name": "A", "email": "a@t.com", "password": "123456",
    })
    assert resp.status_code == 201
    assert attribution.links == []


class _Analytics:
    def __init__(self):
        self.calls = []

    async def track_event(self, distinct_id, event, properties=None):
        self.calls.append((distinct_id, event, properties))


class _EventRepo:
    async def get_by_event_id(self, event_id):
        return None

    async def save(self, model):
        return model


class _SubRepo:
    def __init__(self, sub):
        self.sub = sub

    async def get_by_billing_subscription_id(self, _):
        return self.sub

    async def save(self, sub):
        return sub


class _UserRepo:
    async def get_by_id(self, _):
        return None

    async def save(self, user):
        return user


@pytest.mark.asyncio
async def test_recurring_payment_received_emits_invoice_paid():
    owner_id = uuid.uuid4()
    sub = SimpleNamespace(id=uuid.uuid4(), owner_id=owner_id, plan_id=None, status="pending",
                          value=Decimal("89.90"), subscription_type="monthly",
                          last_event_at=None, expires_at=None)
    analytics = _Analytics()
    svc = SubscriptionService(user_repo=_UserRepo(), plan_repo=None, subscription_repo=_SubRepo(sub),
                              event_repo=_EventRepo(), analytics=analytics)
    await svc.process_recurring_event("PAYMENT_RECEIVED", "bc-sub-1", None, datetime(2026, 10, 4), {})
    assert analytics.calls == [(str(owner_id), "invoice_paid",
                                {"amount": "89.90", "subscription_type": "monthly", "billing_mode": "recurring"})]


@pytest.mark.asyncio
async def test_recurring_other_events_do_not_emit_invoice_paid():
    sub = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4(), plan_id=None, status="active",
                          value=Decimal("1"), subscription_type="monthly", last_event_at=None, expires_at=None)
    analytics = _Analytics()
    svc = SubscriptionService(user_repo=_UserRepo(), plan_repo=None, subscription_repo=_SubRepo(sub),
                              event_repo=_EventRepo(), analytics=analytics)
    await svc.process_recurring_event("SUBSCRIPTION_INACTIVATED", "bc-sub-1", None, None, {})
    assert analytics.calls == []


def test_services_default_to_build_analytics(monkeypatch):
    import application.services.invoice_service as inv
    import application.services.recurring_service as rec
    import application.services.subscription_service as sub
    sentinel = object()
    for module in (inv, rec, sub):
        monkeypatch.setattr(module, "build_analytics", lambda: sentinel)
    assert sub.SubscriptionService(user_repo=None, plan_repo=None)._analytics is sentinel
    assert inv.InvoiceService(None, None, None, None, None)._analytics is sentinel
    assert rec.RecurringService(None, None, None, None, None)._analytics is sentinel
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_funnel_wiring.py -v`
Expected: FAIL — `funnel_session_id` desconhecido / `get_funnel_attribution_service` inexistente / `build_analytics` ausente nos módulos.

- [ ] **Step 3: Switch the analytics default in the three services**

Em cada um de `subscription_service.py`, `invoice_service.py` e `recurring_service.py`, troque o import e o default:

```python
# antes
from infra.observability.analytics import PostHogClient
...
        self._analytics = analytics or PostHogClient()
```
```python
# depois
from infra.observability.funnel_analytics import build_analytics
...
        self._analytics = analytics or build_analytics()
```

(Se `PostHogClient` for usado em outro ponto do arquivo, mantenha o import; confira com `grep -n PostHogClient <arquivo>`.)

- [ ] **Step 4: Emit `invoice_paid` for recurring payments**

Em `SubscriptionService.process_recurring_event`, dentro do `try`, logo após o bloco `if local_sub is not None and new_status is not None: ... await self.user_repo.save(user)` e antes de `event_model.processing_status = "processed"`:

```python
            if event == "PAYMENT_RECEIVED" and local_sub is not None:
                await self._analytics.track_event(
                    str(local_sub.owner_id), "invoice_paid",
                    {
                        "amount": str(local_sub.value),
                        "subscription_type": local_sub.subscription_type,
                        "billing_mode": "recurring",
                    },
                )
```

- [ ] **Step 5: Add `funnel_session_id` to the DTO**

Em `app/application/dtos.py`, em `UserCreateDTO` (logo após `cpf: Optional[str] = None`); garanta `import uuid` no topo do arquivo:

```python
    # Sessão de funil de venda que trouxe o cadastro (opcional; ver spec de funis)
    funnel_session_id: Optional[uuid.UUID] = None
```

- [ ] **Step 6: Add the dependency**

Em `app/infra/web/dependencies.py`, ao lado de `get_audit_service`:

```python
def get_funnel_attribution_service():
    from application.services.funnel_attribution_service import FunnelAttributionService
    from infra.database.setup import async_session_factory
    return FunnelAttributionService(async_session_factory)
```

- [ ] **Step 7: Link the registration in the router**

Em `app/infra/web/routers/identity.py`, adicione `get_funnel_attribution_service` ao import de `infra.web.dependencies` e substitua `register_user`:

```python
@router.post("/register", response_model=UserResponseDTO, status_code=status.HTTP_201_CREATED)
async def register_user(
    dto: UserCreateDTO,
    service: IdentityService = Depends(get_identity_service),
    attribution=Depends(get_funnel_attribution_service),
):
    try:
        user = await service.register_user(dto)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if dto.funnel_session_id is not None:
        await attribution.link_registration(dto.funnel_session_id, user.id)
    return user
```

- [ ] **Step 8: Run the new and the affected existing tests**

Run: `python -m pytest tests/unit/test_funnel_wiring.py tests/unit/test_invoice_service.py tests/unit/test_identity_dtos.py tests/unit/test_internal_webhook_recurring.py tests/unit/test_phase4_billing.py -v`
Expected: PASS em todos (os existentes não mudam de comportamento: a flag está desligada nos testes).

- [ ] **Step 9: Commit**

```bash
git add app/application/services/subscription_service.py app/application/services/invoice_service.py app/application/services/recurring_service.py app/application/dtos.py app/infra/web/dependencies.py app/infra/web/routers/identity.py tests/unit/test_funnel_wiring.py
git commit -m "feat(funnels): atribui cadastro, trial, assinatura e pagamento à sessão de funil

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Métricas (agregação pura + repositório)

**Files:**
- Create: `app/application/services/funnel_metrics.py`
- Create: `app/infra/repositories/funnel_metrics_repo.py`
- Test: `tests/unit/test_funnel_metrics.py`

**Interfaces:**
- Consumes: modelos (Task 2), `ab_confidence` (Task 1).
- Produces:
  - `@dataclass SessionRow`: `id, variant_id, utm_source, utm_campaign, created_at, completed_at, registered_at, trial_at, subscribed_at, paid_at, first_payment_amount`
  - `aggregate_metrics(*, rows: list[SessionRow], step_views: dict[int, int], variants: list[tuple[uuid.UUID, str, int]], step_names: dict[int, str]) -> dict` com chaves `summary`, `steps`, `variants`, `sources`, `timeseries`
  - `FunnelMetricsRepository(db)`: `async cohort_rows(funnel_id, start, end, *, variant_id=None, utm_source=None, utm_campaign=None) -> list[SessionRow]`; `async step_view_counts(session_ids: list[uuid.UUID]) -> dict[int, int]`; `async list_kpis(since: datetime) -> dict[uuid.UUID, tuple[int, int]]` (sessões, pagos)

Formato de `aggregate_metrics` (o frontend da Task 15 depende dele):
```text
summary:    {sessions, completed, registered, trials, subscribed, paid, paid_conversion: float, revenue: str}
steps:      [{key: "step:0"|"completed"|"registered"|"trial"|"subscribed"|"paid", label, count, pct_of_total: float, drop_from_previous: float|null}]
variants:   [{variant_id, name, weight, sessions, registered, paid, conversion: float, revenue_per_session: str, confidence: float|null, low_sample: bool, is_control: bool}]
sources:    [{utm_source: str|null, utm_campaign: str|null, sessions, registered, paid, conversion: float}]  (ordenado por sessions desc)
timeseries: [{date: "YYYY-MM-DD", sessions, paid}]  (sessions por created_at, paid por paid_at)
```
Percentuais são frações 0–1 arredondadas a 4 casas; dinheiro como string com 2 casas.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_funnel_metrics.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from funnel_fixtures import db, engine, seed_admin, seed_funnel, session_factory  # noqa: F401
from application.services.funnel_metrics import SessionRow, aggregate_metrics
from infra.repositories.funnel_metrics_repo import FunnelMetricsRepository
from infra.repositories.funnel_repo import FunnelRepository

T0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
VA, VB = uuid.uuid4(), uuid.uuid4()


def _row(variant=VA, *, source=None, paid=False, registered=False, completed=False, day=0, amount="99.90"):
    created = T0 + timedelta(days=day)
    return SessionRow(
        id=uuid.uuid4(), variant_id=variant, utm_source=source, utm_campaign=None, created_at=created,
        completed_at=created if completed or registered or paid else None,
        registered_at=created if registered or paid else None,
        trial_at=created if registered or paid else None,
        subscribed_at=created if paid else None,
        paid_at=created + timedelta(days=3) if paid else None,
        first_payment_amount=Decimal(amount) if paid else None,
    )


def test_aggregate_summary_steps_and_drop():
    rows = [_row(paid=True), _row(registered=True), _row(completed=True), _row()]
    out = aggregate_metrics(rows=rows, step_views={0: 4, 1: 3}, variants=[(VA, "A", 100)],
                            step_names={0: "Intro", 1: "Oferta"})
    assert out["summary"] == {"sessions": 4, "completed": 3, "registered": 2, "trials": 2, "subscribed": 1,
                              "paid": 1, "paid_conversion": 0.25, "revenue": "99.90"}
    keys = [s["key"] for s in out["steps"]]
    assert keys == ["step:0", "step:1", "completed", "registered", "trial", "subscribed", "paid"]
    step1 = out["steps"][1]
    assert step1["label"] == "Oferta" and step1["count"] == 3
    assert step1["pct_of_total"] == 0.75 and step1["drop_from_previous"] == 0.25
    assert out["steps"][0]["drop_from_previous"] is None


def test_aggregate_variants_control_and_low_sample():
    rows = [_row(VA) for _ in range(150)] + [_row(VA, paid=True) for _ in range(5)]
    rows += [_row(VB) for _ in range(140)] + [_row(VB, paid=True) for _ in range(15)]
    out = aggregate_metrics(rows=rows, step_views={}, variants=[(VA, "A", 50), (VB, "B", 50)], step_names={})
    a, b = out["variants"]
    assert a["is_control"] is True and a["confidence"] is None and a["low_sample"] is False
    assert b["is_control"] is False and b["low_sample"] is False and b["confidence"] > 0.9
    assert b["paid"] == 15 and b["revenue_per_session"] == "9.69"


def test_aggregate_sources_and_timeseries():
    rows = [_row(source="meta", paid=True), _row(source="meta"), _row(day=1)]
    out = aggregate_metrics(rows=rows, step_views={}, variants=[(VA, "A", 100)], step_names={})
    assert out["sources"][0] == {"utm_source": "meta", "utm_campaign": None, "sessions": 2,
                                 "registered": 1, "paid": 1, "conversion": 0.5}
    assert out["sources"][1]["utm_source"] is None
    series = {p["date"]: p for p in out["timeseries"]}
    assert series["2026-10-01"]["sessions"] == 2
    assert series["2026-10-04"]["paid"] == 1


def test_aggregate_empty():
    out = aggregate_metrics(rows=[], step_views={}, variants=[(VA, "A", 100)], step_names={0: "x"})
    assert out["summary"]["sessions"] == 0 and out["summary"]["paid_conversion"] == 0.0


@pytest.mark.asyncio
async def test_repo_cohort_excludes_admin_and_out_of_range(db):
    funnel, variant = await seed_funnel(db, status="published")
    admin = await seed_admin(db)
    owner = await seed_admin(db, role="owner")
    repo, metrics = FunnelRepository(db), FunnelMetricsRepository(db)
    inside = await repo.create_session(funnel_id=funnel.id, variant_id=variant.id, user_id=owner.id, utm_source="meta")
    await repo.create_session(funnel_id=funnel.id, variant_id=variant.id, user_id=admin.id)
    old = await repo.create_session(funnel_id=funnel.id, variant_id=variant.id)
    old.created_at = datetime.now(timezone.utc) - timedelta(days=90)
    await repo.add_event(inside, "step_view", 0)
    await repo.add_event(inside, "step_view", 1)
    await db.flush()

    start = datetime.now(timezone.utc) - timedelta(days=30)
    end = datetime.now(timezone.utc) + timedelta(minutes=1)
    rows = await metrics.cohort_rows(funnel.id, start, end)
    assert [r.id for r in rows] == [inside.id]
    assert await metrics.step_view_counts([r.id for r in rows]) == {0: 1, 1: 1}
    assert await metrics.cohort_rows(funnel.id, start, end, utm_source="google") == []
    kpis = await metrics.list_kpis(start)
    assert kpis[funnel.id] == (1, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_funnel_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the pure aggregation**

```python
# app/application/services/funnel_metrics.py
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
```

> Nota: o controle (índice 0) usa `low_sample = n < 100` só para exibição; a confiança das demais vem de `ab_confidence`, que também considera as conversões somadas.

- [ ] **Step 4: Write the metrics repository**

```python
# app/infra/repositories/funnel_metrics_repo.py
"""Consultas de métricas de funil: coorte de sessões, contagem por etapa e KPIs da lista."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.services.funnel_metrics import SessionRow
from infra.database.models import FunnelEventModel, FunnelSessionModel, UserModel

_S = FunnelSessionModel


def _not_admin():
    return or_(_S.user_id.is_(None), UserModel.role != "admin")


class FunnelMetricsRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def cohort_rows(self, funnel_id: uuid.UUID, start: datetime, end: datetime, *,
                          variant_id: Optional[uuid.UUID] = None, utm_source: Optional[str] = None,
                          utm_campaign: Optional[str] = None) -> list[SessionRow]:
        stmt = (
            select(_S.id, _S.variant_id, _S.utm_source, _S.utm_campaign, _S.created_at, _S.completed_at,
                   _S.registered_at, _S.trial_at, _S.subscribed_at, _S.paid_at, _S.first_payment_amount)
            .outerjoin(UserModel, UserModel.id == _S.user_id)
            .where(_S.funnel_id == funnel_id, _S.created_at >= start, _S.created_at < end, _not_admin())
            .order_by(_S.created_at)
        )
        if variant_id is not None:
            stmt = stmt.where(_S.variant_id == variant_id)
        if utm_source is not None:
            stmt = stmt.where(_S.utm_source == utm_source)
        if utm_campaign is not None:
            stmt = stmt.where(_S.utm_campaign == utm_campaign)
        return [SessionRow(*row) for row in (await self._db.execute(stmt)).all()]

    async def step_view_counts(self, session_ids: list[uuid.UUID]) -> dict[int, int]:
        if not session_ids:
            return {}
        counts: dict[int, int] = {}
        for i in range(0, len(session_ids), 500):  # evita IN gigante
            chunk = session_ids[i:i + 500]
            stmt = (
                select(FunnelEventModel.step_position, func.count(func.distinct(FunnelEventModel.session_id)))
                .where(FunnelEventModel.type == "step_view", FunnelEventModel.session_id.in_(chunk))
                .group_by(FunnelEventModel.step_position)
            )
            for position, value in (await self._db.execute(stmt)).all():
                counts[position] = counts.get(position, 0) + value
        return counts

    async def list_kpis(self, since: datetime) -> dict[uuid.UUID, tuple[int, int]]:
        stmt = (
            select(_S.funnel_id, func.count(_S.id), func.sum(case((_S.paid_at.isnot(None), 1), else_=0)))
            .outerjoin(UserModel, UserModel.id == _S.user_id)
            .where(and_(_S.created_at >= since, _not_admin()))
            .group_by(_S.funnel_id)
        )
        return {fid: (int(n), int(p or 0)) for fid, n, p in (await self._db.execute(stmt)).all()}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_funnel_metrics.py -v`
Expected: PASS (5 testes)

- [ ] **Step 6: Commit**

```bash
git add app/application/services/funnel_metrics.py app/infra/repositories/funnel_metrics_repo.py tests/unit/test_funnel_metrics.py
git commit -m "feat(funnels): métricas por coorte, etapas, A/B e origem

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Routers público e admin + registro no app

**Files:**
- Create: `app/application/dtos_funnels.py`
- Create: `app/infra/web/routers/funnels_public.py`
- Create: `app/infra/web/routers/funnels_admin.py`
- Modify: `app/infra/web/main.py` (import em `from infra.web.routers import (...)` ~linha 25 e `include_router` ~linha 357)
- Test: `tests/unit/test_funnel_routes.py`

**Interfaces:**
- Consumes: Tasks 3–8; `require_admin`, `get_current_user`, `get_audit_service`, `get_funnel_attribution_service` (dependencies); `enforce_rate_limit_async`; `record_audit_event`.
- Produces (HTTP; o frontend das Tasks 12–16 depende destes contratos):

| Método e caminho (`/api/v1`) | Corpo / query | Resposta |
|---|---|---|
| `POST /funnels/public/{slug}/session` | `{fsid?, utm_source?, utm_medium?, utm_campaign?, utm_content?, utm_term?, referrer?}` | `{fsid, funnel:{name, plan_id}, variant:{id}, tracking_html, steps:[{position,name,html}], resume_position}`; 404 |
| `POST /funnels/public/events` | `{fsid, type, step_position?}` | 204; 404 sessão; 410 despublicado; 422 tipo |
| `POST /funnels/sessions/{fsid}/claim` (auth) | — | `{linked: bool}` |
| `GET /admin/funnels` | — | `[{id, slug, name, status, plan_id, variant_count, sessions_30d, paid_30d, paid_conversion_30d}]` |
| `POST /admin/funnels` | `{name, slug, plan_id?}` | detalhe (201) |
| `GET /admin/funnels/{id}` | — | detalhe |
| `PATCH /admin/funnels/{id}` | `{name?, slug?, plan_id?, tracking_html?}` | detalhe |
| `POST /admin/funnels/{id}/duplicate` \| `/publish` \| `/unpublish` \| `/archive` | — | detalhe (`duplicate` → 201) |
| `POST /admin/funnels/{id}/variants` | `{name, weight}` | `{variant, warnings}` (201) |
| `PATCH /admin/funnels/variants/{vid}` | `{name?, weight?, is_active?}` | `{variant, warnings}` |
| `DELETE /admin/funnels/variants/{vid}` | — | `{warnings}` |
| `POST /admin/funnels/variants/{vid}/duplicate` | — | `{variant, warnings}` (201) |
| `POST /admin/funnels/variants/{vid}/steps` | `{name, html}` | `{step, warnings}` (201) |
| `PUT /admin/funnels/variants/{vid}/steps/order` | `{step_ids: [uuid]}` | `{warnings}` |
| `PATCH /admin/funnels/steps/{sid}` | `{name?, html?}` | `{step, warnings}` |
| `DELETE /admin/funnels/steps/{sid}` | — | `{warnings}` |
| `GET /admin/funnels/variants/{vid}/preview` | — | `{funnel:{name, plan_id}, tracking_html, steps}` |
| `GET /admin/funnels/{id}/metrics` | `from?, to?` (ISO date), `variant_id?, utm_source?, utm_campaign?` | saída de `aggregate_metrics` + `{"period": {"from","to"}, "maturing": bool}` |

"Detalhe" = `{funnel:{id, slug, name, status, plan_id, tracking_html, created_at, updated_at}, variants:[{id, name, weight, is_active, position, steps:[{id, position, name, html}]}], session_count}`.
Erros de domínio: `FunnelNotFound` → 404; `FunnelGone` → 410; `FunnelError` → 422 com `detail = {"code", "message", "errors"?}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_funnel_routes.py
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from funnel_fixtures import db, engine, seed_admin, seed_plan, session_factory  # noqa: F401
from infra.database.setup import get_db
from infra.web.routers import funnels_admin, funnels_public


@pytest_asyncio.fixture
async def client(session_factory):
    async with session_factory() as s:
        admin = await seed_admin(s)
        plan = await seed_plan(s)
        await s.commit()
    state = {"user": SimpleNamespace(id=admin.id, role="admin")}

    app = FastAPI()
    app.include_router(funnels_public.router, prefix="/api/v1/funnels")
    app.include_router(funnels_admin.router, prefix="/api/v1/admin/funnels")

    async def _db():
        async with session_factory() as s:
            yield s

    def _require_admin():
        from fastapi import HTTPException
        if state["user"].role != "admin":
            raise HTTPException(status_code=403, detail="Acesso negado.")
        return state["user"]

    class _Attribution:
        claims = []

        async def claim(self, fsid, user_id):
            self.claims.append((fsid, user_id))
            return True

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[funnels_admin.require_admin] = _require_admin
    app.dependency_overrides[funnels_admin.get_audit_service] = lambda: None
    app.dependency_overrides[funnels_public.get_current_user] = lambda: state["user"]
    app.dependency_overrides[funnels_public.get_funnel_attribution_service] = lambda: _Attribution()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.state, c.plan_id = state, plan.id
        yield c


async def _published_funnel(client, slug="oferta"):
    r = await client.post("/api/v1/admin/funnels", json={"name": "Oferta", "slug": slug, "plan_id": str(client.plan_id)})
    assert r.status_code == 201, r.text
    detail = r.json()
    vid = detail["variants"][0]["id"]
    for name in ("Intro", "Oferta"):
        r = await client.post(f"/api/v1/admin/funnels/variants/{vid}/steps", json={"name": name, "html": f"<p>{name}</p>"})
        assert r.status_code == 201
    r = await client.post(f"/api/v1/admin/funnels/{detail['funnel']['id']}/publish")
    assert r.status_code == 200, r.text
    return detail["funnel"]["id"], vid


@pytest.mark.asyncio
async def test_full_public_flow_and_metrics(client):
    funnel_id, _ = await _published_funnel(client)
    r = await client.post("/api/v1/funnels/public/oferta/session", json={"utm_source": "meta"})
    assert r.status_code == 200
    body = r.json()
    assert [s["name"] for s in body["steps"]] == ["Intro", "Oferta"]
    fsid = body["fsid"]
    for payload in ({"type": "step_view", "step_position": 0}, {"type": "step_next", "step_position": 0},
                    {"type": "step_view", "step_position": 1}, {"type": "funnel_completed"}):
        r = await client.post("/api/v1/funnels/public/events", json={"fsid": fsid, **payload})
        assert r.status_code == 204, r.text

    metrics = (await client.get(f"/api/v1/admin/funnels/{funnel_id}/metrics")).json()
    assert metrics["summary"]["sessions"] == 1 and metrics["summary"]["completed"] == 1
    assert metrics["sources"][0]["utm_source"] == "meta"
    listing = (await client.get("/api/v1/admin/funnels")).json()
    assert listing[0]["sessions_30d"] == 1 and listing[0]["variant_count"] == 1


@pytest.mark.asyncio
async def test_public_errors(client):
    assert (await client.post("/api/v1/funnels/public/nada/session", json={})).status_code == 404
    funnel_id, _ = await _published_funnel(client)
    fsid = (await client.post("/api/v1/funnels/public/oferta/session", json={})).json()["fsid"]
    r = await client.post("/api/v1/funnels/public/events", json={"fsid": fsid, "type": "invoice_paid"})
    assert r.status_code == 422
    r = await client.post("/api/v1/funnels/public/events", json={"fsid": str(uuid.uuid4()), "type": "step_view", "step_position": 0})
    assert r.status_code == 404
    await client.post(f"/api/v1/admin/funnels/{funnel_id}/unpublish")
    r = await client.post("/api/v1/funnels/public/events", json={"fsid": fsid, "type": "step_view", "step_position": 0})
    assert r.status_code == 410


@pytest.mark.asyncio
async def test_publish_invalid_returns_errors(client):
    r = await client.post("/api/v1/admin/funnels", json={"name": "X", "slug": "x"})
    r = await client.post(f"/api/v1/admin/funnels/{r.json()['funnel']['id']}/publish")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "funnel.publish_invalid"
    assert "Selecione um plano de destino ativo." in r.json()["detail"]["errors"]


@pytest.mark.asyncio
async def test_invalid_slug_is_422(client):
    r = await client.post("/api/v1/admin/funnels", json={"name": "X", "slug": "Com Espaço"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "funnel.invalid_slug"


@pytest.mark.asyncio
async def test_preview_returns_draft_steps_without_session(client):
    r = await client.post("/api/v1/admin/funnels", json={"name": "X", "slug": "x"})
    vid = r.json()["variants"][0]["id"]
    await client.post(f"/api/v1/admin/funnels/variants/{vid}/steps", json={"name": "S", "html": "<b>s</b>"})
    r = await client.get(f"/api/v1/admin/funnels/variants/{vid}/preview")
    assert r.status_code == 200 and r.json()["steps"][0]["html"] == "<b>s</b>"


@pytest.mark.asyncio
async def test_admin_routes_forbidden_for_non_admin(client):
    client.state["user"] = SimpleNamespace(id=uuid.uuid4(), role="owner")
    assert (await client.get("/api/v1/admin/funnels")).status_code == 403


@pytest.mark.asyncio
async def test_claim_calls_attribution(client):
    fsid = uuid.uuid4()
    r = await client.post(f"/api/v1/funnels/sessions/{fsid}/claim")
    assert r.status_code == 200 and r.json() == {"linked": True}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_funnel_routes.py -v`
Expected: FAIL — `ImportError: cannot import name 'funnels_admin'`

- [ ] **Step 3: Write the DTOs**

```python
# app/application/dtos_funnels.py
"""DTOs dos endpoints de funis de venda."""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field


class FunnelSessionRequest(BaseModel):
    fsid: Optional[uuid.UUID] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    utm_term: Optional[str] = None
    referrer: Optional[str] = None


class FunnelEventRequest(BaseModel):
    fsid: uuid.UUID
    type: str = Field(max_length=40)
    step_position: Optional[int] = Field(default=None, ge=0)


class FunnelCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str
    plan_id: Optional[uuid.UUID] = None


class FunnelUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    slug: Optional[str] = None
    plan_id: Optional[uuid.UUID] = None
    tracking_html: Optional[str] = None


class VariantCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    weight: int = Field(ge=0)


class VariantUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    weight: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None


class StepCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    html: str = ""


class StepUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    html: Optional[str] = None


class StepOrderRequest(BaseModel):
    step_ids: list[uuid.UUID]
```

- [ ] **Step 4: Write the public router**

```python
# app/infra/web/routers/funnels_public.py
"""Endpoints públicos dos funis de venda (visitante anônimo) e claim autenticado."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from application.dtos_funnels import FunnelEventRequest, FunnelSessionRequest
from application.services.funnel_public_service import UTM_KEYS, FunnelGone, FunnelPublicService
from domain.funnels import FunnelError, FunnelNotFound
from infra.database.setup import get_db
from infra.repositories.funnel_repo import FunnelRepository
from infra.security.rate_limiter import enforce_rate_limit_async
from infra.web.dependencies import get_current_user, get_funnel_attribution_service

router = APIRouter()


def funnel_http_error(exc: FunnelError) -> HTTPException:
    if isinstance(exc, FunnelNotFound):
        return HTTPException(status_code=404, detail={"code": exc.code, "message": exc.message})
    if isinstance(exc, FunnelGone):
        return HTTPException(status_code=410, detail={"code": exc.code, "message": exc.message})
    detail = {"code": exc.code, "message": exc.message}
    if getattr(exc, "errors", None):
        detail["errors"] = exc.errors
    return HTTPException(status_code=422, detail=detail)


@router.post("/public/{slug}/session")
async def start_session(slug: str, body: FunnelSessionRequest, request: Request, db: AsyncSession = Depends(get_db)):
    await enforce_rate_limit_async(request, bucket="funnel_session", limit=30, window_seconds=60)
    svc = FunnelPublicService(FunnelRepository(db))
    try:
        data = await svc.start_session(
            slug, fsid=body.fsid, utm={k: getattr(body, k) for k in UTM_KEYS}, referrer=body.referrer,
        )
    except FunnelError as exc:
        raise funnel_http_error(exc)
    await db.commit()
    return data


@router.post("/public/events", status_code=status.HTTP_204_NO_CONTENT)
async def record_event(body: FunnelEventRequest, request: Request, db: AsyncSession = Depends(get_db)):
    await enforce_rate_limit_async(request, bucket="funnel_events", limit=120, window_seconds=60)
    await enforce_rate_limit_async(request, bucket=f"funnel_events_fsid:{body.fsid}", limit=60, window_seconds=60)
    svc = FunnelPublicService(FunnelRepository(db))
    try:
        await svc.record_event(body.fsid, body.type, body.step_position)
    except FunnelError as exc:
        raise funnel_http_error(exc)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sessions/{fsid}/claim")
async def claim_session(fsid: uuid.UUID, current_user=Depends(get_current_user),
                        attribution=Depends(get_funnel_attribution_service)):
    return {"linked": await attribution.claim(fsid, current_user.id)}
```

- [ ] **Step 5: Write the admin router**

```python
# app/infra/web/routers/funnels_admin.py
"""Rotas admin de funis de venda — somente `require_admin`."""
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from application.dtos_funnels import (
    FunnelCreateRequest, FunnelUpdateRequest, StepCreateRequest, StepOrderRequest, StepUpdateRequest,
    VariantCreateRequest, VariantUpdateRequest,
)
from application.services.funnel_admin_service import UNSET, FunnelAdminService
from application.services.funnel_metrics import aggregate_metrics
from domain.funnels import FunnelError, FunnelNotFound
from infra.database.setup import get_db
from infra.observability.audit import record_audit_event
from infra.repositories.funnel_metrics_repo import FunnelMetricsRepository
from infra.repositories.funnel_repo import FunnelRepository
from infra.web.dependencies import get_audit_service, require_admin
from infra.web.routers.funnels_public import funnel_http_error

router = APIRouter()


def _svc(db: AsyncSession) -> FunnelAdminService:
    return FunnelAdminService(FunnelRepository(db))


def _step(s):
    return {"id": s.id, "position": s.position, "name": s.name, "html": s.html}


def _variant(v, steps=None):
    data = {"id": v.id, "name": v.name, "weight": v.weight, "is_active": v.is_active, "position": v.position}
    if steps is not None:
        data["steps"] = [_step(s) for s in steps]
    return data


def _detail(d):
    f = d["funnel"]
    return {
        "funnel": {"id": f.id, "slug": f.slug, "name": f.name, "status": f.status, "plan_id": f.plan_id,
                   "tracking_html": f.tracking_html, "created_at": f.created_at, "updated_at": f.updated_at},
        "variants": [_variant(x["variant"], x["steps"]) for x in d["variants"]],
        "session_count": d["session_count"],
    }


async def _run(db, coro):
    try:
        result = await coro
    except FunnelError as exc:
        await db.rollback()
        raise funnel_http_error(exc)
    await db.commit()
    return result


async def _audit(audit, request, admin, action, funnel_id, metadata=None):
    await record_audit_event(audit, request, actor=admin, action=action, resource_type="funnel",
                             resource_id=str(funnel_id), result="success", metadata=metadata or {})


@router.get("")
async def list_funnels(db: AsyncSession = Depends(get_db), admin=Depends(require_admin)):
    repo, metrics = FunnelRepository(db), FunnelMetricsRepository(db)
    kpis = await metrics.list_kpis(datetime.now(timezone.utc) - timedelta(days=30))
    out = []
    for f in await repo.list_funnels():
        sessions, paid = kpis.get(f.id, (0, 0))
        out.append({"id": f.id, "slug": f.slug, "name": f.name, "status": f.status, "plan_id": f.plan_id,
                    "variant_count": len(await repo.list_variants(f.id)), "sessions_30d": sessions,
                    "paid_30d": paid, "paid_conversion_30d": round(paid / sessions, 4) if sessions else 0.0})
    return out


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_funnel(body: FunnelCreateRequest, request: Request, db: AsyncSession = Depends(get_db),
                        admin=Depends(require_admin), audit=Depends(get_audit_service)):
    svc = _svc(db)
    funnel = await _run(db, svc.create_funnel(name=body.name, slug=body.slug, plan_id=body.plan_id, created_by=admin.id))
    await _audit(audit, request, admin, "funnel.created", funnel.id)
    return _detail(await svc.get_detail(funnel.id))


@router.get("/variants/{variant_id}/preview")
async def preview_variant(variant_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin=Depends(require_admin)):
    repo = FunnelRepository(db)
    variant = await repo.get_variant(variant_id)
    if variant is None:
        raise funnel_http_error(FunnelNotFound("Variante"))
    funnel = await repo.get_funnel(variant.funnel_id)
    return {"funnel": {"name": funnel.name, "plan_id": funnel.plan_id}, "tracking_html": funnel.tracking_html or "",
            "steps": [{"position": s.position, "name": s.name, "html": s.html} for s in await repo.list_steps(variant.id)]}


@router.get("/{funnel_id}")
async def get_funnel(funnel_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin=Depends(require_admin)):
    try:
        return _detail(await _svc(db).get_detail(funnel_id))
    except FunnelError as exc:
        raise funnel_http_error(exc)


@router.patch("/{funnel_id}")
async def update_funnel(funnel_id: uuid.UUID, body: FunnelUpdateRequest, db: AsyncSession = Depends(get_db),
                        admin=Depends(require_admin)):
    svc, sent = _svc(db), body.model_fields_set
    await _run(db, svc.update_funnel(
        funnel_id, name=body.name, slug=body.slug,
        plan_id=body.plan_id if "plan_id" in sent else UNSET,
        tracking_html=body.tracking_html if "tracking_html" in sent else UNSET,
    ))
    return _detail(await svc.get_detail(funnel_id))


@router.post("/{funnel_id}/duplicate", status_code=status.HTTP_201_CREATED)
async def duplicate_funnel(funnel_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db),
                           admin=Depends(require_admin), audit=Depends(get_audit_service)):
    svc = _svc(db)
    copy = await _run(db, svc.duplicate_funnel(funnel_id, created_by=admin.id))
    await _audit(audit, request, admin, "funnel.duplicated", copy.id, {"source_id": str(funnel_id)})
    return _detail(await svc.get_detail(copy.id))


async def _transition(action, funnel_id, request, db, admin, audit):
    svc = _svc(db)
    await _run(db, getattr(svc, action)(funnel_id))
    await _audit(audit, request, admin, f"funnel.{action}", funnel_id)
    return _detail(await svc.get_detail(funnel_id))


@router.post("/{funnel_id}/publish")
async def publish(funnel_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db),
                  admin=Depends(require_admin), audit=Depends(get_audit_service)):
    return await _transition("publish", funnel_id, request, db, admin, audit)


@router.post("/{funnel_id}/unpublish")
async def unpublish(funnel_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db),
                    admin=Depends(require_admin), audit=Depends(get_audit_service)):
    return await _transition("unpublish", funnel_id, request, db, admin, audit)


@router.post("/{funnel_id}/archive")
async def archive(funnel_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db),
                  admin=Depends(require_admin), audit=Depends(get_audit_service)):
    return await _transition("archive", funnel_id, request, db, admin, audit)


@router.post("/{funnel_id}/variants", status_code=status.HTTP_201_CREATED)
async def add_variant(funnel_id: uuid.UUID, body: VariantCreateRequest, request: Request,
                      db: AsyncSession = Depends(get_db), admin=Depends(require_admin), audit=Depends(get_audit_service)):
    variant, warnings = await _run(db, _svc(db).add_variant(funnel_id, name=body.name, weight=body.weight))
    await _audit(audit, request, admin, "funnel.variant_added", funnel_id, {"weight": body.weight})
    return {"variant": _variant(variant, []), "warnings": warnings}


@router.patch("/variants/{variant_id}")
async def update_variant(variant_id: uuid.UUID, body: VariantUpdateRequest, request: Request,
                         db: AsyncSession = Depends(get_db), admin=Depends(require_admin), audit=Depends(get_audit_service)):
    variant, warnings = await _run(db, _svc(db).update_variant(
        variant_id, name=body.name, weight=body.weight, is_active=body.is_active))
    if body.weight is not None or body.is_active is not None:
        await _audit(audit, request, admin, "funnel.variant_weights_changed", variant.funnel_id,
                     {"variant_id": str(variant_id), "weight": variant.weight, "is_active": variant.is_active})
    return {"variant": _variant(variant), "warnings": warnings}


@router.delete("/variants/{variant_id}")
async def delete_variant(variant_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin=Depends(require_admin)):
    return {"warnings": await _run(db, _svc(db).delete_variant(variant_id))}


@router.post("/variants/{variant_id}/duplicate", status_code=status.HTTP_201_CREATED)
async def duplicate_variant(variant_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin=Depends(require_admin)):
    svc = _svc(db)
    variant, warnings = await _run(db, svc.duplicate_variant(variant_id))
    return {"variant": _variant(variant, await svc._repo.list_steps(variant.id)), "warnings": warnings}


@router.post("/variants/{variant_id}/steps", status_code=status.HTTP_201_CREATED)
async def add_step(variant_id: uuid.UUID, body: StepCreateRequest, db: AsyncSession = Depends(get_db),
                   admin=Depends(require_admin)):
    step, warnings = await _run(db, _svc(db).add_step(variant_id, name=body.name, html=body.html))
    return {"step": _step(step), "warnings": warnings}


@router.put("/variants/{variant_id}/steps/order")
async def reorder_steps(variant_id: uuid.UUID, body: StepOrderRequest, db: AsyncSession = Depends(get_db),
                        admin=Depends(require_admin)):
    return {"warnings": await _run(db, _svc(db).reorder_steps(variant_id, body.step_ids))}


@router.patch("/steps/{step_id}")
async def update_step(step_id: uuid.UUID, body: StepUpdateRequest, db: AsyncSession = Depends(get_db),
                      admin=Depends(require_admin)):
    step, warnings = await _run(db, _svc(db).update_step(step_id, name=body.name, html=body.html))
    return {"step": _step(step), "warnings": warnings}


@router.delete("/steps/{step_id}")
async def delete_step(step_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin=Depends(require_admin)):
    return {"warnings": await _run(db, _svc(db).delete_step(step_id))}


@router.get("/{funnel_id}/metrics")
async def funnel_metrics(
    funnel_id: uuid.UUID,
    from_: Optional[date] = Query(default=None, alias="from"),
    to: Optional[date] = Query(default=None),
    variant_id: Optional[uuid.UUID] = None,
    utm_source: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    repo = FunnelRepository(db)
    funnel = await repo.get_funnel(funnel_id)
    if funnel is None:
        raise funnel_http_error(FunnelNotFound())
    today = datetime.now(timezone.utc).date()
    end_day = to or today
    start_day = from_ or (end_day - timedelta(days=29))
    start = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
    end = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=timezone.utc)

    metrics = FunnelMetricsRepository(db)
    rows = await metrics.cohort_rows(funnel_id, start, end, variant_id=variant_id,
                                     utm_source=utm_source, utm_campaign=utm_campaign)
    variants = await repo.list_variants(funnel_id)
    # Nomes das etapas vêm da primeira variante (as posições são compartilhadas entre variantes).
    first_steps = await repo.list_steps(variants[0].id) if variants else []
    data = aggregate_metrics(
        rows=rows,
        step_views=await metrics.step_view_counts([r.id for r in rows]),
        variants=[(v.id, v.name, v.weight) for v in variants if variant_id is None or v.id == variant_id],
        step_names={s.position: s.name for s in first_steps},
    )
    data["period"] = {"from": start_day.isoformat(), "to": end_day.isoformat()}
    data["maturing"] = (today - end_day).days < 14
    return data
```

> Nota: as rotas `/variants/...` e `/steps/...` são declaradas **antes** de `/{funnel_id}` só onde há conflito de método+caminho (`GET /variants/{id}/preview` vs `GET /{funnel_id}`); FastAPI casa na ordem de declaração.

- [ ] **Step 6: Register in `main.py`**

Em `app/infra/web/main.py`, adicione `funnels_admin` e `funnels_public` à lista `from infra.web.routers import (...)` e, após `app.include_router(pix.router, ...)`:

```python
app.include_router(funnels_public.router, prefix="/api/v1/funnels", tags=["Funnels"])
app.include_router(funnels_admin.router, prefix="/api/v1/admin/funnels", tags=["Admin Funnels"])
```

- [ ] **Step 7: Run tests**

Run: `python -m pytest tests/unit/test_funnel_routes.py -v`
Expected: PASS (7 testes)

Run: `python -m pytest tests/unit -q`
Expected: suíte unitária inteira verde (mesmo número de falhas pré-existentes que antes da feature, se houver — anote no relatório).

- [ ] **Step 8: Commit**

```bash
git add app/application/dtos_funnels.py app/infra/web/routers/funnels_public.py app/infra/web/routers/funnels_admin.py app/infra/web/main.py tests/unit/test_funnel_routes.py
git commit -m "feat(funnels): API pública e admin de funis

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Teste de integração ponta a ponta da atribuição (SQLite)

**Files:**
- Test: `tests/unit/test_funnel_attribution_e2e.py`

**Interfaces:**
- Consumes: `FunnelPublicService`, `FunnelAttributionService`, `FunnelTrackingAnalytics`, `FunnelMetricsRepository`, `aggregate_metrics`.

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_funnel_attribution_e2e.py
"""Sessão → cadastro → trial → assinatura → pagamento → métricas, com sessões de banco separadas."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from funnel_fixtures import db, engine, seed_admin, seed_funnel, session_factory  # noqa: F401
from application.services.funnel_attribution_service import FunnelAttributionService
from application.services.funnel_metrics import aggregate_metrics
from application.services.funnel_public_service import FunnelPublicService
from infra.observability.funnel_analytics import FunnelTrackingAnalytics
from infra.repositories.funnel_metrics_repo import FunnelMetricsRepository
from infra.repositories.funnel_repo import FunnelRepository


class _NoopPostHog:
    async def track_event(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_visit_to_paid(db, session_factory):
    funnel, variant = await seed_funnel(db, status="published", steps=2)
    await db.commit()

    public = FunnelPublicService(FunnelRepository(db))
    started = await public.start_session("oferta", fsid=None, utm={"utm_source": "meta"}, referrer=None)
    await public.record_event(started["fsid"], "step_view", 0)
    await public.record_event(started["fsid"], "step_view", 1)
    await public.record_event(started["fsid"], "funnel_completed", None)
    await db.commit()

    async with session_factory() as s:
        user = await seed_admin(s, role="owner")
        await s.commit()

    attribution = FunnelAttributionService(session_factory)
    analytics = FunnelTrackingAnalytics(_NoopPostHog(), attribution)
    assert await attribution.link_registration(started["fsid"], user.id) is True
    await analytics.track_event(str(user.id), "trial_activated", {})
    await analytics.track_event(str(user.id), "subscription_created", {})
    await analytics.track_event(str(user.id), "invoice_paid", {"amount": "49.90"})
    await analytics.track_event(str(user.id), "invoice_paid", {"amount": "49.90"})  # renovação: ignorada

    async with session_factory() as s:
        metrics = FunnelMetricsRepository(s)
        now = datetime.now(timezone.utc)
        rows = await metrics.cohort_rows(funnel.id, now - timedelta(days=1), now + timedelta(minutes=1))
        out = aggregate_metrics(rows=rows, step_views=await metrics.step_view_counts([r.id for r in rows]),
                                variants=[(variant.id, "A", 100)], step_names={})
    assert out["summary"] == {"sessions": 1, "completed": 1, "registered": 1, "trials": 1, "subscribed": 1,
                              "paid": 1, "paid_conversion": 1.0, "revenue": "49.90"}
```

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/unit/test_funnel_attribution_e2e.py -v`
Expected: PASS (as Tasks 3–8 já implementam tudo; se falhar, corrija o componente responsável — não o teste).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_funnel_attribution_e2e.py
git commit -m "test(funnels): fluxo completo da visita ao pagamento

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Migration e índice parcial em Postgres real

**Files:**
- Test: `tests/integration/test_funnel_migrations.py`

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_funnel_migrations.py
# ruff: noqa: E402
"""Requer TEST_POSTGRES_URL apontando para um Postgres descartável de teste, já com `alembic upgrade head`."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[2]
app_dir = backend_dir / "app"
if str(app_dir) not in sys.path:
    sys.path.append(str(app_dir))

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _alembic(*args):
    env = {**os.environ, "DATABASE_URL": TEST_POSTGRES_URL}
    subprocess.run(["alembic", *args], cwd=backend_dir, env=env, check=True)


def test_migration_roundtrip():
    _alembic("upgrade", "head")
    _alembic("downgrade", "20260914_0022")
    _alembic("upgrade", "head")


@pytest.mark.asyncio
async def test_tables_and_partial_unique_index():
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
        assert {"funnels", "funnel_variants", "funnel_steps", "funnel_sessions", "funnel_events"} <= set(tables)

        admin_id, funnel_id, variant_id, session_id = (uuid.uuid4() for _ in range(4))
        await conn.execute(text(
            "INSERT INTO users (id, name, email, password_hash, role) VALUES (:id, 'a', :email, 'h', 'admin')"
        ), {"id": admin_id, "email": f"{admin_id}@t.com"})
        await conn.execute(text(
            "INSERT INTO funnels (id, slug, name, created_by) VALUES (:id, :slug, 'f', :admin)"
        ), {"id": funnel_id, "slug": f"pg-{funnel_id.hex[:8]}", "admin": admin_id})
        await conn.execute(text(
            "INSERT INTO funnel_variants (id, funnel_id, name) VALUES (:id, :f, 'A')"
        ), {"id": variant_id, "f": funnel_id})
        await conn.execute(text(
            "INSERT INTO funnel_sessions (id, funnel_id, variant_id) VALUES (:id, :f, :v)"
        ), {"id": session_id, "f": funnel_id, "v": variant_id})
        insert_event = text(
            "INSERT INTO funnel_events (id, session_id, funnel_id, variant_id, type, step_position) "
            "VALUES (:id, :s, :f, :v, :t, 0)"
        )
        params = {"s": session_id, "f": funnel_id, "v": variant_id}
        await conn.execute(insert_event, {"id": uuid.uuid4(), "t": "step_next", **params})
        await conn.execute(insert_event, {"id": uuid.uuid4(), "t": "step_next", **params})
        await conn.execute(insert_event, {"id": uuid.uuid4(), "t": "step_view", **params})
        with pytest.raises(IntegrityError):
            await conn.execute(insert_event, {"id": uuid.uuid4(), "t": "step_view", **params})
        await conn.rollback()
    await engine.dispose()
```

- [ ] **Step 2: Run against a disposable Postgres**

Run (PowerShell): `$env:TEST_POSTGRES_URL='postgresql://marketfy:marketfy@localhost:5432/marketfy_test'; python -m pytest tests/integration/test_funnel_migrations.py -v`
Expected: PASS. Sem Postgres disponível: SKIPPED — registre no relatório da task que a validação ficou pendente para staging (`alembic upgrade head && alembic downgrade -1 && alembic upgrade head`).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_funnel_migrations.py
git commit -m "test(funnels): migration e índice parcial em Postgres

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

# FRONTEND

Todos os comandos de frontend rodam em `marketfy/frontend`. Testes: `npx vitest run <arquivo>`. Commits no repo do frontend.

### Task 12: Biblioteca `lib/funnels.js`

**Files:**
- Create: `src/lib/funnels.js`
- Test: `src/test/funnelsLib.test.js`

**Interfaces:**
- Produces:
  - `FUNNEL_MESSAGE_SOURCE = 'marketfy-funnel'`, `FUNNEL_HELPER_SCRIPT: string`
  - `readFsid(slug) -> string|null`, `saveFsid(slug, fsid)`, `rememberCheckoutFsid(fsid)`, `readCheckoutFsid() -> string|null`, `clearCheckoutFsid()`
  - `readUtms(search: string) -> {utm_source?, utm_medium?, utm_campaign?, utm_content?, utm_term?}`
  - `buildSrcdoc({ html, trackingHtml }) -> string`
  - `parseFunnelMessage(event, frameWindow) -> 'next'|'back'|'finish'|null`
  - `finishUrl({ planId, fsid, loggedIn }) -> string`
  - `startFunnelSession(slug, body) -> Promise<data>`, `sendFunnelEvent(body) -> Promise<void>` (1 retentativa, nunca rejeita), `claimFunnelSession(fsid) -> Promise<void>` (nunca rejeita), `getFunnelPreview(variantId) -> Promise<data>`
  - `SANDBOX = 'allow-scripts allow-forms allow-popups'`

- [ ] **Step 1: Write the failing test**

```js
// src/test/funnelsLib.test.js
import { beforeEach, describe, expect, it, vi } from 'vitest';
import api from '../lib/api';
import {
  FUNNEL_HELPER_SCRIPT, SANDBOX, buildSrcdoc, claimFunnelSession, finishUrl, parseFunnelMessage,
  readCheckoutFsid, readFsid, readUtms, rememberCheckoutFsid, saveFsid, sendFunnelEvent,
} from '../lib/funnels';

vi.mock('../lib/api', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

describe('lib/funnels', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  it('persists fsid per slug and for checkout', () => {
    saveFsid('oferta', 'abc');
    expect(readFsid('oferta')).toBe('abc');
    expect(readFsid('outro')).toBeNull();
    rememberCheckoutFsid('abc');
    expect(readCheckoutFsid()).toBe('abc');
  });

  it('reads only known utm params', () => {
    expect(readUtms('?utm_source=meta&utm_campaign=bf&x=1')).toEqual({ utm_source: 'meta', utm_campaign: 'bf' });
  });

  it('builds srcdoc with tracking, helper and step html', () => {
    const doc = buildSrcdoc({ html: '<p>oi</p>', trackingHtml: '<script>px()</script>' });
    expect(doc.startsWith('<!doctype html>')).toBe(true);
    expect(doc).toContain('<script>px()</script>');
    expect(doc).toContain(FUNNEL_HELPER_SCRIPT);
    expect(doc.indexOf(FUNNEL_HELPER_SCRIPT)).toBeLessThan(doc.indexOf('<p>oi</p>'));
  });

  it('never grants same-origin or top navigation', () => {
    expect(SANDBOX).not.toMatch(/allow-same-origin|allow-top-navigation/);
  });

  it('accepts only well-formed messages from the frame', () => {
    const frame = {};
    const ok = (type, source = frame) => parseFunnelMessage({ source, data: { source: 'marketfy-funnel', type } }, frame);
    expect(ok('next')).toBe('next');
    expect(ok('finish')).toBe('finish');
    expect(ok('steal')).toBeNull();
    expect(ok('next', {})).toBeNull();
    expect(parseFunnelMessage({ source: frame, data: 'next' }, frame)).toBeNull();
    expect(parseFunnelMessage({ source: frame, data: { source: 'x', type: 'next' } }, frame)).toBeNull();
  });

  it('builds finish urls', () => {
    expect(finishUrl({ planId: 'p1', fsid: 'f1', loggedIn: false })).toBe('/register?plan=p1&fsid=f1');
    expect(finishUrl({ planId: 'p1', fsid: 'f1', loggedIn: true })).toBe('/plans?plan=p1&fsid=f1');
    expect(finishUrl({ planId: null, fsid: 'f1', loggedIn: false })).toBe('/register?fsid=f1');
  });

  it('retries an event once and then gives up silently', async () => {
    api.post.mockRejectedValue(new Error('net'));
    await expect(sendFunnelEvent({ fsid: 'f', type: 'step_view', step_position: 0 })).resolves.toBeUndefined();
    expect(api.post).toHaveBeenCalledTimes(2);
  });

  it('claim never throws', async () => {
    api.post.mockRejectedValue(new Error('401'));
    await expect(claimFunnelSession('f')).resolves.toBeUndefined();
    expect(api.post).toHaveBeenCalledWith('/funnels/sessions/f/claim');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/test/funnelsLib.test.js`
Expected: FAIL — `Failed to resolve import "../lib/funnels"`

- [ ] **Step 3: Write the library**

```js
// src/lib/funnels.js
import api from './api';

export const FUNNEL_MESSAGE_SOURCE = 'marketfy-funnel';
export const SANDBOX = 'allow-scripts allow-forms allow-popups';
const UTM_KEYS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'];
const CHECKOUT_KEY = 'funnel_fsid';
const ACTIONS = new Set(['next', 'back', 'finish']);

export const FUNNEL_HELPER_SCRIPT = `<script>(function(){
function send(t){parent.postMessage({source:'${FUNNEL_MESSAGE_SOURCE}',type:t},'*');}
window.Funnel={next:function(){send('next');},back:function(){send('back');},finish:function(){send('finish');}};
document.addEventListener('click',function(e){
var el=e.target&&e.target.closest&&e.target.closest('[data-funnel-next],[data-funnel-back],[data-funnel-finish]');
if(!el)return;e.preventDefault();
if(el.hasAttribute('data-funnel-next'))send('next');else if(el.hasAttribute('data-funnel-back'))send('back');else send('finish');
});})();</script>`;

const safe = (fn, fallback = null) => {
  try {
    return fn();
  } catch {
    return fallback;
  }
};

export const readFsid = (slug) => safe(() => localStorage.getItem(`funnel:${slug}`));
export const saveFsid = (slug, fsid) => safe(() => localStorage.setItem(`funnel:${slug}`, fsid));
export const rememberCheckoutFsid = (fsid) => safe(() => sessionStorage.setItem(CHECKOUT_KEY, fsid));
export const readCheckoutFsid = () => safe(() => sessionStorage.getItem(CHECKOUT_KEY));
export const clearCheckoutFsid = () => safe(() => sessionStorage.removeItem(CHECKOUT_KEY));

export function readUtms(search) {
  const params = new URLSearchParams(search);
  return UTM_KEYS.reduce((acc, key) => {
    const value = params.get(key);
    if (value) acc[key] = value;
    return acc;
  }, {});
}

export function buildSrcdoc({ html, trackingHtml }) {
  return `<!doctype html><html><head><meta charset="utf-8">`
    + `<meta name="viewport" content="width=device-width,initial-scale=1">`
    + `${trackingHtml || ''}${FUNNEL_HELPER_SCRIPT}</head><body>${html || ''}</body></html>`;
}

export function parseFunnelMessage(event, frameWindow) {
  if (!frameWindow || event.source !== frameWindow) return null;
  const data = event.data;
  if (!data || typeof data !== 'object' || data.source !== FUNNEL_MESSAGE_SOURCE) return null;
  return ACTIONS.has(data.type) ? data.type : null;
}

export function finishUrl({ planId, fsid, loggedIn }) {
  const params = new URLSearchParams();
  if (planId) params.set('plan', planId);
  params.set('fsid', fsid);
  return `${loggedIn ? '/plans' : '/register'}?${params.toString()}`;
}

export const startFunnelSession = (slug, body) =>
  api.post(`/funnels/public/${encodeURIComponent(slug)}/session`, body).then((r) => r.data);

export const getFunnelPreview = (variantId) =>
  api.get(`/admin/funnels/variants/${variantId}/preview`).then((r) => r.data);

export async function sendFunnelEvent(body) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      await api.post('/funnels/public/events', body);
      return;
    } catch {
      // segue para a próxima tentativa; depois desiste em silêncio
    }
  }
}

export async function claimFunnelSession(fsid) {
  try {
    await api.post(`/funnels/sessions/${fsid}/claim`);
  } catch {
    // atribuição nunca bloqueia o checkout
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/test/funnelsLib.test.js`
Expected: PASS (8 testes)

- [ ] **Step 5: Commit**

```bash
git add src/lib/funnels.js src/test/funnelsLib.test.js
git commit -m "feat(funnels): biblioteca do funil (fsid, srcdoc, protocolo postMessage)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Iframe da etapa e página pública `/f/:slug`

**Files:**
- Create: `src/components/funnels/FunnelStepFrame.jsx`
- Create: `src/pages/funnels/FunnelPlayer.jsx`
- Modify: `src/App.jsx` (lazy import + rota pública antes de `path="*"`)
- Test: `src/test/funnelPlayer.test.jsx`

**Interfaces:**
- Consumes: `lib/funnels.js` (Task 12), `useAuth` (`{ user, loading }`).
- Produces: `<FunnelStepFrame html trackingHtml title onAction />` (chama `onAction('next'|'back'|'finish')`); página default export `FunnelPlayer`; rota `/f/:slug`.

- [ ] **Step 1: Write the failing test**

```jsx
// src/test/funnelPlayer.test.jsx
import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FunnelPlayer from '../pages/funnels/FunnelPlayer';
import * as funnels from '../lib/funnels';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});
let authUser = null;
vi.mock('../hooks/useAuth', () => ({ useAuth: () => ({ user: authUser, loading: false }) }));
vi.mock('../lib/funnels', async () => {
  const actual = await vi.importActual('../lib/funnels');
  return {
    ...actual,
    startFunnelSession: vi.fn(),
    sendFunnelEvent: vi.fn().mockResolvedValue(undefined),
    getFunnelPreview: vi.fn(),
  };
});

const session = {
  fsid: 'fs-1', funnel: { name: 'Oferta', plan_id: 'plan-1' }, variant: { id: 'v1' }, tracking_html: '',
  steps: [{ position: 0, name: 'Intro', html: '<p>Intro</p>' }, { position: 1, name: 'Oferta', html: '<p>Oferta</p>' }],
  resume_position: 0,
};

function renderAt(url) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes><Route path="/f/:slug" element={<FunnelPlayer />} /></Routes>
    </MemoryRouter>,
  );
}

function postFromFrame(type) {
  const frame = screen.getByTitle(/etapa/i);
  act(() => {
    window.dispatchEvent(new MessageEvent('message', {
      data: { source: 'marketfy-funnel', type }, source: frame.contentWindow,
    }));
  });
}

describe('FunnelPlayer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    authUser = null;
    funnels.startFunnelSession.mockResolvedValue(session);
  });

  it('starts a session with utms, stores fsid and logs the first view', async () => {
    renderAt('/f/oferta?utm_source=meta');
    await screen.findByTitle('Etapa 1 de 2');
    expect(funnels.startFunnelSession).toHaveBeenCalledWith('oferta', expect.objectContaining({ utm_source: 'meta', fsid: null }));
    expect(localStorage.getItem('funnel:oferta')).toBe('fs-1');
    expect(funnels.sendFunnelEvent).toHaveBeenCalledWith({ fsid: 'fs-1', type: 'step_view', step_position: 0 });
    const frame = screen.getByTitle('Etapa 1 de 2');
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts allow-forms allow-popups');
  });

  it('resumes with the stored fsid', async () => {
    localStorage.setItem('funnel:oferta', 'fs-1');
    renderAt('/f/oferta');
    await screen.findByTitle('Etapa 1 de 2');
    expect(funnels.startFunnelSession).toHaveBeenCalledWith('oferta', expect.objectContaining({ fsid: 'fs-1' }));
  });

  it('advances on next and finishes into register with fsid', async () => {
    renderAt('/f/oferta');
    await screen.findByTitle('Etapa 1 de 2');
    postFromFrame('next');
    await screen.findByTitle('Etapa 2 de 2');
    expect(funnels.sendFunnelEvent).toHaveBeenCalledWith({ fsid: 'fs-1', type: 'step_next', step_position: 0 });
    postFromFrame('next');
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/register?plan=plan-1&fsid=fs-1'));
    expect(funnels.sendFunnelEvent).toHaveBeenCalledWith({ fsid: 'fs-1', type: 'funnel_completed' });
    expect(sessionStorage.getItem('funnel_fsid')).toBe('fs-1');
  });

  it('sends logged users to plans', async () => {
    authUser = { id: 'u1', role: 'owner' };
    renderAt('/f/oferta');
    await screen.findByTitle('Etapa 1 de 2');
    postFromFrame('finish');
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/plans?plan=plan-1&fsid=fs-1'));
  });

  it('ignores messages from other windows', async () => {
    renderAt('/f/oferta');
    await screen.findByTitle('Etapa 1 de 2');
    act(() => {
      window.dispatchEvent(new MessageEvent('message', { data: { source: 'marketfy-funnel', type: 'next' }, source: window }));
    });
    expect(screen.getByTitle('Etapa 1 de 2')).toBeInTheDocument();
  });

  it('shows not found when the funnel is unavailable', async () => {
    funnels.startFunnelSession.mockRejectedValue({ response: { status: 404 } });
    renderAt('/f/nada');
    expect(await screen.findByText(/funil não encontrado/i)).toBeInTheDocument();
  });

  it('preview mode loads the variant and records nothing', async () => {
    authUser = { id: 'a', role: 'admin' };
    funnels.getFunnelPreview.mockResolvedValue({ funnel: session.funnel, tracking_html: '', steps: session.steps });
    renderAt('/f/oferta?preview=v1');
    await screen.findByTitle('Etapa 1 de 2');
    expect(screen.getByText(/prévia/i)).toBeInTheDocument();
    postFromFrame('next');
    await screen.findByTitle('Etapa 2 de 2');
    expect(funnels.startFunnelSession).not.toHaveBeenCalled();
    expect(funnels.sendFunnelEvent).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/test/funnelPlayer.test.jsx`
Expected: FAIL — `Failed to resolve import "../pages/funnels/FunnelPlayer"`

- [ ] **Step 3: Write the frame component**

```jsx
// src/components/funnels/FunnelStepFrame.jsx
import { useEffect, useMemo, useRef } from 'react';
import { SANDBOX, buildSrcdoc, parseFunnelMessage } from '../../lib/funnels';

export default function FunnelStepFrame({ html, trackingHtml, title, onAction, className = '' }) {
  const frameRef = useRef(null);
  const srcDoc = useMemo(() => buildSrcdoc({ html, trackingHtml }), [html, trackingHtml]);
  const onActionRef = useRef(onAction);
  onActionRef.current = onAction;

  useEffect(() => {
    const handler = (event) => {
      const action = parseFunnelMessage(event, frameRef.current?.contentWindow);
      if (action) onActionRef.current?.(action);
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, []);

  return (
    <iframe
      ref={frameRef}
      title={title}
      sandbox={SANDBOX}
      srcDoc={srcDoc}
      className={`w-full border-0 ${className}`}
    />
  );
}
```

- [ ] **Step 4: Write the player page**

```jsx
// src/pages/funnels/FunnelPlayer.jsx
import { useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import FunnelStepFrame from '../../components/funnels/FunnelStepFrame';
import { useAuth } from '../../hooks/useAuth';
import {
  finishUrl, getFunnelPreview, readFsid, readUtms, rememberCheckoutFsid, saveFsid, sendFunnelEvent,
  startFunnelSession,
} from '../../lib/funnels';

export default function FunnelPlayer() {
  const { slug } = useParams();
  const { search } = useLocation();
  const navigate = useNavigate();
  const { user, loading: authLoading } = useAuth();
  const previewVariant = new URLSearchParams(search).get('preview');

  const [data, setData] = useState(null);
  const [index, setIndex] = useState(0);
  const [status, setStatus] = useState('loading'); // loading | ready | missing

  useEffect(() => {
    if (previewVariant && authLoading) return;
    let cancelled = false;
    const load = async () => {
      try {
        if (previewVariant) {
          const preview = await getFunnelPreview(previewVariant);
          if (!cancelled) {
            setData({ ...preview, fsid: null });
            setIndex(0);
            setStatus('ready');
          }
          return;
        }
        const result = await startFunnelSession(slug, {
          fsid: readFsid(slug), ...readUtms(search), referrer: document.referrer || null,
        });
        if (cancelled) return;
        saveFsid(slug, result.fsid);
        const start = result.steps.findIndex((s) => s.position === result.resume_position);
        setData(result);
        setIndex(start >= 0 ? start : 0);
        setStatus('ready');
      } catch {
        if (!cancelled) setStatus('missing');
      }
    };
    load();
    return () => { cancelled = true; };
    // search é lido só na primeira carga (UTMs da chegada)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, previewVariant, authLoading]);

  const step = data?.steps[index];
  const recording = Boolean(data?.fsid);

  useEffect(() => {
    if (recording && step) sendFunnelEvent({ fsid: data.fsid, type: 'step_view', step_position: step.position });
  }, [recording, step, data?.fsid]);

  const finish = useCallback(() => {
    if (!recording) return;
    sendFunnelEvent({ fsid: data.fsid, type: 'funnel_completed' });
    rememberCheckoutFsid(data.fsid);
    navigate(finishUrl({ planId: data.funnel.plan_id, fsid: data.fsid, loggedIn: Boolean(user) }));
  }, [recording, data, navigate, user]);

  const onAction = useCallback((action) => {
    if (!data) return;
    if (action === 'back') {
      setIndex((i) => Math.max(0, i - 1));
      return;
    }
    if (action === 'finish' || index >= data.steps.length - 1) {
      finish();
      return;
    }
    if (recording) sendFunnelEvent({ fsid: data.fsid, type: 'step_next', step_position: data.steps[index].position });
    setIndex((i) => i + 1);
  }, [data, index, recording, finish]);

  if (status === 'loading') {
    return (
      <div className="h-[100dvh] flex items-center justify-center bg-white">
        <Loader2 className="animate-spin text-gray-400" size={40} />
      </div>
    );
  }
  if (status === 'missing' || !step) {
    return (
      <div className="h-[100dvh] flex items-center justify-center bg-gray-50 px-4 text-center">
        <div>
          <h1 className="text-2xl font-black text-gray-900 mb-2">Funil não encontrado</h1>
          <p className="text-gray-500">Este link não está mais disponível.</p>
        </div>
      </div>
    );
  }

  const total = data.steps.length;
  return (
    <div className="h-[100dvh] flex flex-col bg-white">
      {previewVariant && (
        <div className="flex items-center justify-between gap-2 bg-slate-900 text-white text-sm px-4 py-2">
          <span className="font-bold">Prévia — nada é registrado</span>
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => setIndex((i) => Math.max(0, i - 1))} className="px-2 py-1 rounded bg-white/10">‹</button>
            <span>{index + 1} / {total}</span>
            <button type="button" onClick={() => setIndex((i) => Math.min(total - 1, i + 1))} className="px-2 py-1 rounded bg-white/10">›</button>
          </div>
        </div>
      )}
      <FunnelStepFrame
        key={`${index}-${step.position}`}
        html={step.html}
        trackingHtml={data.tracking_html}
        title={`Etapa ${index + 1} de ${total}`}
        onAction={onAction}
        className="flex-1"
      />
    </div>
  );
}
```

- [ ] **Step 5: Add the route**

Em `src/App.jsx`, junto dos outros `React.lazy`:

```jsx
const FunnelPlayer = React.lazy(() => import('./pages/funnels/FunnelPlayer'));
```

e, dentro de `<Routes>`, logo antes de `<Route path="*" element={<NotFound />} />`:

```jsx
            <Route path="/f/:slug" element={<FunnelPlayer />} />
```

- [ ] **Step 6: Run tests**

Run: `npx vitest run src/test/funnelPlayer.test.jsx`
Expected: PASS (7 testes)

- [ ] **Step 7: Commit**

```bash
git add src/components/funnels/FunnelStepFrame.jsx src/pages/funnels/FunnelPlayer.jsx src/App.jsx src/test/funnelPlayer.test.jsx
git commit -m "feat(funnels): página pública /f/:slug com etapas em iframe isolado

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: `fsid` no cadastro e *claim* no checkout

**Files:**
- Modify: `src/pages/auth/Register.jsx` (`onSubmit`, ~linha 44)
- Modify: `src/pages/auth/Plans.jsx` (novo `useEffect` perto da linha 85)
- Test: `src/test/funnelCheckoutLink.test.jsx`

**Interfaces:**
- Consumes: `readCheckoutFsid`, `rememberCheckoutFsid`, `claimFunnelSession`, `clearCheckoutFsid` (Task 12); `registerUser(userData)` do `useAuth`.

- [ ] **Step 1: Write the failing test**

```jsx
// src/test/funnelCheckoutLink.test.jsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Register from '../pages/auth/Register';
import { resolveCheckoutFsid } from '../pages/auth/Plans';

const registerUser = vi.fn().mockResolvedValue({ id: 'u1' });
vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({ registerUser, login: vi.fn().mockResolvedValue(undefined), user: null, loading: false }),
}));
vi.mock('../lib/api', () => ({ default: { post: vi.fn().mockResolvedValue({ data: {} }), get: vi.fn() } }));
vi.mock('../lib/analytics', () => ({ track: vi.fn() }));
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

async function fillAndSubmit() {
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText('Seu nome'), 'Ana Souza');
  await user.type(screen.getByPlaceholderText('seu@email.com'), 'ana@t.com');
  await user.type(screen.getByPlaceholderText('Mínimo 6 caracteres'), 'segredo123');
  await user.click(screen.getByRole('checkbox')); // aceite dos Termos (obrigatório no schema)
  await user.click(screen.getByRole('button', { name: /começar meu teste grátis/i }));
}

describe('funnel session through checkout', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it('sends funnel_session_id from the url on register', async () => {
    render(<MemoryRouter initialEntries={['/register?plan=p1&fsid=fs-9']}><Register /></MemoryRouter>);
    await fillAndSubmit();
    await waitFor(() => expect(registerUser).toHaveBeenCalledWith(expect.objectContaining({ funnel_session_id: 'fs-9' })));
  });

  it('falls back to the fsid stored in sessionStorage', async () => {
    sessionStorage.setItem('funnel_fsid', 'fs-7');
    render(<MemoryRouter initialEntries={['/register']}><Register /></MemoryRouter>);
    await fillAndSubmit();
    await waitFor(() => expect(registerUser).toHaveBeenCalledWith(expect.objectContaining({ funnel_session_id: 'fs-7' })));
  });

  it('omits funnel_session_id when there is none', async () => {
    render(<MemoryRouter initialEntries={['/register']}><Register /></MemoryRouter>);
    await fillAndSubmit();
    await waitFor(() => expect(registerUser).toHaveBeenCalled());
    expect(registerUser.mock.calls[0][0]).not.toHaveProperty('funnel_session_id');
  });

  it('resolves checkout fsid from url first, then storage', () => {
    sessionStorage.setItem('funnel_fsid', 'stored');
    expect(resolveCheckoutFsid('?fsid=url')).toBe('url');
    expect(resolveCheckoutFsid('')).toBe('stored');
    sessionStorage.clear();
    expect(resolveCheckoutFsid('')).toBeNull();
  });
});
```

> Os seletores acima usam os textos atuais de `Register.jsx` (placeholders, checkbox de Termos e botão "Começar meu teste grátis"); o teste não exige mudança de UI no cadastro.

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/test/funnelCheckoutLink.test.jsx`
Expected: FAIL — `resolveCheckoutFsid` não exportado e `funnel_session_id` ausente.

- [ ] **Step 3: Update `Register.jsx`**

Adicione o import:

```jsx
import { readCheckoutFsid, rememberCheckoutFsid } from '../../lib/funnels';
```

E no começo de `onSubmit`, troque a montagem de `userData`:

```jsx
    const fsid = searchParams.get('fsid') || readCheckoutFsid();
    if (fsid) rememberCheckoutFsid(fsid);
    const userData = { name: data.name, email: data.email, ...(fsid ? { funnel_session_id: fsid } : {}) };
```

- [ ] **Step 4: Update `Plans.jsx`**

Adicione os imports (`useLocation` junto de `useNavigate`):

```jsx
import { useLocation, useNavigate } from 'react-router-dom';
import { claimFunnelSession, readCheckoutFsid, rememberCheckoutFsid } from '../../lib/funnels';
```

Exporte o helper (fora do componente, antes do `export default`):

```jsx
export function resolveCheckoutFsid(search) {
  return new URLSearchParams(search).get('fsid') || readCheckoutFsid();
}
```

E dentro do componente, após os hooks existentes:

```jsx
  const location = useLocation();
  useEffect(() => {
    if (!user) return;
    const fsid = resolveCheckoutFsid(location.search);
    if (!fsid) return;
    rememberCheckoutFsid(fsid);
    claimFunnelSession(fsid);
  }, [user, location.search]);
```

(`user` vem do `useAuth()` já usado em `Plans.jsx`; confira o nome da variável no arquivo.) O *claim* é idempotente no backend: se a sessão já foi ligada no cadastro, ele só devolve `linked: false`.

- [ ] **Step 5: Run tests (novos + existentes das telas tocadas)**

Run: `npx vitest run src/test/funnelCheckoutLink.test.jsx src/test`
Expected: PASS em tudo.

- [ ] **Step 6: Commit**

```bash
git add src/pages/auth/Register.jsx src/pages/auth/Plans.jsx src/test/funnelCheckoutLink.test.jsx
git commit -m "feat(funnels): leva a sessão de funil pelo cadastro e checkout

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Admin — lista de funis, rotas e menu

**Files:**
- Create: `src/pages/admin/funnels/FunnelsList.jsx`
- Modify: `src/App.jsx` (rotas `funnels` e `funnels/:funnelId` dentro de `/admin`)
- Modify: `src/components/layout/SaaSLayout.jsx` (item de menu, linha ~24)
- Test: `src/test/adminFunnelsList.test.jsx`

**Interfaces:**
- Consumes: API `GET/POST /admin/funnels`, `POST /admin/funnels/{id}/duplicate|archive` (Task 9).
- Produces: página default `FunnelsList`; rotas `/admin/funnels` e `/admin/funnels/:funnelId` (o editor é a Task 16 — nesta task a rota do editor aponta para o componente criado na Task 16, então crie um stub mínimo `FunnelEditor.jsx` que a Task 16 substitui).

- [ ] **Step 1: Write the failing test**

```jsx
// src/test/adminFunnelsList.test.jsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FunnelsList from '../pages/admin/funnels/FunnelsList';
import api from '../lib/api';

const navigate = vi.fn();
vi.mock('../lib/api', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

const row = { id: 'f1', slug: 'oferta', name: 'Oferta BF', status: 'published', plan_id: 'p1',
  variant_count: 2, sessions_30d: 200, paid_30d: 10, paid_conversion_30d: 0.05 };

describe('FunnelsList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.get.mockResolvedValue({ data: [row] });
  });

  it('lists funnels with 30-day kpis', async () => {
    render(<MemoryRouter><FunnelsList /></MemoryRouter>);
    expect(await screen.findByText('Oferta BF')).toBeInTheDocument();
    expect(screen.getByText('/f/oferta')).toBeInTheDocument();
    expect(screen.getByText('5,0%')).toBeInTheDocument();
    expect(screen.getByText(/publicado/i)).toBeInTheDocument();
  });

  it('creates a funnel and opens the editor', async () => {
    api.post.mockResolvedValue({ data: { funnel: { id: 'new-id' } } });
    const user = userEvent.setup();
    render(<MemoryRouter><FunnelsList /></MemoryRouter>);
    await screen.findByText('Oferta BF');
    await user.click(screen.getByRole('button', { name: /novo funil/i }));
    await user.type(screen.getByLabelText(/nome/i), 'Teste');
    await user.type(screen.getByLabelText(/slug/i), 'teste');
    await user.click(screen.getByRole('button', { name: /criar/i }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/funnels', { name: 'Teste', slug: 'teste' }));
    expect(navigate).toHaveBeenCalledWith('/admin/funnels/new-id');
  });

  it('duplicates a funnel', async () => {
    api.post.mockResolvedValue({ data: { funnel: { id: 'copy' } } });
    const user = userEvent.setup();
    render(<MemoryRouter><FunnelsList /></MemoryRouter>);
    await user.click(await screen.findByRole('button', { name: /duplicar oferta bf/i }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/funnels/f1/duplicate'));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/test/adminFunnelsList.test.jsx`
Expected: FAIL — import não resolvido.

- [ ] **Step 3: Write the list page**

```jsx
// src/pages/admin/funnels/FunnelsList.jsx
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { Archive, Copy, Plus } from 'lucide-react';
import api, { getApiErrorMessage } from '../../../lib/api';

export const STATUS_LABEL = { draft: 'Rascunho', published: 'Publicado', archived: 'Arquivado' };
export const formatPct = (value) => `${(value * 100).toFixed(1).replace('.', ',')}%`;

export default function FunnelsList() {
  const navigate = useNavigate();
  const [funnels, setFunnels] = useState([]);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: '', slug: '' });

  const load = useCallback(async () => {
    try {
      const { data } = await api.get('/admin/funnels');
      setFunnels(data);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Não foi possível carregar os funis.'));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const create = async (event) => {
    event.preventDefault();
    try {
      const { data } = await api.post('/admin/funnels', { name: form.name, slug: form.slug });
      navigate(`/admin/funnels/${data.funnel.id}`);
    } catch (error) {
      toast.error(error.response?.data?.detail?.message || getApiErrorMessage(error));
    }
  };

  const action = async (funnel, verb) => {
    try {
      await api.post(`/admin/funnels/${funnel.id}/${verb}`);
      toast.success(verb === 'duplicate' ? 'Funil duplicado.' : 'Funil arquivado.');
      load();
    } catch (error) {
      toast.error(getApiErrorMessage(error));
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-black text-gray-900">Funis de venda</h1>
        <button type="button" onClick={() => setCreating((v) => !v)}
          className="inline-flex items-center gap-2 rounded-lg bg-slate-900 text-white px-4 py-2 font-bold">
          <Plus size={16} /> Novo funil
        </button>
      </div>

      {creating && (
        <form onSubmit={create} className="bg-white border border-gray-200 rounded-xl p-4 grid gap-3 sm:grid-cols-3 sm:items-end">
          <label className="text-sm font-bold text-gray-700">Nome
            <input className="mt-1 w-full rounded-lg border px-3 py-2" value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          </label>
          <label className="text-sm font-bold text-gray-700">Slug
            <input className="mt-1 w-full rounded-lg border px-3 py-2 font-mono" value={form.slug}
              onChange={(e) => setForm({ ...form, slug: e.target.value.toLowerCase() })} required />
          </label>
          <button type="submit" className="rounded-lg bg-brand-yellow px-4 py-2 font-bold">Criar</button>
        </form>
      )}

      <div className="bg-white border border-gray-200 rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-gray-500">
            <tr>
              <th className="px-4 py-3">Funil</th><th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Variantes</th><th className="px-4 py-3">Sessões (30d)</th>
              <th className="px-4 py-3">Pagos (30d)</th><th className="px-4 py-3">Conversão</th><th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {funnels.map((f) => (
              <tr key={f.id} className="border-t hover:bg-gray-50 cursor-pointer" onClick={() => navigate(`/admin/funnels/${f.id}`)}>
                <td className="px-4 py-3"><div className="font-bold text-gray-900">{f.name}</div>
                  <div className="font-mono text-xs text-gray-500">/f/{f.slug}</div></td>
                <td className="px-4 py-3">{STATUS_LABEL[f.status]}</td>
                <td className="px-4 py-3">{f.variant_count}</td>
                <td className="px-4 py-3">{f.sessions_30d}</td>
                <td className="px-4 py-3">{f.paid_30d}</td>
                <td className="px-4 py-3">{formatPct(f.paid_conversion_30d)}</td>
                <td className="px-4 py-3 text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                  <button type="button" aria-label={`Duplicar ${f.name}`} onClick={() => action(f, 'duplicate')} className="p-2 text-gray-500 hover:text-gray-900"><Copy size={16} /></button>
                  {f.status !== 'archived' && (
                    <button type="button" aria-label={`Arquivar ${f.name}`} onClick={() => action(f, 'archive')} className="p-2 text-gray-500 hover:text-gray-900"><Archive size={16} /></button>
                  )}
                </td>
              </tr>
            ))}
            {funnels.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-8 text-center text-gray-500">Nenhum funil ainda.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create the editor stub, routes and menu item**

`src/pages/admin/funnels/FunnelEditor.jsx` (stub, substituído na Task 16):

```jsx
export default function FunnelEditor() {
  return null;
}
```

Em `src/App.jsx`, junto dos lazy imports do admin:

```jsx
const FunnelsList = React.lazy(() => import('./pages/admin/funnels/FunnelsList'));
const FunnelEditor = React.lazy(() => import('./pages/admin/funnels/FunnelEditor'));
```

e dentro de `<Route path="/admin" ...>`, após `tickets`:

```jsx
              <Route path="funnels" element={<FunnelsList />} />
              <Route path="funnels/:funnelId" element={<FunnelEditor />} />
```

Em `src/components/layout/SaaSLayout.jsx`, importe `Filter` de `lucide-react` e adicione ao array de menu, após "Gestão de Planos":

```jsx
    { icon: Filter, label: 'Funis', path: '/admin/funnels' },
```

- [ ] **Step 5: Run tests**

Run: `npx vitest run src/test/adminFunnelsList.test.jsx`
Expected: PASS (3 testes)

- [ ] **Step 6: Commit**

```bash
git add src/pages/admin/funnels/FunnelsList.jsx src/pages/admin/funnels/FunnelEditor.jsx src/App.jsx src/components/layout/SaaSLayout.jsx src/test/adminFunnelsList.test.jsx
git commit -m "feat(funnels): lista de funis no admin

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Admin — editor (Configuração e Etapas com prévia ao vivo)

**Files:**
- Modify (substitui o stub): `src/pages/admin/funnels/FunnelEditor.jsx`
- Create: `src/components/admin/funnels/FunnelConfigTab.jsx`
- Create: `src/components/admin/funnels/FunnelStepsTab.jsx`
- Test: `src/test/adminFunnelEditor.test.jsx`

**Interfaces:**
- Consumes: API de detalhe/edição da Task 9; `usePublicPlans` (hook existente que lista planos públicos — confira o retorno em `src/hooks/usePublicPlans.js`; se ele não trouxer o plano `id`/`name`, use `api.get('/admin/plans')`, já usado por `PlansManagement`); `FunnelStepFrame` (Task 13); `STATUS_LABEL` (Task 15).
- Produces: `<FunnelConfigTab detail onSaved />`, `<FunnelStepsTab detail onChanged />`; `FunnelEditor` com abas `config | steps | metrics` (a aba métricas renderiza `FunnelMetricsTab`, criado na Task 17 — nesta task ela mostra "Em breve" e a Task 17 a liga).

- [ ] **Step 1: Write the failing test**

```jsx
// src/test/adminFunnelEditor.test.jsx
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FunnelEditor from '../pages/admin/funnels/FunnelEditor';
import api from '../lib/api';

vi.mock('../lib/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn() },
  getApiErrorMessage: (e, fallback) => e?.response?.data?.detail?.message || fallback || 'erro',
}));
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }));
import toast from 'react-hot-toast';

const detail = {
  funnel: { id: 'f1', slug: 'oferta', name: 'Oferta', status: 'draft', plan_id: 'p1', tracking_html: '' },
  variants: [
    { id: 'v1', name: 'A', weight: 100, is_active: true, position: 0,
      steps: [{ id: 's1', position: 0, name: 'Intro', html: '<p>Intro</p>' },
              { id: 's2', position: 1, name: 'Oferta', html: '<p>Oferta</p>' }] },
  ],
  session_count: 0,
};

function renderEditor() {
  return render(
    <MemoryRouter initialEntries={['/admin/funnels/f1']}>
      <Routes><Route path="/admin/funnels/:funnelId" element={<FunnelEditor />} /></Routes>
    </MemoryRouter>,
  );
}

describe('FunnelEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.get.mockImplementation((url) => {
      if (url === '/admin/funnels/f1') return Promise.resolve({ data: detail });
      if (url === '/admin/plans') return Promise.resolve({ data: [{ id: 'p1', name: 'Pro', is_active: true }] });
      return Promise.resolve({ data: {} });
    });
    api.patch.mockResolvedValue({ data: { step: detail.variants[0].steps[0], warnings: [] } });
    api.post.mockResolvedValue({ data: detail });
    api.put.mockResolvedValue({ data: { warnings: ['Este funil já tem tráfego: alterar etapas afeta as métricas por etapa.'] } });
  });

  it('saves config including tracking html', async () => {
    const user = userEvent.setup();
    renderEditor();
    const tracking = await screen.findByLabelText(/scripts de rastreamento/i);
    await user.type(tracking, '<script>px()</script>');
    await user.click(screen.getByRole('button', { name: /salvar configuração/i }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/admin/funnels/f1', expect.objectContaining({
      name: 'Oferta', slug: 'oferta', plan_id: 'p1', tracking_html: '<script>px()</script>',
    })));
  });

  it('shows publish errors from the api', async () => {
    api.post.mockRejectedValueOnce({ response: { status: 422, data: { detail: {
      code: 'funnel.publish_invalid', message: 'O funil não pode ser publicado.', errors: ['A variante \'A\' não tem etapas.'],
    } } } });
    const user = userEvent.setup();
    renderEditor();
    await user.click(await screen.findByRole('button', { name: /^publicar$/i }));
    expect(await screen.findByText("A variante 'A' não tem etapas.")).toBeInTheDocument();
  });

  it('edits step html with live preview and saves it', async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.click(await screen.findByRole('tab', { name: /etapas/i }));
    const editor = await screen.findByLabelText(/html da etapa/i);
    expect(screen.getByTitle(/prévia da etapa/i).getAttribute('srcdoc')).toContain('<p>Intro</p>');
    await user.clear(editor);
    await user.type(editor, '<h1>Novo</h1>');
    expect(screen.getByTitle(/prévia da etapa/i).getAttribute('srcdoc')).toContain('<h1>Novo</h1>');
    await user.click(screen.getByRole('button', { name: /salvar etapa/i }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/admin/funnels/steps/s1', { name: 'Intro', html: '<h1>Novo</h1>' }));
  });

  it('reorders steps and surfaces warnings', async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.click(await screen.findByRole('tab', { name: /etapas/i }));
    const list = await screen.findByRole('list', { name: /etapas da variante/i });
    await user.click(within(list).getByRole('button', { name: /mover oferta para cima/i }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/admin/funnels/variants/v1/steps/order', { step_ids: ['s2', 's1'] }));
    expect(toast.error).not.toHaveBeenCalled();
    expect(await screen.findByText(/já tem tráfego/i)).toBeInTheDocument();
  });

  it('changes a variant weight', async () => {
    api.patch.mockResolvedValueOnce({ data: { variant: { ...detail.variants[0], weight: 50 }, warnings: [] } });
    const user = userEvent.setup();
    renderEditor();
    await user.click(await screen.findByRole('tab', { name: /etapas/i }));
    const weight = await screen.findByLabelText(/peso da variante a/i);
    await user.clear(weight);
    await user.type(weight, '50');
    await user.tab();
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/admin/funnels/variants/v1', { weight: 50 }));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/test/adminFunnelEditor.test.jsx`
Expected: FAIL (stub não renderiza nada).

- [ ] **Step 3: Write `FunnelConfigTab`**

```jsx
// src/components/admin/funnels/FunnelConfigTab.jsx
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import api, { getApiErrorMessage } from '../../../lib/api';

export default function FunnelConfigTab({ detail, onSaved }) {
  const { funnel } = detail;
  const [plans, setPlans] = useState([]);
  const [form, setForm] = useState({
    name: funnel.name, slug: funnel.slug, plan_id: funnel.plan_id || '', tracking_html: funnel.tracking_html || '',
  });

  useEffect(() => {
    api.get('/admin/plans').then(({ data }) => setPlans(data.filter((p) => p.is_active))).catch(() => setPlans([]));
  }, []);

  const save = async (event) => {
    event.preventDefault();
    try {
      await api.patch(`/admin/funnels/${funnel.id}`, { ...form, plan_id: form.plan_id || null });
      toast.success('Configuração salva.');
      onSaved();
    } catch (error) {
      toast.error(getApiErrorMessage(error));
    }
  };

  const field = 'mt-1 w-full rounded-lg border px-3 py-2';
  return (
    <form onSubmit={save} className="bg-white border border-gray-200 rounded-xl p-6 grid gap-4 max-w-3xl">
      <label className="text-sm font-bold text-gray-700">Nome
        <input className={field} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
      </label>
      <label className="text-sm font-bold text-gray-700">Slug
        <input className={`${field} font-mono`} value={form.slug}
          onChange={(e) => setForm({ ...form, slug: e.target.value.toLowerCase() })} required />
        <span className="block text-xs font-normal text-gray-500 mt-1">URL pública: /f/{form.slug}</span>
      </label>
      <label className="text-sm font-bold text-gray-700">Plano de destino
        <select className={field} value={form.plan_id} onChange={(e) => setForm({ ...form, plan_id: e.target.value })}>
          <option value="">Selecione…</option>
          {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>
      <label className="text-sm font-bold text-gray-700">Scripts de rastreamento
        <textarea className={`${field} font-mono text-xs h-40`} value={form.tracking_html}
          onChange={(e) => setForm({ ...form, tracking_html: e.target.value })}
          placeholder="Pixels (Meta, Google Ads…). Rodam isolados dentro de cada etapa." />
      </label>
      <div><button type="submit" className="rounded-lg bg-slate-900 text-white px-4 py-2 font-bold">Salvar configuração</button></div>
    </form>
  );
}
```

- [ ] **Step 4: Write `FunnelStepsTab`**

```jsx
// src/components/admin/funnels/FunnelStepsTab.jsx
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { ArrowDown, ArrowUp, Copy, ExternalLink, Plus, Trash2 } from 'lucide-react';
import api, { getApiErrorMessage } from '../../../lib/api';
import FunnelStepFrame from '../../funnels/FunnelStepFrame';

export default function FunnelStepsTab({ detail, onChanged, onWarnings }) {
  const { funnel, variants } = detail;
  const [variantId, setVariantId] = useState(variants[0]?.id);
  const variant = variants.find((v) => v.id === variantId) || variants[0];
  const [stepId, setStepId] = useState(variant?.steps[0]?.id);
  const step = variant?.steps.find((s) => s.id === stepId) || variant?.steps[0];
  const [draft, setDraft] = useState({ name: step?.name || '', html: step?.html || '' });

  useEffect(() => { setDraft({ name: step?.name || '', html: step?.html || '' }); }, [step?.id, step?.name, step?.html]);

  const call = async (promise, success) => {
    try {
      const { data } = await promise;
      if (data?.warnings?.length) onWarnings(data.warnings);
      if (success) toast.success(success);
      onChanged();
      return data;
    } catch (error) {
      toast.error(getApiErrorMessage(error));
      return null;
    }
  };

  const move = (index, delta) => {
    const ids = variant.steps.map((s) => s.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    call(api.put(`/admin/funnels/variants/${variant.id}/steps/order`, { step_ids: ids }));
  };

  const addStep = async () => {
    const data = await call(api.post(`/admin/funnels/variants/${variant.id}/steps`, {
      name: `Etapa ${variant.steps.length + 1}`, html: '<button data-funnel-next>Continuar</button>',
    }));
    if (data?.step) setStepId(data.step.id);
  };

  if (!variant) return null;
  return (
    <div className="space-y-4">
      <div className="bg-white border border-gray-200 rounded-xl p-4 flex flex-wrap items-center gap-3">
        {variants.map((v) => (
          <div key={v.id} className={`flex items-center gap-2 rounded-lg border px-3 py-2 ${v.id === variant.id ? 'border-slate-900' : ''}`}>
            <button type="button" className="font-bold" onClick={() => { setVariantId(v.id); setStepId(v.steps[0]?.id); }}>{v.name}</button>
            <label className="text-xs text-gray-500">Peso
              <input aria-label={`Peso da variante ${v.name}`} type="number" min="0" defaultValue={v.weight}
                className="ml-1 w-16 rounded border px-1"
                onBlur={(e) => {
                  const weight = Number(e.target.value);
                  if (Number.isInteger(weight) && weight >= 0 && weight !== v.weight) {
                    call(api.patch(`/admin/funnels/variants/${v.id}`, { weight }));
                  }
                }} />
            </label>
            <label className="text-xs text-gray-500 flex items-center gap-1">
              <input type="checkbox" aria-label={`Variante ${v.name} ativa`} checked={v.is_active}
                onChange={(e) => call(api.patch(`/admin/funnels/variants/${v.id}`, { is_active: e.target.checked }))} /> ativa
            </label>
          </div>
        ))}
        <button type="button" onClick={() => call(api.post(`/admin/funnels/variants/${variant.id}/duplicate`), 'Variante duplicada (peso 0).')}
          className="inline-flex items-center gap-1 text-sm font-bold text-gray-600"><Copy size={14} /> Duplicar variante</button>
        <button type="button" onClick={() => call(api.post(`/admin/funnels/${funnel.id}/variants`, { name: String.fromCharCode(65 + variants.length), weight: 0 }), 'Variante criada.')}
          className="inline-flex items-center gap-1 text-sm font-bold text-gray-600"><Plus size={14} /> Nova variante</button>
        {variants.length > 1 && (
          <button type="button" onClick={() => call(api.delete(`/admin/funnels/variants/${variant.id}`), 'Variante removida.')}
            className="inline-flex items-center gap-1 text-sm font-bold text-red-600"><Trash2 size={14} /> Remover variante</button>
        )}
        <a href={`/f/${funnel.slug}?preview=${variant.id}`} target="_blank" rel="noreferrer"
          className="ml-auto inline-flex items-center gap-1 text-sm font-bold text-slate-900"><ExternalLink size={14} /> Abrir prévia completa</a>
      </div>

      <div className="grid gap-4 lg:grid-cols-[240px_1fr_1fr]">
        <div className="bg-white border border-gray-200 rounded-xl p-3">
          <ul aria-label="Etapas da variante" className="space-y-1">
            {variant.steps.map((s, i) => (
              <li key={s.id} className={`flex items-center gap-1 rounded-lg px-2 py-1 ${s.id === step?.id ? 'bg-gray-100' : ''}`}>
                <button type="button" className="flex-1 text-left text-sm" onClick={() => setStepId(s.id)}>{i + 1}. {s.name}</button>
                <button type="button" aria-label={`Mover ${s.name} para cima`} onClick={() => move(i, -1)} className="p-1 text-gray-500"><ArrowUp size={14} /></button>
                <button type="button" aria-label={`Mover ${s.name} para baixo`} onClick={() => move(i, 1)} className="p-1 text-gray-500"><ArrowDown size={14} /></button>
                <button type="button" aria-label={`Excluir ${s.name}`} onClick={() => call(api.delete(`/admin/funnels/steps/${s.id}`))} className="p-1 text-red-500"><Trash2 size={14} /></button>
              </li>
            ))}
          </ul>
          <button type="button" onClick={addStep} className="mt-2 inline-flex items-center gap-1 text-sm font-bold text-gray-600"><Plus size={14} /> Adicionar etapa</button>
        </div>

        {step ? (
          <div className="bg-white border border-gray-200 rounded-xl p-4 flex flex-col gap-3">
            <label className="text-sm font-bold text-gray-700">Nome da etapa
              <input className="mt-1 w-full rounded-lg border px-3 py-2" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </label>
            <label className="text-sm font-bold text-gray-700 flex-1 flex flex-col">HTML da etapa
              <textarea className="mt-1 flex-1 min-h-[320px] w-full rounded-lg border px-3 py-2 font-mono text-xs" spellCheck={false}
                value={draft.html} onChange={(e) => setDraft({ ...draft, html: e.target.value })} />
            </label>
            <p className="text-xs text-gray-500">Use <code>data-funnel-next</code>, <code>data-funnel-back</code> e <code>data-funnel-finish</code> nos botões, ou <code>Funnel.next()</code> no JS.</p>
            <div><button type="button" className="rounded-lg bg-slate-900 text-white px-4 py-2 font-bold"
              onClick={() => call(api.patch(`/admin/funnels/steps/${step.id}`, draft), 'Etapa salva.')}>Salvar etapa</button></div>
          </div>
        ) : <div className="text-gray-500 p-4">Adicione a primeira etapa.</div>}

        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden min-h-[420px] flex">
          {step && (
            <FunnelStepFrame html={draft.html} trackingHtml="" title="Prévia da etapa"
              onAction={(a) => toast.success(`Ação disparada: ${a}`)} className="flex-1" />
          )}
        </div>
      </div>
    </div>
  );
}
```

> A prévia ao vivo usa `trackingHtml=""` de propósito: pixels não disparam enquanto o admin edita.

- [ ] **Step 5: Write `FunnelEditor`**

```jsx
// src/pages/admin/funnels/FunnelEditor.jsx
import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import api, { getApiErrorMessage } from '../../../lib/api';
import FunnelConfigTab from '../../../components/admin/funnels/FunnelConfigTab';
import FunnelStepsTab from '../../../components/admin/funnels/FunnelStepsTab';
import { STATUS_LABEL } from './FunnelsList';

const TABS = [['config', 'Configuração'], ['steps', 'Etapas'], ['metrics', 'Métricas']];

export default function FunnelEditor() {
  const { funnelId } = useParams();
  const [detail, setDetail] = useState(null);
  const [tab, setTab] = useState('config');
  const [warnings, setWarnings] = useState([]);
  const [publishErrors, setPublishErrors] = useState([]);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/admin/funnels/${funnelId}`);
      setDetail(data);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Não foi possível carregar o funil.'));
    }
  }, [funnelId]);

  useEffect(() => { load(); }, [load]);

  const transition = async (verb) => {
    setPublishErrors([]);
    try {
      const { data } = await api.post(`/admin/funnels/${funnelId}/${verb}`);
      setDetail(data);
      toast.success(verb === 'publish' ? 'Funil publicado.' : 'Status atualizado.');
    } catch (error) {
      const errors = error.response?.data?.detail?.errors;
      if (errors?.length) setPublishErrors(errors);
      else toast.error(getApiErrorMessage(error));
    }
  };

  if (!detail) return null;
  const { funnel } = detail;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/admin/funnels" className="text-sm text-gray-500">← Funis</Link>
        <h1 className="text-2xl font-black text-gray-900">{funnel.name}</h1>
        <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-bold">{STATUS_LABEL[funnel.status]}</span>
        <div className="ml-auto flex gap-2">
          {funnel.status !== 'published'
            ? <button type="button" onClick={() => transition('publish')} className="rounded-lg bg-green-600 text-white px-4 py-2 font-bold">Publicar</button>
            : <button type="button" onClick={() => transition('unpublish')} className="rounded-lg border px-4 py-2 font-bold">Despublicar</button>}
        </div>
      </div>

      {publishErrors.length > 0 && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <p className="font-bold mb-1">O funil não pode ser publicado:</p>
          <ul className="list-disc pl-5">{publishErrors.map((e) => <li key={e}>{e}</li>)}</ul>
        </div>
      )}
      {warnings.length > 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 flex justify-between gap-4">
          <ul>{warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          <button type="button" onClick={() => setWarnings([])} className="font-bold">OK</button>
        </div>
      )}

      <div role="tablist" className="flex gap-2 border-b">
        {TABS.map(([key, label]) => (
          <button key={key} role="tab" type="button" aria-selected={tab === key} onClick={() => setTab(key)}
            className={`px-4 py-2 font-bold ${tab === key ? 'border-b-2 border-slate-900 text-slate-900' : 'text-gray-500'}`}>{label}</button>
        ))}
      </div>

      {tab === 'config' && <FunnelConfigTab detail={detail} onSaved={load} />}
      {tab === 'steps' && <FunnelStepsTab detail={detail} onChanged={load} onWarnings={setWarnings} />}
      {tab === 'metrics' && <p className="text-gray-500">Em breve.</p>}
    </div>
  );
}
```

- [ ] **Step 6: Run tests**

Run: `npx vitest run src/test/adminFunnelEditor.test.jsx src/test/adminFunnelsList.test.jsx`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/pages/admin/funnels/FunnelEditor.jsx src/components/admin/funnels/FunnelConfigTab.jsx src/components/admin/funnels/FunnelStepsTab.jsx src/test/adminFunnelEditor.test.jsx
git commit -m "feat(funnels): editor de funis com variantes, etapas e prévia ao vivo

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: Admin — aba Métricas

**Files:**
- Create: `src/components/admin/funnels/FunnelMetricsTab.jsx`
- Modify: `src/pages/admin/funnels/FunnelEditor.jsx` (troca o "Em breve")
- Test: `src/test/adminFunnelMetrics.test.jsx`

**Interfaces:**
- Consumes: `GET /admin/funnels/{id}/metrics` (formato da Task 8 + `period`, `maturing`); `formatPct` (Task 15); `recharts`.
- Produces: `<FunnelMetricsTab detail />`.

- [ ] **Step 1: Write the failing test**

```jsx
// src/test/adminFunnelMetrics.test.jsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FunnelMetricsTab from '../components/admin/funnels/FunnelMetricsTab';
import api from '../lib/api';

vi.mock('../lib/api', () => ({ default: { get: vi.fn() }, getApiErrorMessage: () => 'erro' }));
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn() } }));
vi.mock('recharts', async () => {
  const actual = await vi.importActual('recharts');
  return { ...actual, ResponsiveContainer: ({ children }) => <div style={{ width: 600, height: 240 }}>{children}</div> };
});

const detail = { funnel: { id: 'f1' }, variants: [{ id: 'v1', name: 'A' }, { id: 'v2', name: 'B' }] };
const metrics = {
  period: { from: '2026-09-05', to: '2026-10-04' }, maturing: true,
  summary: { sessions: 400, completed: 120, registered: 60, trials: 55, subscribed: 20, paid: 12, paid_conversion: 0.03, revenue: '1198.80' },
  steps: [
    { key: 'step:0', label: 'Intro', count: 400, pct_of_total: 1, drop_from_previous: null },
    { key: 'step:1', label: 'Oferta', count: 200, pct_of_total: 0.5, drop_from_previous: 0.5 },
    { key: 'paid', label: 'Pagamento', count: 12, pct_of_total: 0.03, drop_from_previous: 0.4 },
  ],
  variants: [
    { variant_id: 'v1', name: 'A', weight: 50, sessions: 200, registered: 30, paid: 4, conversion: 0.02, revenue_per_session: '2.00', confidence: null, low_sample: false, is_control: true },
    { variant_id: 'v2', name: 'B', weight: 50, sessions: 200, registered: 30, paid: 8, conversion: 0.04, revenue_per_session: '4.00', confidence: null, low_sample: true, is_control: false },
  ],
  sources: [{ utm_source: null, utm_campaign: null, sessions: 400, registered: 60, paid: 12, conversion: 0.03 }],
  timeseries: [{ date: '2026-10-01', sessions: 10, paid: 1 }],
};

describe('FunnelMetricsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.get.mockResolvedValue({ data: metrics });
  });

  it('renders summary, steps, variants and sources', async () => {
    render(<FunnelMetricsTab detail={detail} />);
    expect(await screen.findByText('400')).toBeInTheDocument();
    expect(screen.getByText('3,0%')).toBeInTheDocument();
    expect(screen.getByText(/R\$\s?1\.198,80/)).toBeInTheDocument();
    expect(screen.getByText('Oferta')).toBeInTheDocument();
    expect(screen.getByText('-50,0%')).toBeInTheDocument();
    expect(screen.getByText(/amostra pequena/i)).toBeInTheDocument();
    expect(screen.getByText(/controle/i)).toBeInTheDocument();
    expect(screen.getByText(/direto \/ sem origem/i)).toBeInTheDocument();
    expect(screen.getByText(/amadurecendo/i)).toBeInTheDocument();
  });

  it('refetches with filters', async () => {
    const user = userEvent.setup();
    render(<FunnelMetricsTab detail={detail} />);
    await screen.findByText('400');
    await user.selectOptions(screen.getByLabelText(/variante/i), 'v2');
    await waitFor(() => expect(api.get).toHaveBeenLastCalledWith('/admin/funnels/f1/metrics', {
      params: expect.objectContaining({ variant_id: 'v2' }),
    }));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/test/adminFunnelMetrics.test.jsx`
Expected: FAIL — import não resolvido.

- [ ] **Step 3: Write the metrics tab**

```jsx
// src/components/admin/funnels/FunnelMetricsTab.jsx
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import api, { getApiErrorMessage } from '../../../lib/api';
import { formatCurrency } from '../../../lib/utils';
import { formatPct } from '../../../pages/admin/funnels/FunnelsList';

const Card = ({ label, value }) => (
  <div className="bg-white border border-gray-200 rounded-xl p-4">
    <div className="text-xs font-bold uppercase text-gray-500">{label}</div>
    <div className="text-2xl font-black text-gray-900 mt-1">{value}</div>
  </div>
);

export default function FunnelMetricsTab({ detail }) {
  const [filters, setFilters] = useState({ from: '', to: '', variant_id: '', utm_source: '', utm_campaign: '' });
  const [data, setData] = useState(null);

  useEffect(() => {
    const params = Object.fromEntries(Object.entries(filters).filter(([, v]) => v));
    api.get(`/admin/funnels/${detail.funnel.id}/metrics`, { params })
      .then(({ data: result }) => setData(result))
      .catch((error) => toast.error(getApiErrorMessage(error, 'Não foi possível carregar as métricas.')));
  }, [detail.funnel.id, filters]);

  const set = (key) => (e) => setFilters((f) => ({ ...f, [key]: e.target.value }));
  const input = 'mt-1 rounded-lg border px-2 py-1';

  return (
    <div className="space-y-6">
      <div className="bg-white border border-gray-200 rounded-xl p-4 flex flex-wrap gap-3 items-end text-sm">
        <label className="font-bold text-gray-700">De<input type="date" className={`${input} block`} value={filters.from} onChange={set('from')} /></label>
        <label className="font-bold text-gray-700">Até<input type="date" className={`${input} block`} value={filters.to} onChange={set('to')} /></label>
        <label className="font-bold text-gray-700">Variante
          <select className={`${input} block`} value={filters.variant_id} onChange={set('variant_id')}>
            <option value="">Todas</option>
            {detail.variants.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
          </select>
        </label>
        <label className="font-bold text-gray-700">utm_source<input className={`${input} block`} value={filters.utm_source} onChange={set('utm_source')} /></label>
        <label className="font-bold text-gray-700">utm_campaign<input className={`${input} block`} value={filters.utm_campaign} onChange={set('utm_campaign')} /></label>
      </div>

      {data && (
        <>
          {data.maturing && (
            <p className="text-xs text-amber-700">Coortes recentes ainda estão amadurecendo: conversões podem acontecer depois do período.</p>
          )}
          <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
            <Card label="Sessões" value={data.summary.sessions} />
            <Card label="Cadastros" value={data.summary.registered} />
            <Card label="Pagamentos" value={data.summary.paid} />
            <Card label="Conversão em pagamento" value={formatPct(data.summary.paid_conversion)} />
            <Card label="Concluíram" value={data.summary.completed} />
            <Card label="Trials" value={data.summary.trials} />
            <Card label="Assinaturas" value={data.summary.subscribed} />
            <Card label="Receita" value={formatCurrency(Number(data.summary.revenue))} />
          </div>

          <section className="bg-white border border-gray-200 rounded-xl p-4">
            <h2 className="font-black text-gray-900 mb-3">Funil etapa a etapa</h2>
            <div className="space-y-2">
              {data.steps.map((s) => (
                <div key={s.key} className="grid grid-cols-[160px_1fr_120px] items-center gap-3 text-sm">
                  <span className="font-bold text-gray-700 truncate">{s.label}</span>
                  <div className="h-6 rounded bg-gray-100 overflow-hidden">
                    <div className="h-full bg-slate-900" style={{ width: `${Math.max(1, s.pct_of_total * 100)}%` }} />
                  </div>
                  <span className="text-right text-gray-600">
                    {s.count} · {formatPct(s.pct_of_total)}
                    {s.drop_from_previous != null && <span className="block text-xs text-red-600">-{formatPct(s.drop_from_previous)}</span>}
                  </span>
                </div>
              ))}
            </div>
          </section>

          <section className="bg-white border border-gray-200 rounded-xl p-4 overflow-x-auto">
            <h2 className="font-black text-gray-900 mb-3">Comparação A/B</h2>
            <table className="w-full text-sm">
              <thead className="text-left text-gray-500"><tr>
                <th className="py-2">Variante</th><th>Peso</th><th>Sessões</th><th>Cadastros</th><th>Pagos</th>
                <th>Conversão</th><th>Receita/sessão</th><th>Confiança</th>
              </tr></thead>
              <tbody>
                {data.variants.map((v) => (
                  <tr key={v.variant_id} className="border-t">
                    <td className="py-2 font-bold">{v.name}{v.is_control && <span className="ml-2 text-xs text-gray-500">(controle)</span>}</td>
                    <td>{v.weight}</td><td>{v.sessions}</td><td>{v.registered}</td><td>{v.paid}</td>
                    <td>{formatPct(v.conversion)}</td><td>{formatCurrency(Number(v.revenue_per_session))}</td>
                    <td>{v.is_control ? '—' : v.low_sample ? <span className="text-amber-700">amostra pequena</span> : formatPct(v.confidence)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="bg-white border border-gray-200 rounded-xl p-4 overflow-x-auto">
            <h2 className="font-black text-gray-900 mb-3">Por origem</h2>
            <table className="w-full text-sm">
              <thead className="text-left text-gray-500"><tr>
                <th className="py-2">Origem</th><th>Campanha</th><th>Sessões</th><th>Cadastros</th><th>Pagos</th><th>Conversão</th>
              </tr></thead>
              <tbody>
                {data.sources.map((s) => (
                  <tr key={`${s.utm_source}-${s.utm_campaign}`} className="border-t">
                    <td className="py-2">{s.utm_source || 'direto / sem origem'}</td><td>{s.utm_campaign || '—'}</td>
                    <td>{s.sessions}</td><td>{s.registered}</td><td>{s.paid}</td><td>{formatPct(s.conversion)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="bg-white border border-gray-200 rounded-xl p-4">
            <h2 className="font-black text-gray-900 mb-3">Sessões e pagamentos por dia</h2>
            <div className="h-60">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={data.timeseries}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" fontSize={12} />
                  <YAxis allowDecimals={false} fontSize={12} />
                  <Tooltip />
                  <Legend />
                  <Line type="monotone" dataKey="sessions" name="Sessões" stroke="#0f172a" dot={false} />
                  <Line type="monotone" dataKey="paid" name="Pagamentos" stroke="#16a34a" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
```

> `formatCurrency` já existe em `src/lib/utils.js` (usado por `Plans.jsx`). Se o teste de receita falhar por formatação (`R$ 1.198,80` com espaço não separável), ajuste só a regex do teste para `/1\.198,80/`.

- [ ] **Step 4: Wire it in the editor**

Em `src/pages/admin/funnels/FunnelEditor.jsx`, importe e troque a linha da aba:

```jsx
import FunnelMetricsTab from '../../../components/admin/funnels/FunnelMetricsTab';
```
```jsx
      {tab === 'metrics' && <FunnelMetricsTab detail={detail} />}
```

- [ ] **Step 5: Run the whole frontend suite and lint**

Run: `npx vitest run`
Expected: PASS em tudo.

Run: `npm run lint`
Expected: sem erros nos arquivos novos (corrija avisos como `react-hooks/exhaustive-deps` nos arquivos da feature).

- [ ] **Step 6: Commit**

```bash
git add src/components/admin/funnels/FunnelMetricsTab.jsx src/pages/admin/funnels/FunnelEditor.jsx src/test/adminFunnelMetrics.test.jsx
git commit -m "feat(funnels): painel de métricas por etapa, variante e origem

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Verificação final e documentação

**Files:**
- Modify: `C:\Users\carlos\Documents\projetos\marketfy\OVERVIEW.md` e `backend/docs/OVERVIEW.md` (seção 1, nova linha na tabela "Para o operador da plataforma"; seção 4, tabelas de funis)

- [ ] **Step 1: Backend suite**

Run (backend): `python -m pytest tests/unit -q`
Expected: verde (compare com o baseline anterior à feature; nenhuma falha nova).

- [ ] **Step 2: Manual smoke (dev)**

1. `alembic upgrade head` no Postgres local; subir API (`python run.py`) e front (`npm run dev`).
2. Logar como admin → Funis → criar `teste`, plano Pro, 2 etapas com `<button data-funnel-next>Ir</button>` → Publicar.
3. Aba anônima: `http://localhost:3000/f/teste?utm_source=meta` → avançar até o fim → deve cair em `/register?plan=…&fsid=…`.
4. Concluir cadastro (trial automático) → no admin, Métricas: 1 sessão, 1 cadastro, 1 trial, origem `meta`.
5. No DevTools da aba do funil, rodar `document.querySelector('iframe').contentWindow.document` → deve lançar erro de cross-origin (prova do isolamento).

- [ ] **Step 3: Update OVERVIEW.md**

Na tabela "Para o operador da plataforma" (seção 1), acrescente à frase existente: `funis de venda (HTML por etapa, A/B, UTM) com métricas de conversão até o pagamento`. Na seção "Modelo de dados", adicione a linha: `- **Funis de venda:** funnels, funnel_variants, funnel_steps, funnel_sessions, funnel_events.` Copie o arquivo atualizado para o outro local (`OVERVIEW.md` na raiz ↔ `backend/docs/OVERVIEW.md`).

- [ ] **Step 4: Commit (backend)**

```bash
git add docs/OVERVIEW.md
git commit -m "docs: overview inclui funis de venda

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review (feito)

- **Cobertura da spec:** modelo (T2), sessão/variante fixa/UTM (T5, T13), iframe sandbox e protocolo (T12, T13), prévia sem eventos (T9, T13), rate limit e dedupe (T3, T9), editor e validações/avisos (T4, T15, T16), auditoria (T9), métricas por coorte/etapa/A-B/origem/série e exclusão de admin (T8, T9, T17), atribuição idempotente e à prova de falhas (T6, T7, T10), claim de usuário logado (T9, T14), migration em Postgres (T11). Itens fora de escopo da spec não têm tasks.
- **Placeholders:** nenhum "TBD"; os dois pontos que dependem de nomes locais (seletores do formulário de cadastro na T14 e o nome `user` em `Plans.jsx`) têm instrução explícita de conferência.
- **Consistência de tipos:** `FunnelRepository`, `FunnelAdminService`, `FunnelPublicService`, `FunnelAttributionService`, `FunnelTrackingAnalytics`, `SessionRow`, `aggregate_metrics` e os caminhos HTTP usados no frontend batem com as definições das tasks anteriores.
