# Rodada 2 de Conversão — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar um bypass de paywall, corrigir a persistência de `fiscal_monthly_limit` e expor na API os campos de plano que o front precisa para parar de inventar "recomendado", descrição e documento de cobrança.

**Architecture:** Mudanças pequenas e aditivas sobre o que já existe (`Plan` dataclass → `PlanModel` → `SQLAlchemyPlanRepository` → DTOs Pydantic v2 → routers FastAPI). Uma migration Alembic aditiva (3 colunas com default). Nenhum contrato existente é removido; `GET /identity/plans` passa a filtrar no servidor o que o front já filtrava.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2.5, SQLAlchemy async, Alembic, pytest + pytest-asyncio (SQLite em memória via aiosqlite nos testes de repositório).

**Spec:** Handoff https://claude.ai/code/artifact/194d0a41-ee91-496a-b177-8fc938fe02e4 (seções "Backend pendente" e "Decisões") + plano original https://claude.ai/code/artifact/4b787f55-ad5f-4e6a-8194-29a224a52657. Plano irmão do front: `marketfy-frontend/docs/superpowers/plans/2026-09-14-conversion-round2-frontend.md`.

## Global Constraints

- Testes seguem o padrão existente: `sys.path.append(<repo>/app)` no topo do arquivo, imports sem prefixo `app.` (ex.: `from application.dtos import ...`).
- Rotas testadas com `fastapi.testclient.TestClient(app)` e `app.dependency_overrides[...]`, sempre limpando os overrides no `finally`.
- Nenhuma alteração pode depender das decisões D1, D2, D4–D9 do plano original (seguem abertas).
- Nenhum dado pessoal completo (CPF) sai em resposta de API nova — só mascarado.
- Migration nova encadeia no head atual `20260810_0020`.
- Mensagens de erro para o usuário final em português.
- Commits terminam com `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## Evidência levantada antes do plano (verificada no código em 2026-09-14)

| Achado | Evidência |
|---|---|
| Dono sem assinatura ativa qualquer plano pago de graça | `app/infra/web/routers/identity.py:70-115` só checa admin quando `user_id_override` vem preenchido; `plan_access_service._fallback_from_user` (`:184-210`) libera acesso por `user.plan_id` quando não há `BillingSubscriptionModel`. Não reproduzido contra servidor rodando — inferido do código. O front não chama essa rota (só `/admin/identity/plans/{id}/subscribe`). |
| `fiscal_monthly_limit` nunca é gravado pela aplicação | `SQLAlchemyPlanRepository.save` (`sqlalchemy_repos.py:124-141`) não atribui o campo; `AdminService.create_plan` não o recebe; DTOs não o têm. |
| `GET /identity/plans` expõe planos inativos e de cortesia | `identity.py:65-68` retorna `repo.list_all()` sem filtro nem `response_model`. |
| D3 já está respondida pelo código | `Plan.is_limit_reached` bloqueia com `current >= 0`; `FiscalQuotaService.check_and_reserve` levanta `FiscalQuotaExceededError` quando `included + addon == 0`. `0` = "não incluído". |
| Checkout de fatura em 1 passo não precisa de endpoint novo | `POST /billing/invoices/{id}/checkout` (`billing.py:191-219`) + `InvoiceService.ensure_checkout` já criam/consultam o checkout de forma idempotente. Trabalho fica todo no front. |
| Projeção de créditos (C5) não precisa de histórico novo | `GET /fiscal/{market_id}/credits/balance` já devolve `period` (`%Y%m`), `used_count` e `remaining`. Trabalho fica todo no front. |
| `UserModel` não tem coluna `cnpj` | `models.py:26-40`; `User.cnpj` do domínio nunca é persistido. Documento cadastrado = CPF. |
| Ambiente local sem pytest | `python -m pytest` → "No module named pytest" em Python 3.12 do sistema. Instalar deps de teste antes (`pip install -r requirements.txt pytest pytest-asyncio aiosqlite`). |

## Ordem de execução e deploy

1. **Task 1 sozinha, como hotfix** (PR e deploy próprios, antes de todo o resto).
2. Tasks 2 → 3 → 4 → 5 (um PR). Rodar `alembic upgrade head` em staging antes do deploy.
3. Só depois do deploy do backend: tasks FE-6, FE-7, FE-8 e FE-9 do plano do front.

## File Structure

| Arquivo | Responsabilidade | Task |
|---|---|---|
| `app/infra/web/routers/identity.py` | Guard de admin na rota legada; catálogo público | 1, 4 |
| `app/domain/identity.py` | `Plan` ganha `description`, `is_recommended`, `display_order` | 3 |
| `app/infra/database/models.py` | Colunas novas em `PlanModel` | 3 |
| `alembic/versions/20260914_0021_plan_presentation_fields.py` | Migration aditiva | 3 |
| `app/infra/repositories/sqlalchemy_repos.py` | `save`/`_to_entity` mapeiam todos os campos de plano | 2, 3 |
| `app/application/dtos.py` | DTOs de plano (admin e público), `UserResponseDTO.document_masked` | 2, 3, 4, 5 |
| `app/application/services/admin_service.py` | Regras de criação/edição (limite fiscal, recomendado único) | 2, 3 |
| `app/infra/web/routers/admin.py` | Um único conversor `Plan → PlanResponseDTO` | 2 |
| `app/application/services/plan_catalog.py` (novo) | Filtro/ordenação dos planos vendáveis | 4 |
| `app/application/services/billing_document.py` (novo) | Documento cadastrado, máscara e fallback de cobrança | 5 |
| `app/infra/web/routers/auth.py` | `/auth/me` devolve `document_masked` | 5 |
| `app/infra/web/routers/billing.py` | Recorrente usa documento cadastrado quando omitido | 5 |

---

### Task 1: Fechar o bypass de paywall na rota legada de assinatura

**Files:**
- Modify: `app/infra/web/routers/identity.py:70-80`
- Test: `tests/unit/test_identity_legacy_subscribe_guard.py`

**Interfaces:**
- Consumes: `infra.security.authorization.is_admin_user(user) -> bool` (já importado no router).
- Produces: `POST /api/v1/identity/plans/{plan_id}/subscribe` responde `403` para qualquer não-admin, antes de tocar no banco.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import os
import sys
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)


def _post_as(user):
    from infra.web.main import app
    from infra.web.routers import identity as identity_router

    app.dependency_overrides[identity_router.get_current_user] = lambda: user
    app.dependency_overrides[identity_router.get_db] = lambda: None
    app.dependency_overrides[identity_router.get_subscription_service] = lambda: SimpleNamespace(user_repo=None)
    try:
        client = TestClient(app)
        return client.post(
            f"/api/v1/identity/plans/{uuid.uuid4()}/subscribe",
            json={"duration_days": 365},
        )
    finally:
        app.dependency_overrides.clear()


def test_owner_cannot_self_assign_a_plan_without_paying():
    owner = SimpleNamespace(id=uuid.uuid4(), role="owner")

    response = _post_as(owner)

    assert response.status_code == 403


def test_admin_is_not_blocked_by_the_guard():
    admin = SimpleNamespace(id=uuid.uuid4(), role="admin")

    response = _post_as(admin)

    # Sem banco o fluxo falha adiante (400/500), mas nunca no guard de permissão.
    assert response.status_code != 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_identity_legacy_subscribe_guard.py -v`
