# Funis A/B de Conversão — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persistir eventos anônimos de navegação e leads reais dos dois funis de conversão (`/funil-a`, `/funil-b`), e expor endpoints de admin com o funil de conversão por etapa (com taxa de queda) e a lista de leads capturados, por variante A/B.

**Architecture:** Duas tabelas novas (`marketing_funnel_events`, vocabulário aberto igual ao que já vai pro PostHog; `marketing_funnel_leads`, contato real). Um repositório único (`MarketingFunnelRepository`) concentra toda a lógica de agregação (contagem de visitantes distintos por etapa, conversão, queda) — sem camada de `service`/domínio própria, porque não há regra de negócio além de "gravar" e "contar": adicionar uma camada extra aqui seria abstração sem uso, ao contrário do resto do backend (identity, billing, fiscal) que tem regras reais para encapsular. Dois routers no mesmo arquivo (`router_public` sem auth, `router_admin` atrás de `require_admin`), seguindo o padrão já usado em `finance_support.py` (dois routers, um arquivo).

**Tech Stack:** FastAPI, SQLAlchemy async (Postgres via `asyncpg` em produção, SQLite `aiosqlite` em memória nos testes — mesmo padrão de `tests/unit/test_market_repository_document.py`), Alembic, Pydantic v2.

**Spec:** `backend/docs/superpowers/specs/2026-09-15-funis-ab-conversao-design.md`

## Global Constraints

- `funnel_variant` é sempre `"A"` ou `"B"` (string de 1 caractere) — nunca outro valor, em nenhuma camada.
- Nenhum IP bruto é persistido no banco — rate limit usa `enforce_rate_limit_async` (já existe em `infra/security/rate_limiter.py`), que só toca IP em memória/Redis.
- Vocabulário de eventos fixo (usado idêntico no frontend, no PostHog e aqui): `marketfy_funnel_start`, `marketfy_funnel_step_view` (com `step` = id da etapa, ex. `q1`..`q11` no funil A, `s1`..`s5` no funil B), `marketfy_lead_submit`, `marketfy_offer_view`, `marketfy_offer_cta_click`, `marketfy_offer_decline`, `marketfy_downsell_cta_click`.
- Todas as rotas novas ficam sob `/api/v1/marketing-funnel` (públicas) e `/api/v1/admin/marketing-funnel` (admin), seguindo o prefixo de `main.py`.

---

### Task 1: Modelos de dados + migração

**Files:**
- Modify: `app/infra/database/models.py` (adicionar no fim do arquivo)
- Create: `alembic/versions/20260915_0026_marketing_funnel_tables.py`
- Test: `tests/unit/test_marketing_funnel_models.py`

**Interfaces:**
- Produces: `MarketingFunnelEventModel` (colunas: `id`, `visitor_id`, `funnel_variant`, `event_name`, `step`, `properties`, `created_at`) e `MarketingFunnelLeadModel` (colunas: `id`, `visitor_id`, `funnel_variant`, `name`, `phone`, `email`, `city`, `control_score`, `answers`, `created_at`) — usados por todas as tasks seguintes.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/test_marketing_funnel_models.py`:

```python
from __future__ import annotations

import os
import sys
import uuid

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