Expected: `test_owner_cannot_self_assign_a_plan_without_paying` FAIL (status 400 — o `db=None` quebra dentro do `try`, provando que o guard não existe).

- [ ] **Step 3: Write minimal implementation**

Em `app/infra/web/routers/identity.py`, dentro de `subscribe_to_plan`, antes do `try:`:

```python
    # Rota legada: ativa plano sem cobrança. Donos contratam por
    # POST /billing/subscribe; aqui só administradores podem atribuir planos.
    if not is_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Apenas administradores podem atribuir planos.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_identity_legacy_subscribe_guard.py tests/unit/test_phase3_authorization.py -v`
Expected: PASS nos dois arquivos.

- [ ] **Step 5: Verificar se alguém já explorou (manual, sem alterar dados)**

Rodar em produção, somente leitura, e anexar o resultado ao PR:

```sql
SELECT u.id, u.email, p.name, u.plan_expiration
FROM users u
JOIN plans p ON p.id = u.plan_id
LEFT JOIN billing_subscriptions s ON s.owner_id = u.id
WHERE p.type = 'pago' AND s.id IS NULL AND u.role = 'owner';
```

Qualquer linha retornada é um dono com plano pago sem assinatura: levar ao responsável do produto antes de qualquer correção de dados. Confirmar o nome real da tabela de assinaturas em `BillingSubscriptionModel.__tablename__` antes de rodar.

- [ ] **Step 6: Commit**

```bash
git add app/infra/web/routers/identity.py tests/unit/test_identity_legacy_subscribe_guard.py
git commit -m "fix(security): restrict legacy plan subscribe route to admins"
```

---

### Task 2: Persistir e administrar `fiscal_monthly_limit`

**Files:**
- Modify: `app/infra/repositories/sqlalchemy_repos.py:124-141`
- Modify: `app/application/dtos.py:89-110`
- Modify: `app/application/services/admin_service.py:13-75`
- Modify: `app/infra/web/routers/admin.py:105-170`
- Test: `tests/unit/test_plan_admin_fiscal_limit.py`

**Interfaces:**
- Produces: `PlanCreateDTO.fiscal_monthly_limit: int = Field(0, ge=0)`, `PlanUpdateDTO.fiscal_monthly_limit: Optional[int] = Field(None, ge=0)`, `PlanResponseDTO` herda o campo; `admin._plan_to_response(plan: Plan) -> PlanResponseDTO`.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import os
import sys
from decimal import Decimal

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.database.setup import Base
import infra.database.models  # noqa: F401  (registra os models)
from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository
from application.services.admin_service import AdminService
from application.dtos import PlanCreateDTO, PlanUpdateDTO


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _create_dto(**overrides):
    data = dict(
        name="Essencial", type="pago", max_markets=1, max_terminals=2,
        price_monthly=Decimal("79.90"), price_180days=Decimal("429.90"),
        price_annual=Decimal("799.90"), fiscal_monthly_limit=200,
    )
    data.update(overrides)
    return PlanCreateDTO(**data)


async def _reload(session, plan_id):
    session.expunge_all()
    return await SQLAlchemyPlanRepository(session).get_by_id(plan_id)


@pytest.mark.asyncio
async def test_create_plan_persists_fiscal_monthly_limit(session):
    service = AdminService(SQLAlchemyPlanRepository(session))

    plan = await service.create_plan(_create_dto())

    assert (await _reload(session, plan.id)).fiscal_monthly_limit == 200


@pytest.mark.asyncio
async def test_update_plan_changes_fiscal_monthly_limit(session):
    service = AdminService(SQLAlchemyPlanRepository(session))
    plan = await service.create_plan(_create_dto())

    await service.update_plan(plan.id, PlanUpdateDTO(fiscal_monthly_limit=500))

    assert (await _reload(session, plan.id)).fiscal_monthly_limit == 500


@pytest.mark.asyncio
async def test_update_without_fiscal_limit_keeps_current_value(session):
    service = AdminService(SQLAlchemyPlanRepository(session))
    plan = await service.create_plan(_create_dto())

    await service.update_plan(plan.id, PlanUpdateDTO(name="Essencial 2"))

    assert (await _reload(session, plan.id)).fiscal_monthly_limit == 200