@pytest.mark.asyncio
async def test_event_and_lead_models_round_trip():
    from infra.database.models import Base, MarketingFunnelEventModel, MarketingFunnelLeadModel

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    visitor_id = str(uuid.uuid4())

    async with Session() as session:
        event = MarketingFunnelEventModel(
            id=uuid.uuid4(),
            visitor_id=visitor_id,
            funnel_variant="A",
            event_name="marketfy_funnel_step_view",
            step="q3",
            properties={"answer": "Sim"},
        )
        lead = MarketingFunnelLeadModel(
            id=uuid.uuid4(),
            visitor_id=visitor_id,
            funnel_variant="A",
            name="Ana",
            phone="11999990000",
            email=None,
            city="São Paulo/SP",
            control_score=72,
            answers={"business": "Mercadinho de bairro"},
        )
        session.add_all([event, lead])
        await session.commit()

    async with Session() as session:
        reloaded_event = await session.get(MarketingFunnelEventModel, event.id)
        reloaded_lead = await session.get(MarketingFunnelLeadModel, lead.id)

        assert reloaded_event.visitor_id == visitor_id
        assert reloaded_event.step == "q3"
        assert reloaded_event.properties == {"answer": "Sim"}

        assert reloaded_lead.name == "Ana"
        assert reloaded_lead.control_score == 72
        assert reloaded_lead.answers == {"business": "Mercadinho de bairro"}
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/unit/test_marketing_funnel_models.py -v`
Expected: FAIL com `ImportError: cannot import name 'MarketingFunnelEventModel'`.

- [ ] **Step 3: Adicionar os modelos**

No fim de `app/infra/database/models.py`, adicionar:

```python
class MarketingFunnelEventModel(Base):
    __tablename__ = "marketing_funnel_events"
    __table_args__ = (
        Index("ix_mfe_variant_event_step", "funnel_variant", "event_name", "step"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visitor_id = Column(String, nullable=False, index=True)
    funnel_variant = Column(String(1), nullable=False)
    event_name = Column(String(64), nullable=False)
    step = Column(String(32), nullable=True)
    properties = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class MarketingFunnelLeadModel(Base):
    __tablename__ = "marketing_funnel_leads"
    __table_args__ = (
        Index("ix_mfl_variant", "funnel_variant"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visitor_id = Column(String, nullable=False, index=True)
    funnel_variant = Column(String(1), nullable=False)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    city = Column(String, nullable=True)
    control_score = Column(Integer, nullable=True)
    answers = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/unit/test_marketing_funnel_models.py -v`
Expected: PASS

- [ ] **Step 5: Criar a migração Alembic**

Criar `alembic/versions/20260915_0026_marketing_funnel_tables.py`:

```python
"""marketing_funnel_events e marketing_funnel_leads.

Tabelas dos funis de conversao A/B (/funil-a, /funil-b): eventos de
navegacao anonima (visitante nunca autenticado) e leads reais capturados
no fim do funil. Ver spec 2026-09-15-funis-ab-conversao-design.md.
"""

from typing import Sequence, Union
import uuid

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_0026"
down_revision: Union[str, Sequence[str], None] = "20260915_0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "marketing_funnel_events",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("visitor_id", sa.String(), nullable=False),
        sa.Column("funnel_variant", sa.String(length=1), nullable=False),
        sa.Column("event_name", sa.String(length=64), nullable=False),
        sa.Column("step", sa.String(length=32), nullable=True),
        sa.Column("properties", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_marketing_funnel_events_visitor_id", "marketing_funnel_events", ["visitor_id"])
    op.create_index("ix_marketing_funnel_events_created_at", "marketing_funnel_events", ["created_at"])
    op.create_index(
        "ix_mfe_variant_event_step",
        "marketing_funnel_events",
        ["funnel_variant", "event_name", "step"],
    )

    op.create_table(
        "marketing_funnel_leads",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("visitor_id", sa.String(), nullable=False),
        sa.Column("funnel_variant", sa.String(length=1), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=True),
        sa.Column("control_score", sa.Integer(), nullable=True),
        sa.Column("answers", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_marketing_funnel_leads_visitor_id", "marketing_funnel_leads", ["visitor_id"])
    op.create_index("ix_mfl_variant", "marketing_funnel_leads", ["funnel_variant"])


def downgrade() -> None:
    op.drop_index("ix_mfl_variant", table_name="marketing_funnel_leads")
    op.drop_index("ix_marketing_funnel_leads_visitor_id", table_name="marketing_funnel_leads")
    op.drop_table("marketing_funnel_leads")

    op.drop_index("ix_mfe_variant_event_step", table_name="marketing_funnel_events")
    op.drop_index("ix_marketing_funnel_events_created_at", table_name="marketing_funnel_events")
    op.drop_index("ix_marketing_funnel_events_visitor_id", table_name="marketing_funnel_events")
    op.drop_table("marketing_funnel_events")
```

Run: `alembic upgrade head` (precisa de `DATABASE_URL` apontando para um Postgres real/de dev — o teste do Step 4 já validou o mapeamento ORM contra SQLite).
Expected: migração aplicada sem erro; `alembic current` mostra `20260915_0026 (head)`.

- [ ] **Step 6: Commit**

```bash
git add app/infra/database/models.py alembic/versions/20260915_0026_marketing_funnel_tables.py tests/unit/test_marketing_funnel_models.py
git commit -m "feat: add marketing_funnel_events and marketing_funnel_leads tables"
```

---

### Task 2: Repositório

**Files:**
- Create: `app/infra/repositories/marketing_funnel_repo.py`
- Test: `tests/unit/test_marketing_funnel_repo.py`

**Interfaces:**
- Consumes: `MarketingFunnelEventModel`, `MarketingFunnelLeadModel` (Task 1).
- Produces: `MarketingFunnelRepository(session)` com `create_event(...)`, `create_lead(...)`, `count_distinct_visitors(*, funnel_variant, event_name, step=None) -> int`, `get_funnel_summary(funnel_variant: str) -> list[dict]` (cada dict: `label`, `count`, `conversion_pct`, `drop_off_pct`), `list_leads(*, funnel_variant=None, limit=50, offset=0) -> list[MarketingFunnelLeadModel]`, `count_leads(*, funnel_variant=None) -> int`. Também exporta `FUNNEL_STEPS: dict[str, list[dict]]` — usado pela Task 4/5.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/test_marketing_funnel_repo.py`:

```python
from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


async def _make_session():
    from infra.database.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return Session()


@pytest.mark.asyncio
async def test_create_event_and_count_distinct_visitors():
    from infra.repositories.marketing_funnel_repo import MarketingFunnelRepository

    async with await _make_session() as session:
        repo = MarketingFunnelRepository(session)

        await repo.create_event(visitor_id="v1", funnel_variant="A", event_name="marketfy_funnel_start")
        await repo.create_event(visitor_id="v1", funnel_variant="A", event_name="marketfy_funnel_start")  # mesmo visitante, não deve dobrar a contagem
        await repo.create_event(visitor_id="v2", funnel_variant="A", event_name="marketfy_funnel_start")
        await repo.create_event(visitor_id="v3", funnel_variant="B", event_name="marketfy_funnel_start")

        count_a = await repo.count_distinct_visitors(funnel_variant="A", event_name="marketfy_funnel_start")
        count_b = await repo.count_distinct_visitors(funnel_variant="B", event_name="marketfy_funnel_start")

        assert count_a == 2
        assert count_b == 1


@pytest.mark.asyncio
async def test_count_distinct_visitors_filters_by_step():
    from infra.repositories.marketing_funnel_repo import MarketingFunnelRepository

    async with await _make_session() as session:
        repo = MarketingFunnelRepository(session)

        await repo.create_event(visitor_id="v1", funnel_variant="A", event_name="marketfy_funnel_step_view", step="q1")
        await repo.create_event(visitor_id="v2", funnel_variant="A", event_name="marketfy_funnel_step_view", step="q1")
        await repo.create_event(visitor_id="v1", funnel_variant="A", event_name="marketfy_funnel_step_view", step="q2")

        count_q1 = await repo.count_distinct_visitors(funnel_variant="A", event_name="marketfy_funnel_step_view", step="q1")
        count_q2 = await repo.count_distinct_visitors(funnel_variant="A", event_name="marketfy_funnel_step_view", step="q2")

        assert count_q1 == 2
        assert count_q2 == 1


@pytest.mark.asyncio
async def test_get_funnel_summary_computes_conversion_and_drop_off():
    from infra.repositories.marketing_funnel_repo import MarketingFunnelRepository

    async with await _make_session() as session:
        repo = MarketingFunnelRepository(session)

        # 4 visitantes iniciam o funil B, 2 chegam no cenário 1, 1 vira lead
        for v in ("v1", "v2", "v3", "v4"):
            await repo.create_event(visitor_id=v, funnel_variant="B", event_name="marketfy_funnel_start")
        for v in ("v1", "v2"):
            await repo.create_event(visitor_id=v, funnel_variant="B", event_name="marketfy_funnel_step_view", step="s1")
        for v in ("v1", "v2", "v3", "v4", "v5"):
            await repo.create_event(visitor_id=v, funnel_variant="B", event_name="marketfy_funnel_step_view", step="s2")

        summary = await repo.get_funnel_summary("B")

        start_step = next(s for s in summary if s["label"] == "Início")
        s1_step = next(s for s in summary if s["label"] == "Cenário 1")
        s2_step = next(s for s in summary if s["label"] == "Cenário 2")

        assert start_step["count"] == 4
        assert start_step["conversion_pct"] == 100.0
        assert start_step["drop_off_pct"] == 0.0

        assert s1_step["count"] == 2
        assert s1_step["conversion_pct"] == 50.0
        assert s1_step["drop_off_pct"] == 50.0

        # step com mais visitantes que a etapa anterior (v5 nunca passou por "Início"/"s1")
        # não gera drop-off negativo — fica em 0.0
        assert s2_step["count"] == 5
        assert s2_step["drop_off_pct"] == 0.0


@pytest.mark.asyncio
async def test_create_lead_and_list_leads():
    from infra.repositories.marketing_funnel_repo import MarketingFunnelRepository

    async with await _make_session() as session:
        repo = MarketingFunnelRepository(session)

        await repo.create_lead(
            visitor_id="v1", funnel_variant="A", name="Ana", phone="11999990000",
            email=None, city="São Paulo/SP", control_score=70, answers={"business": "Mercadinho"},
        )
        await repo.create_lead(
            visitor_id="v2", funnel_variant="B", name="Beto", phone=None,
            email="beto@x.com", city=None, control_score=40, answers={},
        )

        all_leads = await repo.list_leads()
        only_a = await repo.list_leads(funnel_variant="A")

        assert len(all_leads) == 2
        assert len(only_a) == 1
        assert only_a[0].name == "Ana"
        assert await repo.count_leads() == 2
        assert await repo.count_leads(funnel_variant="B") == 1
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/unit/test_marketing_funnel_repo.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'infra.repositories.marketing_funnel_repo'`.

- [ ] **Step 3: Implementar o repositório**

Criar `app/infra/repositories/marketing_funnel_repo.py`:

```python
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.models import MarketingFunnelEventModel, MarketingFunnelLeadModel

# Ordem fixa de etapas por funil, usada pelo painel admin para montar o funil
# de conversao. `step=None` significa que o evento nao carrega `step` (o
# proprio `event_name` identifica a etapa, ex. inicio, lead, oferta, cta).
FUNNEL_STEPS: Dict[str, List[Dict[str, Optional[str]]]] = {
    "A": [
        {"label": "Início", "event_name": "marketfy_funnel_start", "step": None},
        *[
            {"label": f"Pergunta {i}", "event_name": "marketfy_funnel_step_view", "step": f"q{i}"}
            for i in range(1, 12)
        ],
        {"label": "Lead enviado", "event_name": "marketfy_lead_submit", "step": None},
        {"label": "Oferta vista", "event_name": "marketfy_offer_view", "step": None},
        {"label": "CTA clicado", "event_name": "marketfy_offer_cta_click", "step": None},
    ],
    "B": [
        {"label": "Início", "event_name": "marketfy_funnel_start", "step": None},
        *[
            {"label": f"Cenário {i}", "event_name": "marketfy_funnel_step_view", "step": f"s{i}"}
            for i in range(1, 6)
        ],
        {"label": "Lead enviado", "event_name": "marketfy_lead_submit", "step": None},
        {"label": "Oferta vista", "event_name": "marketfy_offer_view", "step": None},
        {"label": "CTA clicado", "event_name": "marketfy_offer_cta_click", "step": None},
    ],
}


class MarketingFunnelRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_event(
        self,
        *,
        visitor_id: str,
        funnel_variant: str,
        event_name: str,
        step: Optional[str] = None,
        properties: Optional[dict] = None,
    ) -> MarketingFunnelEventModel:
        model = MarketingFunnelEventModel(
            id=uuid.uuid4(),
            visitor_id=visitor_id,
            funnel_variant=funnel_variant,
            event_name=event_name,
            step=step,
            properties=properties,
            created_at=datetime.utcnow(),
        )
        self.session.add(model)
        await self.session.commit()
        return model

    async def create_lead(
        self,
        *,
        visitor_id: str,
        funnel_variant: str,
        name: str,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        city: Optional[str] = None,
        control_score: Optional[int] = None,
        answers: Optional[dict] = None,
    ) -> MarketingFunnelLeadModel:
        model = MarketingFunnelLeadModel(
            id=uuid.uuid4(),
            visitor_id=visitor_id,
            funnel_variant=funnel_variant,
            name=name,
            phone=phone,
            email=email,
            city=city,
            control_score=control_score,
            answers=answers or {},
            created_at=datetime.utcnow(),
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        return model

    async def count_distinct_visitors(
        self, *, funnel_variant: str, event_name: str, step: Optional[str] = None
    ) -> int:
        query = select(func.count(func.distinct(MarketingFunnelEventModel.visitor_id))).where(
            MarketingFunnelEventModel.funnel_variant == funnel_variant,
            MarketingFunnelEventModel.event_name == event_name,
        )
        if step is not None:
            query = query.where(MarketingFunnelEventModel.step == step)
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def get_funnel_summary(self, funnel_variant: str) -> List[Dict]:
        steps = FUNNEL_STEPS[funnel_variant]
        counts = [
            await self.count_distinct_visitors(
                funnel_variant=funnel_variant, event_name=entry["event_name"], step=entry["step"]
            )
            for entry in steps
        ]
        first_count = counts[0] if counts and counts[0] > 0 else None

        summary = []
        for i, entry in enumerate(steps):
            count = counts[i]
            previous_count = counts[i - 1] if i > 0 else None
            conversion_pct = round((count / first_count) * 100, 1) if first_count else 0.0
            drop_off_pct = (
                round((1 - count / previous_count) * 100, 1)
                if previous_count and count <= previous_count
                else 0.0
            )
            summary.append(
                {
                    "label": entry["label"],
                    "count": count,
                    "conversion_pct": conversion_pct,
                    "drop_off_pct": drop_off_pct,
                }
            )
        return summary

    async def list_leads(
        self, *, funnel_variant: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> List[MarketingFunnelLeadModel]:
        query = (
            select(MarketingFunnelLeadModel)
            .order_by(MarketingFunnelLeadModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if funnel_variant is not None:
            query = query.where(MarketingFunnelLeadModel.funnel_variant == funnel_variant)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_leads(self, *, funnel_variant: Optional[str] = None) -> int:
        query = select(func.count(MarketingFunnelLeadModel.id))
        if funnel_variant is not None:
            query = query.where(MarketingFunnelLeadModel.funnel_variant == funnel_variant)
        result = await self.session.execute(query)
        return result.scalar() or 0
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/unit/test_marketing_funnel_repo.py -v`
Expected: PASS (4 testes)

- [ ] **Step 5: Commit**

```bash
git add app/infra/repositories/marketing_funnel_repo.py tests/unit/test_marketing_funnel_repo.py
git commit -m "feat: add MarketingFunnelRepository with funnel summary aggregation"
```

---

### Task 3: DTOs

**Files:**
- Modify: `app/application/dtos.py` (linha 1: import; fim do arquivo: novos DTOs)
- Test: `tests/unit/test_marketing_funnel_dtos.py`

**Interfaces:**
- Consumes: nada de tasks anteriores (DTOs são independentes de ORM).
- Produces: `MarketingFunnelEventCreateDTO`, `MarketingFunnelLeadCreateDTO`, `MarketingFunnelLeadResponseDTO`, `MarketingFunnelStepSummaryDTO`, `MarketingFunnelSummaryDTO` — usados pela Task 4/5.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/test_marketing_funnel_dtos.py`:

```python
from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from pydantic import ValidationError


def test_event_dto_rejects_invalid_funnel_variant():
    from application.dtos import MarketingFunnelEventCreateDTO

    with pytest.raises(ValidationError):
        MarketingFunnelEventCreateDTO(
            visitor_id="v" * 10, funnel_variant="C", event_name="marketfy_funnel_start"
        )


def test_event_dto_accepts_valid_payload():
    from application.dtos import MarketingFunnelEventCreateDTO

    dto = MarketingFunnelEventCreateDTO(
        visitor_id="v" * 10,
        funnel_variant="A",
        event_name="marketfy_funnel_step_view",
        step="q1",
        properties={"answer": "Sim"},
    )
    assert dto.funnel_variant == "A"
    assert dto.step == "q1"


def test_lead_dto_requires_phone_or_email():
    from application.dtos import MarketingFunnelLeadCreateDTO

    with pytest.raises(ValidationError):
        MarketingFunnelLeadCreateDTO(
            visitor_id="v" * 10, funnel_variant="A", name="Ana", answers={},
        )


def test_lead_dto_accepts_phone_only():
    from application.dtos import MarketingFunnelLeadCreateDTO

    dto = MarketingFunnelLeadCreateDTO(
        visitor_id="v" * 10, funnel_variant="A", name="Ana", phone="11999990000", answers={},
    )
    assert dto.phone == "11999990000"
    assert dto.email is None
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/unit/test_marketing_funnel_dtos.py -v`
Expected: FAIL com `ImportError: cannot import name 'MarketingFunnelEventCreateDTO'`.

- [ ] **Step 3: Implementar os DTOs**

Em `app/application/dtos.py`, trocar a linha de import do topo:

```python
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
```

por:

```python
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
```

E trocar:

```python
from typing import List, Optional, Dict, Any, Union
```

por:

```python
from typing import List, Optional, Dict, Any, Union, Literal
```

No fim do arquivo, adicionar:

```python
# ===========================
# MARKETING FUNNEL DTOs (A/B)
# ===========================

class MarketingFunnelEventCreateDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visitor_id: str = Field(..., min_length=8, max_length=128)
    funnel_variant: Literal["A", "B"]
    event_name: str = Field(..., min_length=1, max_length=64)
    step: Optional[str] = Field(None, max_length=32)
    properties: Optional[dict] = None


class MarketingFunnelLeadCreateDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visitor_id: str = Field(..., min_length=8, max_length=128)
    funnel_variant: Literal["A", "B"]
    name: str = Field(..., min_length=2, max_length=120)
    phone: Optional[str] = Field(None, max_length=32)
    email: Optional[EmailStr] = None
    city: Optional[str] = Field(None, max_length=120)
    control_score: Optional[int] = Field(None, ge=0, le=100)
    answers: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def phone_or_email_required(self):
        if not self.phone and not self.email:
            raise ValueError("Informe telefone ou e-mail.")
        return self


class MarketingFunnelLeadResponseDTO(BaseModel):
    id: Any
    funnel_variant: str
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    city: Optional[str] = None
    control_score: Optional[int] = None
    created_at: Any

    class Config:
        from_attributes = True


class MarketingFunnelStepSummaryDTO(BaseModel):
    label: str
    count: int
    conversion_pct: float
    drop_off_pct: float


class MarketingFunnelSummaryDTO(BaseModel):
    funnel_variant: str
    steps: List[MarketingFunnelStepSummaryDTO]
    lead_count: int
    cta_click_count: int
    lead_to_cta_rate: float
```

`id`/`created_at` usam `Any` em vez de `UUID`/`datetime` porque `UUID` e `datetime` já estão importados no topo do arquivo (`from uuid import UUID`, `from datetime import datetime, date`) — trocar para os tipos corretos:

```python
class MarketingFunnelLeadResponseDTO(BaseModel):
    id: UUID
    funnel_variant: str
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    city: Optional[str] = None
    control_score: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/unit/test_marketing_funnel_dtos.py -v`
Expected: PASS (4 testes)

- [ ] **Step 5: Rodar a suíte completa de DTOs pra garantir que o import novo não quebrou nada**

Run: `pytest tests/unit -k dto -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/application/dtos.py tests/unit/test_marketing_funnel_dtos.py
git commit -m "feat: add marketing funnel DTOs"
```

---

### Task 4: Router público (eventos e leads) + rate limit

**Files:**
- Create: `app/infra/web/routers/marketing_funnel.py`
- Modify: `app/infra/web/main.py`
- Test: `tests/unit/test_marketing_funnel_public_routes.py`

**Interfaces:**
- Consumes: `MarketingFunnelRepository` (Task 2), `MarketingFunnelEventCreateDTO`/`MarketingFunnelLeadCreateDTO`/`MarketingFunnelLeadResponseDTO` (Task 3), `get_db` (`infra.database.setup`), `enforce_rate_limit_async` (`infra.security.rate_limiter`).
- Produces: `router_public` (montado em `main.py` sob `/api/v1/marketing-funnel`) com `POST /events` (202) e `POST /leads` (201) — consumido pelo frontend (plano de frontend) e pela Task 5 (via dados já gravados).

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/test_marketing_funnel_public_routes.py`:

```python
from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def client():
    from infra.database.models import Base
    from infra.web.main import app
    from infra.web.routers import marketing_funnel as funnel_router
    from infra.security import rate_limiter as rl

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    import asyncio
    asyncio.run(_create_tables(engine, Base))

    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as session:
            yield session

    # rate limiter isolado por teste, pra nao vazar contagem entre testes
    rl.rate_limiter = rl.InMemoryRateLimiter()

    app.dependency_overrides[funnel_router.get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


async def _create_tables(engine, Base):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def test_post_event_returns_202(client):
    response = client.post(
        "/api/v1/marketing-funnel/events",
        json={
            "visitor_id": "v" * 10,
            "funnel_variant": "A",
            "event_name": "marketfy_funnel_start",
        },
    )
    assert response.status_code == 202


def test_post_lead_returns_201_with_body(client):
    response = client.post(
        "/api/v1/marketing-funnel/leads",
        json={
            "visitor_id": "v" * 10,
            "funnel_variant": "B",
            "name": "Ana",
            "phone": "11999990000",
            "answers": {"business": "Mercadinho"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Ana"
    assert body["funnel_variant"] == "B"


def test_post_lead_without_phone_or_email_returns_422(client):
    response = client.post(
        "/api/v1/marketing-funnel/leads",
        json={"visitor_id": "v" * 10, "funnel_variant": "A", "name": "Ana", "answers": {}},
    )
    assert response.status_code == 422


def test_post_lead_rate_limited_after_five_per_minute(client):
    payload = {
        "visitor_id": "v" * 10,
        "funnel_variant": "A",
        "name": "Ana",
        "phone": "11999990000",
        "answers": {},
    }
    for _ in range(5):
        response = client.post("/api/v1/marketing-funnel/leads", json=payload)
        assert response.status_code == 201

    response = client.post("/api/v1/marketing-funnel/leads", json=payload)
    assert response.status_code == 429
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/unit/test_marketing_funnel_public_routes.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'infra.web.routers.marketing_funnel'`.

- [ ] **Step 3: Implementar o router público**

Criar `app/infra/web/routers/marketing_funnel.py`:

```python
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from application.dtos import (
    MarketingFunnelEventCreateDTO,
    MarketingFunnelLeadCreateDTO,
    MarketingFunnelLeadResponseDTO,
    MarketingFunnelSummaryDTO,
)
from domain.identity import User
from infra.database.setup import get_db
from infra.repositories.marketing_funnel_repo import MarketingFunnelRepository
from infra.security.rate_limiter import enforce_rate_limit_async
from infra.web.dependencies import require_admin

router_public = APIRouter()
router_admin = APIRouter()


@router_public.post("/events", status_code=status.HTTP_202_ACCEPTED)
async def create_funnel_event(
    dto: MarketingFunnelEventCreateDTO,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit_async(request, bucket="marketing_funnel_event", limit=60, window_seconds=60)
    repo = MarketingFunnelRepository(db)
    await repo.create_event(
        visitor_id=dto.visitor_id,
        funnel_variant=dto.funnel_variant,
        event_name=dto.event_name,
        step=dto.step,
        properties=dto.properties,
    )
    return None


@router_public.post(
    "/leads", response_model=MarketingFunnelLeadResponseDTO, status_code=status.HTTP_201_CREATED
)
async def create_funnel_lead(
    dto: MarketingFunnelLeadCreateDTO,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit_async(request, bucket="marketing_funnel_lead", limit=5, window_seconds=60)
    repo = MarketingFunnelRepository(db)
    return await repo.create_lead(
        visitor_id=dto.visitor_id,
        funnel_variant=dto.funnel_variant,
        name=dto.name,
        phone=dto.phone,
        email=dto.email,
        city=dto.city,
        control_score=dto.control_score,
        answers=dto.answers,
    )


@router_admin.get("/marketing-funnel/summary", response_model=List[MarketingFunnelSummaryDTO])
async def get_marketing_funnel_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    repo = MarketingFunnelRepository(db)
    result = []
    for variant in ("A", "B"):
        steps = await repo.get_funnel_summary(variant)
        lead_count = next((s["count"] for s in steps if s["label"] == "Lead enviado"), 0)
        cta_count = next((s["count"] for s in steps if s["label"] == "CTA clicado"), 0)
        lead_to_cta_rate = round((cta_count / lead_count) * 100, 1) if lead_count else 0.0
        result.append(
            MarketingFunnelSummaryDTO(
                funnel_variant=variant,
                steps=steps,
                lead_count=lead_count,
                cta_click_count=cta_count,
                lead_to_cta_rate=lead_to_cta_rate,
            )
        )
    return result


@router_admin.get("/marketing-funnel/leads", response_model=List[MarketingFunnelLeadResponseDTO])
async def list_marketing_funnel_leads(
    variant: Optional[str] = Query(None, pattern="^(A|B)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    repo = MarketingFunnelRepository(db)
    return await repo.list_leads(funnel_variant=variant, limit=limit, offset=offset)
```

- [ ] **Step 4: Registrar em `main.py`**

Em `app/infra/web/main.py`, adicionar `marketing_funnel` ao bloco de import (`from infra.web.routers import (...)`, ordem alfabética junto dos demais) e, perto dos outros `include_router`, adicionar:

```python
app.include_router(marketing_funnel.router_public, prefix="/api/v1/marketing-funnel", tags=["Marketing Funnel"])
app.include_router(marketing_funnel.router_admin, prefix="/api/v1/admin", tags=["Marketing Funnel Admin"])
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `pytest tests/unit/test_marketing_funnel_public_routes.py -v`
Expected: PASS (4 testes)

- [ ] **Step 6: Commit**

```bash
git add app/infra/web/routers/marketing_funnel.py app/infra/web/main.py tests/unit/test_marketing_funnel_public_routes.py
git commit -m "feat: add public marketing-funnel events/leads endpoints"
```

---

### Task 5: Router admin (resumo do funil e lista de leads)

**Files:**
- Modify: `app/infra/web/routers/marketing_funnel.py` (já criado na Task 4 — endpoints admin já estão nele, só falta o teste)
- Test: `tests/unit/test_marketing_funnel_admin_routes.py`

**Interfaces:**
- Consumes: `router_admin` (Task 4, já implementado), `MarketingFunnelRepository` (Task 2), `require_admin` (`infra.web.dependencies`).
- Produces: dados usados pela página de admin do frontend (`GET /api/v1/admin/marketing-funnel/summary`, `GET /api/v1/admin/marketing-funnel/leads`).

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/test_marketing_funnel_admin_routes.py`:

```python
from __future__ import annotations

import os
import sys
import uuid
from types import SimpleNamespace

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def client_and_session():
    from infra.database.models import Base
    from infra.web.main import app
    from infra.web.routers import marketing_funnel as funnel_router

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    import asyncio
    asyncio.run(_create_tables(engine, Base))

    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as session:
            yield session

    admin_user = SimpleNamespace(id=uuid.uuid4(), role="admin")
    app.dependency_overrides[funnel_router.get_db] = _override_get_db
    app.dependency_overrides[funnel_router.require_admin] = lambda: admin_user
    try:
        yield TestClient(app), Session
    finally:
        app.dependency_overrides.clear()


async def _create_tables(engine, Base):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed(Session):
    from infra.repositories.marketing_funnel_repo import MarketingFunnelRepository

    async with Session() as session:
        repo = MarketingFunnelRepository(session)
        await repo.create_event(visitor_id="v1", funnel_variant="A", event_name="marketfy_funnel_start")
        await repo.create_event(visitor_id="v2", funnel_variant="A", event_name="marketfy_funnel_start")
        await repo.create_event(visitor_id="v1", funnel_variant="A", event_name="marketfy_lead_submit")
        await repo.create_event(visitor_id="v1", funnel_variant="A", event_name="marketfy_offer_cta_click")
        await repo.create_lead(
            visitor_id="v1", funnel_variant="A", name="Ana", phone="11999990000", answers={},
        )


def test_summary_returns_both_variants_with_counts(client_and_session):
    import asyncio

    client, Session = client_and_session
    asyncio.run(_seed(Session))

    response = client.get("/api/v1/admin/marketing-funnel/summary")
    assert response.status_code == 200
    body = response.json()

    variants = {item["funnel_variant"]: item for item in body}
    assert variants["A"]["lead_count"] == 1
    assert variants["A"]["cta_click_count"] == 1
    assert variants["A"]["lead_to_cta_rate"] == 100.0

    start_step = next(s for s in variants["A"]["steps"] if s["label"] == "Início")
    assert start_step["count"] == 2

    assert variants["B"]["lead_count"] == 0


def test_leads_endpoint_filters_by_variant(client_and_session):
    import asyncio

    client, Session = client_and_session
    asyncio.run(_seed(Session))

    response = client.get("/api/v1/admin/marketing-funnel/leads", params={"variant": "A"})
    assert response.status_code == 200
    leads = response.json()
    assert len(leads) == 1
    assert leads[0]["name"] == "Ana"

    response_b = client.get("/api/v1/admin/marketing-funnel/leads", params={"variant": "B"})
    assert response_b.json() == []
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/unit/test_marketing_funnel_admin_routes.py -v`
Expected: FAIL — `app.dependency_overrides[funnel_router.require_admin]` funciona, mas antes da Task 4 estar completa (main.py sem os routers montados) as rotas retornam 404.

Se a Task 4 já foi commitada, este teste deve falhar apenas se algo estiver incorreto no `get_funnel_summary`/`list_leads`; confirme rodando antes de qualquer ajuste.

- [ ] **Step 3: Ajustar se necessário**

Os endpoints já foram implementados na Task 4 (`router_admin` no mesmo arquivo). Se o teste falhar por causa da agregação, revisar `get_funnel_summary` em `app/infra/repositories/marketing_funnel_repo.py` (Task 2) — não deve ser necessário nenhum código novo aqui, este teste é a rede de segurança que confirma que Task 2 + Task 4 se encaixam corretamente end-to-end.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/unit/test_marketing_funnel_admin_routes.py -v`
Expected: PASS (2 testes)

- [ ] **Step 5: Rodar a suíte inteira de testes novos da feature**

Run: `pytest tests/unit -k marketing_funnel -v`
Expected: PASS (todos os testes das Tasks 1-5)

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_marketing_funnel_admin_routes.py
git commit -m "test: cover marketing-funnel admin summary and leads endpoints"
```