def test_negative_fiscal_limit_is_rejected():
    with pytest.raises(ValidationError):
        _create_dto(fiscal_monthly_limit=-1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_plan_admin_fiscal_limit.py -v`
Expected: FAIL — `PlanCreateDTO` ignora o campo (extra) e o valor recarregado é `0`; `test_negative_fiscal_limit_is_rejected` falha por não levantar.

- [ ] **Step 3: Write minimal implementation**

`app/application/dtos.py` (garantir `Field` importado de `pydantic`):

```python
class PlanCreateDTO(BaseModel):
    name: str
    type: str
    max_markets: int
    max_terminals: int
    price_monthly: Decimal
    price_180days: Decimal
    price_annual: Decimal
    fiscal_monthly_limit: int = Field(0, ge=0)
    is_active: bool = True

class PlanUpdateDTO(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    max_markets: Optional[int] = None
    max_terminals: Optional[int] = None
    price_monthly: Optional[Decimal] = None
    price_180days: Optional[Decimal] = None
    price_annual: Optional[Decimal] = None
    fiscal_monthly_limit: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None
```

`app/infra/repositories/sqlalchemy_repos.py`, em `SQLAlchemyPlanRepository.save`, junto das outras atribuições:

```python
        model.fiscal_monthly_limit = plan.fiscal_monthly_limit
```

`app/application/services/admin_service.py`:
- em `create_plan`, no construtor `Plan(...)`, adicionar `fiscal_monthly_limit=dto.fiscal_monthly_limit,`
- em `update_plan`, depois de `max_terminals`:

```python
        if dto.fiscal_monthly_limit is not None: plan.fiscal_monthly_limit = dto.fiscal_monthly_limit
```

- corrigir o mojibake `invÃ¡lido` → `inválido` na mensagem de `update_plan`.

`app/infra/web/routers/admin.py`: criar um conversor único e usá-lo nas três rotas (`list_plans`, `create_plan`, `update_plan`), apagando os três blocos `PlanResponseDTO(...)` duplicados:

```python
def _plan_to_response(p: Plan) -> PlanResponseDTO:
    return PlanResponseDTO(
        id=p.id, name=p.name, type=p.type.value,
        max_markets=p.max_markets, max_terminals=p.max_terminals,
        price_monthly=p.price_monthly, price_180days=p.price_180days,
        price_annual=p.price_annual, fiscal_monthly_limit=p.fiscal_monthly_limit,
        is_active=p.is_active,
    )
```

(`list_plans` vira `return [_plan_to_response(p) for p in plans]`; as outras duas `return _plan_to_response(p)`. Confirmar que `Plan` já é importado no topo de `admin.py`; se não, `from domain.identity import Plan`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_plan_admin_fiscal_limit.py tests/unit/test_fiscal_quota_plan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/application/dtos.py app/infra/repositories/sqlalchemy_repos.py app/application/services/admin_service.py app/infra/web/routers/admin.py tests/unit/test_plan_admin_fiscal_limit.py
git commit -m "fix(plans): persist fiscal_monthly_limit and expose it to admins"
```

---

### Task 3: Campos de apresentação do plano (`description`, `is_recommended`, `display_order`)

**Files:**
- Create: `alembic/versions/20260914_0021_plan_presentation_fields.py`
- Modify: `app/domain/identity.py:36-49`
- Modify: `app/infra/database/models.py:12-24`
- Modify: `app/infra/repositories/sqlalchemy_repos.py:124-164`
- Modify: `app/application/dtos.py` (DTOs de plano)
- Modify: `app/application/services/admin_service.py`
- Modify: `app/infra/web/routers/admin.py` (`_plan_to_response`)
- Test: `tests/unit/test_plan_presentation_fields.py`

**Interfaces:**
- Consumes: `_plan_to_response` e DTOs da Task 2.
- Produces: `Plan.description: Optional[str]`, `Plan.is_recommended: bool`, `Plan.display_order: int`; mesmos campos em `PlanCreateDTO`/`PlanUpdateDTO`/`PlanResponseDTO`. Regra: no máximo um plano com `is_recommended=True`.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import os
import sys
from decimal import Decimal

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.database.setup import Base
import infra.database.models  # noqa: F401
from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository
from application.services.admin_service import AdminService
from application.dtos import PlanCreateDTO, PlanUpdateDTO


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _dto(name, **overrides):
    data = dict(
        name=name, type="pago", max_markets=1, max_terminals=2,
        price_monthly=Decimal("79.90"), price_180days=Decimal("429.90"),
        price_annual=Decimal("799.90"),
    )
    data.update(overrides)
    return PlanCreateDTO(**data)


async def _reload_all(session):
    session.expunge_all()
    return {p.name: p for p in await SQLAlchemyPlanRepository(session).list_all()}


@pytest.mark.asyncio
async def test_presentation_fields_round_trip(session):
    service = AdminService(SQLAlchemyPlanRepository(session))

    await service.create_plan(_dto("Pro", description="  Para quem tem 2 caixas.  ", display_order=2, is_recommended=True))

    pro = (await _reload_all(session))["Pro"]
    assert pro.description == "Para quem tem 2 caixas."
    assert pro.display_order == 2
    assert pro.is_recommended is True


@pytest.mark.asyncio
async def test_only_one_plan_stays_recommended(session):
    service = AdminService(SQLAlchemyPlanRepository(session))
    essencial = await service.create_plan(_dto("Essencial", is_recommended=True))
    pro = await service.create_plan(_dto("Pro"))

    await service.update_plan(pro.id, PlanUpdateDTO(is_recommended=True))

    plans = await _reload_all(session)
    assert plans["Pro"].is_recommended is True
    assert plans["Essencial"].is_recommended is False


@pytest.mark.asyncio
async def test_update_can_clear_description(session):
    service = AdminService(SQLAlchemyPlanRepository(session))
    plan = await service.create_plan(_dto("Pro", description="Texto antigo"))

    await service.update_plan(plan.id, PlanUpdateDTO(description=None))

    assert (await _reload_all(session))["Pro"].description is None


@pytest.mark.asyncio
async def test_update_without_description_keeps_it(session):
    service = AdminService(SQLAlchemyPlanRepository(session))
    plan = await service.create_plan(_dto("Pro", description="Mantém"))

    await service.update_plan(plan.id, PlanUpdateDTO(name="Pro"))

    assert (await _reload_all(session))["Pro"].description == "Mantém"


def test_description_longer_than_280_chars_is_rejected():
    with pytest.raises(ValidationError):
        _dto("Pro", description="x" * 281)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_plan_presentation_fields.py -v`
Expected: FAIL — `Plan` não tem `description`/`is_recommended`/`display_order`.

- [ ] **Step 3: Write the migration**

`alembic/versions/20260914_0021_plan_presentation_fields.py`:

```python
"""Plan presentation fields: description, is_recommended, display_order."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260914_0021"
down_revision: Union[str, Sequence[str], None] = "20260810_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("description", sa.String(length=280), nullable=True))
    op.add_column(
        "plans",
        sa.Column("is_recommended", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "plans",
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("plans", "display_order")
    op.drop_column("plans", "is_recommended")
    op.drop_column("plans", "description")
```

- [ ] **Step 4: Domain, model, repository**

`app/domain/identity.py`, em `Plan`, depois de `fiscal_monthly_limit` e antes de `is_active`:

```python
    description: Optional[str] = None
    is_recommended: bool = False
    display_order: int = 0
```

`app/infra/database/models.py`, em `PlanModel`, depois de `fiscal_monthly_limit`:

```python
    description = Column(String(280), nullable=True)
    is_recommended = Column(Boolean, nullable=False, server_default="false", default=False)
    display_order = Column(Integer, nullable=False, server_default="0", default=0)
```

`app/infra/repositories/sqlalchemy_repos.py`:
- em `save`, junto das atribuições:

```python
        model.description = plan.description
        model.is_recommended = plan.is_recommended
        model.display_order = plan.display_order
```

- em `_to_entity`, no construtor `Plan(...)`:

```python
            description=m.description,
            is_recommended=bool(m.is_recommended),
            display_order=m.display_order or 0,
```

- [ ] **Step 5: DTOs, service e resposta admin**

`app/application/dtos.py`:
- `PlanCreateDTO` ganha:

```python
    description: Optional[str] = Field(None, max_length=280)
    is_recommended: bool = False
    display_order: int = 0
```

- `PlanUpdateDTO` ganha:

```python
    description: Optional[str] = Field(None, max_length=280)
    is_recommended: Optional[bool] = None
    display_order: Optional[int] = None
```

`app/application/services/admin_service.py`:

```python
def _clean_description(value):
    text = (value or "").strip()
    return text or None
```

- `create_plan`: passar `description=_clean_description(dto.description), is_recommended=dto.is_recommended, display_order=dto.display_order` para `Plan(...)`; trocar o `return await self.plan_repo.save(plan)` por:

```python
        saved = await self.plan_repo.save(plan)
        if saved.is_recommended:
            await self._unset_other_recommended(saved.id)
        return saved
```

- `update_plan`, antes do `save`:

```python
        if "description" in dto.model_fields_set:
            plan.description = _clean_description(dto.description)
        if dto.is_recommended is not None:
            plan.is_recommended = dto.is_recommended
        if dto.display_order is not None:
            plan.display_order = dto.display_order
```

  e o mesmo trecho `saved = ...; if saved.is_recommended: await self._unset_other_recommended(saved.id); return saved` no final.

- método novo:

```python
    async def _unset_other_recommended(self, keep_id: uuid.UUID) -> None:
        """Mantém no máximo um plano recomendado."""
        for other in await self.plan_repo.list_all():
            if other.id != keep_id and other.is_recommended:
                other.is_recommended = False
                await self.plan_repo.save(other)
```

`app/infra/web/routers/admin.py`, `_plan_to_response` ganha:

```python
        description=p.description, is_recommended=p.is_recommended,
        display_order=p.display_order,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_plan_presentation_fields.py tests/unit/test_plan_admin_fiscal_limit.py tests/unit/test_plan_access_grace.py -v`
Expected: PASS.

- [ ] **Step 7: Validar a migration num Postgres real**

Run (banco local/staging, nunca produção direto): `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`
Expected: sem erro; `\d plans` mostra as 3 colunas com defaults.

- [ ] **Step 8: Commit**

```bash
git add alembic/versions/20260914_0021_plan_presentation_fields.py app/domain/identity.py app/infra/database/models.py app/infra/repositories/sqlalchemy_repos.py app/application/dtos.py app/application/services/admin_service.py app/infra/web/routers/admin.py tests/unit/test_plan_presentation_fields.py
git commit -m "feat(plans): add description, is_recommended and display_order"
```

---

### Task 4: Catálogo público de planos filtrado no servidor

**Files:**
- Create: `app/application/services/plan_catalog.py`
- Modify: `app/application/dtos.py` (novo `PublicPlanDTO`)
- Modify: `app/infra/web/routers/identity.py:65-68`
- Test: `tests/unit/test_public_plan_catalog.py`

**Interfaces:**
- Consumes: campos de `Plan` das Tasks 2 e 3.
- Produces: `select_public_plans(plans: Iterable[Plan]) -> list[Plan]`; `GET /api/v1/identity/plans` → `List[PublicPlanDTO]` com `id, name, type, description, max_markets, max_terminals, fiscal_monthly_limit, price_monthly, price_180days, price_annual, is_recommended, display_order, is_active`.

**Mudança de contrato a registrar no PR:** preços passam a ser serializados como string decimal (`"79.90"`) pelo Pydantic v2, e somem `created_at`/`updated_at`/`version`. O front já converte com `Number(...)` em `lib/pricing.js` e não usa as datas (verificado).

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal

from fastapi.testclient import TestClient

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.identity import Plan, PlanType
from application.services.plan_catalog import select_public_plans


def _plan(name, type_=PlanType.PAID, active=True, order=0, monthly="79.90"):
    return Plan(
        name=name, type=type_, max_markets=1, max_terminals=1,
        price_monthly=Decimal(monthly), is_active=active, display_order=order,
    )


def test_hides_inactive_and_courtesy_plans():
    plans = [
        _plan("Pago"),
        _plan("Trial", type_=PlanType.TRIAL, monthly="0"),
        _plan("Cortesia", type_=PlanType.FREE, monthly="0"),
        _plan("Antigo", active=False),
    ]

    assert [p.name for p in select_public_plans(plans)] == ["Trial", "Pago"]


def test_orders_by_display_order_then_monthly_price():
    plans = [
        _plan("Caro", order=0, monthly="199.90"),
        _plan("Barato", order=0, monthly="49.90"),
        _plan("Primeiro", order=-1, monthly="999.00"),
    ]

    assert [p.name for p in select_public_plans(plans)] == ["Primeiro", "Barato", "Caro"]


def test_public_route_returns_only_sellable_plans_without_internal_fields():
    from infra.web.main import app
    from infra.web.routers import identity as identity_router

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return [_plan("Pago", monthly="79.90"), _plan("Cortesia", type_=PlanType.FREE)]

    original = identity_router.SQLAlchemyPlanRepository
    identity_router.SQLAlchemyPlanRepository = FakeRepo
    app.dependency_overrides[identity_router.get_db] = lambda: None
    try:
        response = TestClient(app).get("/api/v1/identity/plans")
    finally:
        identity_router.SQLAlchemyPlanRepository = original
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert [p["name"] for p in body] == ["Pago"]
    assert body[0]["type"] == "pago"
    assert body[0]["is_recommended"] is False
    assert "created_at" not in body[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_public_plan_catalog.py -v`
Expected: FAIL com `ModuleNotFoundError: application.services.plan_catalog`.

- [ ] **Step 3: Write minimal implementation**

`app/application/services/plan_catalog.py`:

```python
"""Planos que podem aparecer para visitantes (landing, /precos, /plans)."""

from typing import Iterable, List

from domain.identity import Plan, PlanType

PUBLIC_PLAN_TYPES = (PlanType.PAID, PlanType.TRIAL)


def select_public_plans(plans: Iterable[Plan]) -> List[Plan]:
    """Só planos ativos pagos/trial, na ordem definida pelo admin e depois pelo preço mensal."""
    visible = [p for p in plans if p.is_active and p.type in PUBLIC_PLAN_TYPES]
    return sorted(visible, key=lambda p: (p.display_order, p.price_monthly))
```

`app/application/dtos.py`, logo depois de `PlanResponseDTO`:

```python
class PublicPlanDTO(BaseModel):
    """Plano como visto por visitantes: sem campos internos."""
    id: UUID
    name: str
    type: str
    description: Optional[str] = None
    max_markets: int
    max_terminals: int
    fiscal_monthly_limit: int = 0
    price_monthly: Decimal
    price_180days: Decimal
    price_annual: Decimal
    is_recommended: bool = False
    display_order: int = 0
    is_active: bool = True

    @field_validator('type', mode='before')
    @classmethod
    def parse_plan_type(cls, v: Any) -> str:
        return v.value if hasattr(v, 'value') else str(v)

    class Config:
        from_attributes = True
```

`app/infra/web/routers/identity.py`:

```python
from typing import List
from application.dtos import PublicPlanDTO
from application.services.plan_catalog import select_public_plans

@router.get("/plans", response_model=List[PublicPlanDTO])
async def list_plans(db: AsyncSession = Depends(get_db)):
    repo = SQLAlchemyPlanRepository(db)
    return [PublicPlanDTO.model_validate(p) for p in select_public_plans(await repo.list_all())]
```

(Somar os imports às linhas existentes; não duplicar `from application.dtos import ...`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_public_plan_catalog.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/application/services/plan_catalog.py app/application/dtos.py app/infra/web/routers/identity.py tests/unit/test_public_plan_catalog.py
git commit -m "feat(plans): serve only sellable plans on the public catalog"
```

---

### Task 5: Documento cadastrado mascarado e fallback no checkout recorrente

**Files:**
- Create: `app/application/services/billing_document.py`
- Modify: `app/application/dtos.py:44-52` (`UserResponseDTO`)
- Modify: `app/infra/web/routers/auth.py:195-224`
- Modify: `app/infra/web/routers/billing.py:140-160`
- Test: `tests/unit/test_billing_document.py`

**Interfaces:**
- Produces: `registered_document(user) -> Optional[str]` (11 dígitos), `mask_document(digits) -> Optional[str]` (ex.: `"***.456.789-**"`), `resolve_billing_document(provided, user) -> Optional[str]`; `UserResponseDTO.document_masked: Optional[str] = None`; `POST /billing/subscribe` com `billing_mode=recurring` aceita `document` ausente quando o usuário tem CPF cadastrado.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import os
import sys
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.billing_document import (
    mask_document,
    registered_document,
    resolve_billing_document,
)


def _user(cpf="123.456.789-01"):
    return SimpleNamespace(
        id=uuid.uuid4(), name="Ana", email="ana@t.com", role="owner",
        plan_id=None, plan_expiration=None, is_active=True,
        cpf=SimpleNamespace(value=cpf.replace(".", "").replace("-", "")) if cpf else None,
    )


def test_registered_document_returns_cpf_digits():
    assert registered_document(_user()) == "12345678901"


def test_registered_document_is_none_without_cpf():
    assert registered_document(_user(cpf=None)) is None


def test_mask_keeps_only_middle_digits():
    assert mask_document("12345678901") == "***.456.789-**"
    assert mask_document(None) is None
    assert mask_document("123") is None


def test_typed_document_wins_over_registered_one():
    assert resolve_billing_document("98.765.432/0001-10", _user()) == "98765432000110"


def test_falls_back_to_registered_document_when_omitted():
    assert resolve_billing_document(None, _user()) == "12345678901"
    assert resolve_billing_document("", _user()) == "12345678901"


def test_auth_me_exposes_only_the_masked_document():
    from infra.web.main import app
    from infra.web.routers import auth as auth_router

    app.dependency_overrides[auth_router.get_current_user] = lambda: _user()
    app.dependency_overrides[auth_router.get_db] = lambda: None
    try:
        response = TestClient(app).get("/api/v1/auth/me")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["document_masked"] == "***.456.789-**"
    assert "12345678901" not in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_billing_document.py -v`
Expected: FAIL com `ModuleNotFoundError: application.services.billing_document`.

- [ ] **Step 3: Write minimal implementation**

`app/application/services/billing_document.py`:

```python
"""Documento (CPF) usado na cobrança, sem expor o número completo na API."""

import re
from typing import Any, Optional


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def registered_document(user: Any) -> Optional[str]:
    """CPF cadastrado do usuário, só dígitos. UserModel não persiste CNPJ hoje."""
    cpf = getattr(user, "cpf", None)
    digits = _digits(getattr(cpf, "value", cpf))
    return digits if len(digits) == 11 else None


def mask_document(digits: Optional[str]) -> Optional[str]:
    if not digits or len(digits) != 11:
        return None
    return f"***.{digits[3:6]}.{digits[6:9]}-**"


def resolve_billing_document(provided: Optional[str], user: Any) -> Optional[str]:
    """Documento digitado no checkout; se ausente, o CPF do cadastro."""
    return _digits(provided) or registered_document(user)
```

`app/application/dtos.py`, em `UserResponseDTO`, depois de `is_active`:

```python
    document_masked: Optional[str] = None
```

`app/infra/web/routers/auth.py`:
- import: `from application.services.billing_document import mask_document, registered_document`
- em `read_users_me`, no `UserResponseDTO(...)`, adicionar:

```python
        document_masked=mask_document(registered_document(current_user)),
```

`app/infra/web/routers/billing.py`, no ramo recorrente de `subscribe`:
- trocar

```python
    if not dto.document:
        raise HTTPException(status_code=400, detail="Documento (CPF/CNPJ) é obrigatório para cobrança recorrente.")
```

  por

```python
    from application.services.billing_document import resolve_billing_document
    document = resolve_billing_document(dto.document, current_user)
    if not document:
        raise HTTPException(status_code=400, detail="Documento (CPF/CNPJ) é obrigatório para cobrança recorrente.")
```

- e no `rec.contract(...)` trocar `document=dto.document` por `document=document`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_billing_document.py tests/unit/test_subscribe_endpoint.py tests/unit/test_recurring_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/application/services/billing_document.py app/application/dtos.py app/infra/web/routers/auth.py app/infra/web/routers/billing.py tests/unit/test_billing_document.py
git commit -m "feat(billing): default recurring document to the registered CPF"
```

---

### Task 6: Regressão completa e registro técnico

**Files:**
- Create: `docs/agent_memory/plans-and-paywall-findings.md`

- [ ] **Step 1: Suite completa**

Run: `python -m pytest tests/unit -q`
Expected: tudo verde. Se algo quebrar fora dos arquivos tocados, parar e investigar (superpowers:systematic-debugging) — não ajustar teste alheio para passar.

- [ ] **Step 2: Registrar achados**

`docs/agent_memory/plans-and-paywall-findings.md`:

```markdown
# Planos e paywall — achados (2026-09-14)

- `POST /identity/plans/{id}/subscribe` ativava plano sem cobrança para qualquer dono sem BillingSubscription; restrita a admin (rodada 2 de conversão).
- `PlanAccessService._fallback_from_user` libera acesso por `users.plan_id` quando não existe assinatura local — qualquer caminho que grave `plan_id` sem criar assinatura é um bypass em potencial.
- Limite `0` em `max_markets`/`max_terminals`/`fiscal_monthly_limit` significa "não incluído" (bloqueia), não "ilimitado".
- `SQLAlchemyPlanRepository.save` precisa mapear campo a campo; campo novo em `Plan` sem linha no `save` é silenciosamente descartado.
- `UserModel` não tem coluna `cnpj`; documento cadastrado = CPF.
- `GET /identity/plans` é público e passa por `select_public_plans` (ativos, `pago`/`trial`).
```

- [ ] **Step 3: Commit**

```bash
git add docs/agent_memory/plans-and-paywall-findings.md
git commit -m "docs: record plans and paywall findings"
```

---

## Fora desta rodada (bloqueado por decisão)

| Item | Bloqueio |
|---|---|
| Features diferentes por plano (`get_plan_features` libera tudo para `pago`) | D1 |
| `Plan.slug`, `highlights`, `sales_contact_only`, `badge_label` | D1/D6 — sem uso real até as decisões |
| CNPJ no cadastro (`UserModel.cnpj`) | D4 |
| Ciclo semestral | D2 |
| Eventos de analytics server-side | D9 |
