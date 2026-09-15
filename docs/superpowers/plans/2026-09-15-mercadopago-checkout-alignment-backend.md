# Marketfy Backend: alinhamento do checkout com o Mercado Pago — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir a assinatura recorrente por cartao (que hoje nunca ativa), dar cancelamento e troca de plano seguros com acesso mantido ate o fim do periodo pago, e alinhar o calculo de acesso e validade a eventos reais do billing-core em vez de valores fabricados pelo proprio Marketfy.

**Architecture:** A assinatura por cartao passa a seguir o mesmo padrao assincrono que a fatura ja usa: cria a linha local primeiro, enfileira no billing-core, e um segundo endpoint confirma o checkout quando o job termina. Cancelamento e troca de plano nunca apagam nem travam a assinatura na hora — marcam `cancel_at_period_end` e deixam o acesso seguir ate `expires_at`, calculado localmente a partir da data real de pagamento. `PlanAccessService` passa a escolher a assinatura mais relevante (nao a mais recente) e a tratar `cancel_at_period_end` sem a carencia usada para atraso de pagamento.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 async, Alembic, ARQ, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-15-mercadopago-checkout-alignment-design.md`

**Depende de:** `billing-core/docs/superpowers/plans/2026-09-15-subscription-checkout-support-backend.md` publicado (campo `back_url` e `GET /v1/subscriptions/{id}`) antes das Tasks 3, 6 e 9.

## Global Constraints

- `/billing/success`, `/billing/cancel`, `/billing/expired` continuam existindo (o frontend passa a redirecionar deles para `/billing/retorno`, ver plano do frontend).
- Nenhum endpoint HTTP existente muda de payload de request/response.
- Webhook do billing-core (`POST /billing/webhooks/internal`, assinatura HMAC) continua sendo a unica fonte de verdade para ativacao definitiva; acesso provisorio nunca substitui essa validacao.
- Suite completa verde ao fim de cada task: `python -m pytest tests/unit -q` (a partir de `backend/`).
- Commits convencionais, terminando com `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- Mensagens de erro e logs em portugues.

---

### Task 1: Constante compartilhada de periodo de cobranca

Hoje `PERIOD_DAYS = {"monthly": 30, "semiannual": 180, "annual": 365}` esta duplicado em `recurring_service.py` e `invoice_service.py`. A Task 7 precisa da mesma tabela em `subscription_service.py`; extrair antes de triplicar.

**Files:**
- Create: `app/domain/billing_periods.py`
- Modify: `app/application/services/recurring_service.py`
- Modify: `app/application/services/invoice_service.py`
- Test: `tests/unit/test_billing_periods.py`

**Interfaces:**
- Produces: `PERIOD_DAYS: dict[str, int]`, `CYCLE_MAP: dict[str, str]` (mapa `monthly/semiannual/annual` → `MONTHLY/SEMIANNUALLY/YEARLY` do billing-core, hoje so em `recurring_service.py`).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_billing_periods.py
from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.billing_periods import CYCLE_MAP, PERIOD_DAYS


def test_period_days_covers_all_cycles():
    assert PERIOD_DAYS == {"monthly": 30, "semiannual": 180, "annual": 365}


def test_cycle_map_matches_billing_core_enum_values():
    assert CYCLE_MAP == {"monthly": "MONTHLY", "semiannual": "SEMIANNUALLY", "annual": "YEARLY"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_billing_periods.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'domain.billing_periods'`.

- [ ] **Step 3: Create the shared module**

```python
# app/domain/billing_periods.py
"""Tabelas de periodicidade de cobranca compartilhadas entre invoice e recurring."""

PERIOD_DAYS: dict[str, int] = {"monthly": 30, "semiannual": 180, "annual": 365}

# subscription_type do Marketfy -> SubscriptionType do billing-core
CYCLE_MAP: dict[str, str] = {"monthly": "MONTHLY", "semiannual": "SEMIANNUALLY", "annual": "YEARLY"}
```

- [ ] **Step 4: Point the two existing services at the shared module**

Em `app/application/services/recurring_service.py`, remover as linhas locais `CYCLE_MAP = {...}` e `PERIOD_DAYS = {...}` do topo do arquivo e adicionar `from domain.billing_periods import CYCLE_MAP, PERIOD_DAYS`.

Em `app/application/services/invoice_service.py`, remover a linha local `PERIOD_DAYS = {...}` e adicionar `from domain.billing_periods import PERIOD_DAYS`.

- [ ] **Step 5: Run test to verify it passes, then run full suite**

Run: `python -m pytest tests/unit/test_billing_periods.py tests/unit/test_recurring_service.py tests/unit/test_invoice_service.py -v`
Expected: PASS — os testes existentes de `recurring_service`/`invoice_service` continuam verdes porque `CYCLE_MAP`/`PERIOD_DAYS` sao importados com o mesmo nome e mesmos valores.

- [ ] **Step 6: Commit**

```bash
git add app/domain/billing_periods.py app/application/services/recurring_service.py app/application/services/invoice_service.py tests/unit/test_billing_periods.py
git commit -m "refactor(billing): extract shared PERIOD_DAYS/CYCLE_MAP into domain.billing_periods"
```

---

### Task 2: Colunas novas em `billing_subscriptions`

**Files:**
- Create: `alembic/versions/20260915_0024_billing_subscription_cancel_and_provisional.py`
- Modify: `app/infra/database/models.py`
- Test: `tests/unit/test_billing_subscription_model_columns.py`

**Interfaces:**
- Produces: `BillingSubscriptionModel.cancel_at_period_end: bool`, `.canceled_at: datetime | None`, `.provisional: bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_billing_subscription_model_columns.py
from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.database.models import BillingSubscriptionModel


def test_billing_subscription_has_cancel_and_provisional_columns():
    columns = BillingSubscriptionModel.__table__.columns
    assert columns["cancel_at_period_end"].type.python_type is bool
    assert columns["cancel_at_period_end"].nullable is False
    assert columns["canceled_at"].nullable is True
    assert columns["provisional"].type.python_type is bool
    assert columns["provisional"].nullable is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_billing_subscription_model_columns.py -v`
Expected: FAIL — `KeyError: 'cancel_at_period_end'`.

- [ ] **Step 3: Add the columns to the model**

Em `app/infra/database/models.py`, dentro de `BillingSubscriptionModel`, logo apos a linha `last_event_at = Column(DateTime, nullable=True)`:

```python
    # Cancelamento sem perda imediata de acesso (D1): o usuario mantem acesso
    # ate expires_at; nenhuma fatura/cobranca nova e gerada depois disso.
    cancel_at_period_end = Column(Boolean, default=False, nullable=False)
    canceled_at = Column(DateTime, nullable=True)

    # Acesso provisorio de 24h concedido quando o Mercado Pago autoriza o
    # cartao mas a primeira fatura (e o webhook real) ainda nao chegou.
    provisional = Column(Boolean, default=False, nullable=False)
```

Confirme que `Boolean` ja esta importado de `sqlalchemy` no topo do arquivo (o mesmo import usado por `plans.includes_finance`); se nao estiver, adicionar.

- [ ] **Step 4: Write the migration**

Confira o `down_revision` correto rodando `alembic heads` antes de criar o arquivo (o plano assume `20260914_0022`, cabeça no momento em que este plano foi escrito — se houver uma migration mais nova, use-a como `down_revision`).

```python
# alembic/versions/20260915_0024_billing_subscription_cancel_and_provisional.py
"""billing_subscriptions: cancel_at_period_end, canceled_at, provisional."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260915_0024"
down_revision: Union[str, Sequence[str], None] = "20260914_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "billing_subscriptions",
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("billing_subscriptions", sa.Column("canceled_at", sa.DateTime(), nullable=True))
    op.add_column(
        "billing_subscriptions",
        sa.Column("provisional", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("billing_subscriptions", "provisional")
    op.drop_column("billing_subscriptions", "canceled_at")
    op.drop_column("billing_subscriptions", "cancel_at_period_end")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_billing_subscription_model_columns.py -v`
Expected: PASS.

- [ ] **Step 6: Apply the migration against a local/staging database and commit**

Run: `alembic upgrade head` (contra um banco de desenvolvimento configurado em `DATABASE_URL`).
Expected: aplica sem erro; `alembic current` mostra `20260915_0024`.

```bash
git add alembic/versions/20260915_0024_billing_subscription_cancel_and_provisional.py app/infra/database/models.py tests/unit/test_billing_subscription_model_columns.py
git commit -m "feat(billing): add cancel_at_period_end, canceled_at and provisional to billing_subscriptions"
```

---

### Task 3: `BillingCoreClient` — back_url, cancelar e consultar status

**Files:**
- Modify: `app/infra/clients/billing_core_client.py`
- Test: `tests/unit/test_billing_core_client.py`

**Interfaces:**
- Produces: `BillingCoreClient.create_subscription(..., back_url: str | None = None)`, `BillingCoreClient.cancel_subscription(billing_subscription_id: str, *, idempotency_key: str, reason: str | None = None) -> dict`, `BillingCoreClient.get_subscription_status(billing_subscription_id: str) -> dict`.

- [ ] **Step 1: Write the failing tests**

Adicionar a `tests/unit/test_billing_core_client.py` (reaproveitar os fixtures/mocks de `httpx` ja existentes no arquivo — o padrao ja usado la para testar `create_payment`/`get_job` contra um `httpx.AsyncClient` mockado):

```python
@pytest.mark.asyncio
async def test_create_subscription_includes_back_url_when_given(client_enabled, mock_post):
    await client_enabled.create_subscription(
        system_sub_id="sub-local-1",
        customer_provider_id="cus_1",
        description="Plano Pro",
        value=129.90,
        subscription_type="MONTHLY",
        expires_at=datetime(2099, 1, 1),
        webhook_link="https://api-marketfy.neectify.com/billing/webhooks/internal",
        idempotency_key="bc-sub-sub-local-1",
        back_url="https://app.marketfy.com/billing/retorno?tipo=subscription&ref=sub-local-1",
    )
    sent_json = mock_post.call_args.kwargs["json"]
    assert sent_json["back_url"] == "https://app.marketfy.com/billing/retorno?tipo=subscription&ref=sub-local-1"


@pytest.mark.asyncio
async def test_create_subscription_omits_back_url_when_not_given(client_enabled, mock_post):
    await client_enabled.create_subscription(
        system_sub_id="sub-local-2",
        customer_provider_id="cus_1",
        description="Plano Pro",
        value=129.90,
        subscription_type="MONTHLY",
        expires_at=datetime(2099, 1, 1),
        webhook_link="https://api-marketfy.neectify.com/billing/webhooks/internal",
        idempotency_key="bc-sub-sub-local-2",
    )
    sent_json = mock_post.call_args.kwargs["json"]
    assert "back_url" not in sent_json


@pytest.mark.asyncio
async def test_cancel_subscription_calls_correct_endpoint(client_enabled, mock_post):
    await client_enabled.cancel_subscription("preapproval_1", idempotency_key="cancel-sub-local-1", reason="Solicitado pelo cliente")
    args, kwargs = mock_post.call_args
    assert "/v1/subscriptions/preapproval_1/cancel" in str(args) or "/v1/subscriptions/preapproval_1/cancel" in str(kwargs)


@pytest.mark.asyncio
async def test_get_subscription_status_calls_get_endpoint(client_enabled, mock_get):
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "subscription_id": "local-id", "gateway_status": "ACTIVE",
        "next_due_date": "2026-11-01", "value": "129.90", "cycle": "MONTHLY",
    }
    result = await client_enabled.get_subscription_status("preapproval_1")
    assert result["gateway_status"] == "ACTIVE"
```

Ajustar `client_enabled`/`mock_post`/`mock_get` para os nomes exatos ja usados nos fixtures existentes do arquivo (o arquivo ja tem testes de `create_payment`, `create_customer`, `get_job` — seguir o mesmo padrao de mock de `httpx.AsyncClient.request` la usado; se o arquivo mocka `_request` diretamente em vez de `httpx`, ajustar os asserts acima para inspecionar os argumentos de `_request`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_billing_core_client.py -v -k "back_url or cancel_subscription or get_subscription_status"`
Expected: FAIL — `TypeError`/`AttributeError` (metodos e parametro ainda nao existem).

- [ ] **Step 3: Add `back_url` to `create_subscription`**

Em `app/infra/clients/billing_core_client.py`, no metodo `create_subscription`, adicionar `back_url: str | None = None` a assinatura e, no corpo, so incluir a chave quando presente:

```python
        payload = {
            "system": self._system,
            "system_sub_id": system_sub_id,
            "customer_provider_id": customer_provider_id,
            "description": description,
            "value": value,
            "subscription_type": subscription_type,
            "expires_at": expires_at.isoformat(),
            "webhook_link": webhook_link,
        }
        if back_url:
            payload["back_url"] = back_url
```

(substitui o dict `payload` que ja existe no metodo, mantendo o resto igual).

- [ ] **Step 4: Add `cancel_subscription`**

Logo apos `create_subscription`, no mesmo arquivo:

```python
    async def cancel_subscription(
        self,
        billing_subscription_id: str,
        *,
        idempotency_key: str,
        reason: str | None = None,
    ) -> Dict[str, Any]:
        """Cancela assinatura no Billing Core.

        POST /v1/subscriptions/{id}/cancel
        """
        if not self._enabled:
            return {"job_id": f"job_mock_cancel_{uuid.uuid4().hex[:12]}"}

        payload: Dict[str, Any] = {}
        if reason:
            payload["reason"] = reason

        return await self._request(
            "POST",
            f"/v1/subscriptions/{billing_subscription_id}/cancel",
            json=payload,
            idempotency_key=idempotency_key,
        )
```

- [ ] **Step 5: Add `get_subscription_status`**

```python
    async def get_subscription_status(self, billing_subscription_id: str) -> Dict[str, Any]:
        """Consulta o status ao vivo da assinatura no gateway.

        GET /v1/subscriptions/{id} — requer scope subscriptions:read no
        billing-core (docs/agent_memory/mercadopago-adapter-contract.md la).
        """
        if not self._enabled:
            return {
                "subscription_id": billing_subscription_id,
                "gateway_status": "ACTIVE",
                "next_due_date": datetime.utcnow().date().isoformat(),
                "value": "0.00",
                "cycle": "MONTHLY",
            }

        return await self._request("GET", f"/v1/subscriptions/{billing_subscription_id}")
```

- [ ] **Step 6: Run tests to verify they pass, then run the full suite**

Run: `python -m pytest tests/unit/test_billing_core_client.py -v`
Expected: PASS.

Run: `python -m pytest tests/unit -q`
Expected: todos os testes passam.

- [ ] **Step 7: Commit**

```bash
git add app/infra/clients/billing_core_client.py tests/unit/test_billing_core_client.py
git commit -m "feat(billing-client): support back_url, cancel_subscription and get_subscription_status"
```

---

### Task 4: Selecao de assinatura vigente e ajustes de repositorio

**Files:**
- Create: `app/application/services/billing_subscription_selection.py`
- Modify: `app/infra/repositories/billing_repo.py`
- Test: `tests/unit/test_billing_subscription_selection.py`
- Test: `tests/unit/test_billing_subscription_repo_queries.py` (novo — cobre so a exclusao de `cancel_at_period_end` na query de faturas a vencer; o resto do repositorio ja tem cobertura em `tests/unit/test_billing_invoice_repo.py` no que for equivalente)

**Interfaces:**
- Produces: `select_current_subscription(subs: list) -> subscription | None` (funcao pura), `SQLAlchemyBillingSubscriptionRepository.get_current_for_owner(owner_id) -> BillingSubscriptionModel | None`, `.list_pending_recurring_with_gateway_id() -> list[BillingSubscriptionModel]`.
- Consumes: nenhuma dependencia nova.

- [ ] **Step 1: Write the failing test for the pure selection function**

```python
# tests/unit/test_billing_subscription_selection.py
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.billing_subscription_selection import select_current_subscription


@dataclass
class StubSub:
    status: str
    updated_at: datetime
    expires_at: datetime | None = None
    cancel_at_period_end: bool = False
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def test_returns_none_for_empty_list():
    assert select_current_subscription([]) is None


def test_prefers_active_over_abandoned_pending():
    now = datetime.utcnow()
    active = StubSub(status="active", updated_at=now - timedelta(days=10), expires_at=now + timedelta(days=20))
    abandoned_pending = StubSub(status="pending", updated_at=now)
    result = select_current_subscription([abandoned_pending, active])
    assert result is active


def test_canceled_at_period_end_with_time_left_beats_a_new_pending_checkout():
    now = datetime.utcnow()
    canceling = StubSub(
        status="active", updated_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=5), cancel_at_period_end=True,
    )
    new_pending = StubSub(status="pending", updated_at=now)
    result = select_current_subscription([new_pending, canceling])
    assert result is canceling


def test_among_same_priority_picks_most_recently_updated():
    now = datetime.utcnow()
    older = StubSub(status="pending", updated_at=now - timedelta(hours=2))
    newer = StubSub(status="pending", updated_at=now)
    result = select_current_subscription([older, newer])
    assert result is newer
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_billing_subscription_selection.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the pure selection function**

```python
# app/application/services/billing_subscription_selection.py
"""Escolhe, entre as assinaturas locais de um owner, a que deve governar o
acesso — nunca simplesmente a mais recente por updated_at (um checkout
recorrente aberto e abandonado nao pode derrubar quem ja tinha acesso)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence


def _priority_rank(sub: Any, *, now: datetime) -> int:
    if sub.status in ("active", "trialing"):
        return 0
    if sub.cancel_at_period_end and sub.expires_at is not None and sub.expires_at >= now:
        return 1
    if sub.status == "past_due":
        return 2
    if sub.status == "pending":
        return 3
    return 4


def select_current_subscription(subs: Sequence[Any]) -> Optional[Any]:
    if not subs:
        return None
    now = datetime.utcnow()
    return min(
        subs,
        key=lambda sub: (_priority_rank(sub, now=now), -sub.updated_at.timestamp()),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_billing_subscription_selection.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing repo test**

```python
# tests/unit/test_billing_subscription_repo_queries.py
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from infra.database.models import Base, BillingSubscriptionModel


@pytest.mark.asyncio
async def test_list_invoice_subs_expiring_within_excludes_cancel_at_period_end():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository

    owner_id = uuid.uuid4()
    now = datetime.utcnow()
    async with Session() as session:
        repo = SQLAlchemyBillingSubscriptionRepository(session)
        keep = BillingSubscriptionModel(
            id=uuid.uuid4(), owner_id=owner_id, billing_mode="invoice", status="active",
            expires_at=now + timedelta(days=1), cancel_at_period_end=False,
        )
        skip = BillingSubscriptionModel(
            id=uuid.uuid4(), owner_id=owner_id, billing_mode="invoice", status="active",
            expires_at=now + timedelta(days=1), cancel_at_period_end=True,
        )
        session.add_all([keep, skip])
        await session.commit()

        result = await repo.list_invoice_subs_expiring_within(now + timedelta(days=5))
        result_ids = {s.id for s in result}
        assert keep.id in result_ids
        assert skip.id not in result_ids

    await engine.dispose()
```

Se a suite do projeto ja tiver um fixture padrao de engine SQLite em memoria para testes de repositorio (conferir `tests/unit/test_billing_invoice_repo.py`, que provavelmente ja resolve isso), reaproveitar esse fixture em vez de duplicar a criacao do engine acima.

- [ ] **Step 6: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_billing_subscription_repo_queries.py -v`
Expected: FAIL — a query atual nao filtra `cancel_at_period_end`, entao `skip` aparece no resultado.

- [ ] **Step 7: Update the query and add the two new repo methods**

Em `app/infra/repositories/billing_repo.py`, dentro de `list_invoice_subs_expiring_within`, adicionar a condicao `BillingSubscriptionModel.cancel_at_period_end.is_(False)` ao `where(...)` existente.

Adicionar dois metodos novos a `SQLAlchemyBillingSubscriptionRepository`:

```python
    async def get_current_for_owner(self, owner_id: uuid.UUID) -> Optional[BillingSubscriptionModel]:
        """Assinatura que deve governar o acesso do owner agora (nao a mais recente)."""
        from application.services.billing_subscription_selection import select_current_subscription

        subs = await self.list_by_owner(owner_id)
        return select_current_subscription(subs)

    async def list_pending_recurring_with_gateway_id(self) -> List[BillingSubscriptionModel]:
        """Assinaturas recorrentes com checkout ja criado no gateway, ainda pendentes
        de confirmacao — candidatas ao acesso provisorio (D2)."""
        result = await self._db.execute(
            select(BillingSubscriptionModel).where(
                BillingSubscriptionModel.billing_mode == "recurring",
                BillingSubscriptionModel.status == "pending",
                BillingSubscriptionModel.billing_subscription_id.isnot(None),
            )
        )
        return list(result.scalars().all())
```

- [ ] **Step 8: Run tests to verify they pass, then run the full suite**

Run: `python -m pytest tests/unit/test_billing_subscription_repo_queries.py tests/unit/test_billing_subscription_selection.py -v`
Expected: PASS.

Run: `python -m pytest tests/unit -q`
Expected: todos os testes passam.

- [ ] **Step 9: Commit**

```bash
git add app/application/services/billing_subscription_selection.py app/infra/repositories/billing_repo.py tests/unit/test_billing_subscription_selection.py tests/unit/test_billing_subscription_repo_queries.py
git commit -m "feat(billing): pick the subscription that governs access instead of the most recent row"
```

---

### Task 5: `RecurringService` assincrono de verdade

Corrige o bug critico: hoje `contract()` consulta o job do billing-core uma unica vez, logo apos criar, e o link quase nunca esta pronto ainda.

**Files:**
- Modify: `app/application/services/recurring_service.py`
- Test: `tests/unit/test_recurring_service.py` (modificar os testes existentes que dependem do comportamento antigo e adicionar os novos abaixo)

**Interfaces:**
- Consumes: `BillingCoreClient.create_subscription(..., back_url=...)` (Task 3), `PERIOD_DAYS`/`CYCLE_MAP` de `domain.billing_periods` (Task 1).
- Produces: `RecurringService.contract(user, plan_id, subscription_type, document, idempotency_key) -> {"subscription_id": <id local>, "job_id": ...}` (sem `checkout_url` — muda de contrato em relacao a versao antiga, e por isso a Task 6 atualiza o router e o frontend consome o endpoint novo). `RecurringService.ensure_checkout(local_subscription_id) -> {"status": ..., "checkout_url": ...}`.

- [ ] **Step 1: Write the failing tests**

Substituir, em `tests/unit/test_recurring_service.py`, o teste `test_contract_creates_customer_and_subscription` (que hoje espera `result["checkout_url"]` vindo direto de `contract()`) pelos testes abaixo — manter os outros testes do arquivo que nao dependem do polling inline (`test_cycle_map_uses_billing_enum_values` continua igual):

```python
@pytest.mark.asyncio
async def test_contract_creates_local_subscription_and_enqueues_without_polling():
    plan = StubPlan()
    user = StubUser()
    bc = AsyncMock()
    bc.create_customer.return_value = {"provider_customer_id": "cus_1"}
    bc.create_subscription.return_value = {"job_id": "job_1"}

    sub_repo = SubRepo()
    svc = RecurringService(sub_repo, PlanRepo(plan), UserRepo(), bc, StubSettings())
    result = await svc.contract(user, plan.id, "monthly", document="12345678901", idempotency_key="idem-1")

    assert "checkout_url" not in result
    assert result["job_id"] == "job_1"
    assert result["subscription_id"] is not None
    bc.get_job.assert_not_awaited()

    saved = sub_repo.saved[-1]
    assert saved.status == "pending"
    bc.create_subscription.assert_awaited_once()
    _, kwargs = bc.create_subscription.call_args
    assert kwargs["system_sub_id"] == str(saved.id)
    assert kwargs["idempotency_key"] == f"bc-sub-{saved.id}"


@pytest.mark.asyncio
async def test_contract_passes_back_url_derived_from_local_subscription_id():
    plan = StubPlan()
    user = StubUser()
    bc = AsyncMock()
    bc.create_customer.return_value = {"provider_customer_id": "cus_1"}
    bc.create_subscription.return_value = {"job_id": "job_1"}

    settings = StubSettings()
    settings.PUBLIC_FRONTEND_URL = "https://app.marketfy.com"
    svc = RecurringService(SubRepo(), PlanRepo(plan), UserRepo(), bc, settings)
    result = await svc.contract(user, plan.id, "monthly", document="12345678901", idempotency_key="idem-1")

    _, kwargs = bc.create_subscription.call_args
    assert kwargs["back_url"] == f"https://app.marketfy.com/billing/retorno?tipo=subscription&ref={result['subscription_id']}"


@pytest.mark.asyncio
async def test_ensure_checkout_polls_job_and_persists_checkout_url():
    from infra.database.models import BillingSubscriptionModel

    local_id = uuid.uuid4()
    sub_repo = AsyncMock()
    local_sub = BillingSubscriptionModel(
        id=local_id, owner_id=uuid.uuid4(), billing_mode="recurring",
        status="pending", billing_job_id="job_1", checkout_url=None,
    )
    sub_repo.get_by_id.return_value = local_sub

    bc = AsyncMock()
    bc.get_job.return_value = {
        "status": "completed",
        "result": {"checkout_url": "https://pay/mp/x", "subscription_id": "preapproval_1"},
    }

    svc = RecurringService(sub_repo, PlanRepo(StubPlan()), UserRepo(), bc, StubSettings())
    result = await svc.ensure_checkout(local_id)

    assert result == {"status": "completed", "checkout_url": "https://pay/mp/x"}
    assert local_sub.billing_subscription_id == "preapproval_1"
    assert local_sub.checkout_url == "https://pay/mp/x"
    sub_repo.save.assert_awaited_once_with(local_sub)


@pytest.mark.asyncio
async def test_ensure_checkout_returns_pending_when_job_not_done():
    from infra.database.models import BillingSubscriptionModel

    local_id = uuid.uuid4()
    sub_repo = AsyncMock()
    local_sub = BillingSubscriptionModel(
        id=local_id, owner_id=uuid.uuid4(), billing_mode="recurring",
        status="pending", billing_job_id="job_1", checkout_url=None,
    )
    sub_repo.get_by_id.return_value = local_sub

    bc = AsyncMock()
    bc.get_job.return_value = {"status": "processing"}

    svc = RecurringService(sub_repo, PlanRepo(StubPlan()), UserRepo(), bc, StubSettings())
    result = await svc.ensure_checkout(local_id)

    assert result == {"status": "processing", "checkout_url": None}
    sub_repo.save.assert_not_awaited()
```

Adicionar `import uuid` ao topo do arquivo de teste se ainda nao existir (ja e importado — conferir).

`SubRepo` (fixture ja existente no arquivo) precisa de um `get_by_id` alem do `save`/`get_by_idempotency_key` ja existentes; adicionar:

```python
class SubRepo:
    def __init__(self):
        self.saved = []
        self._by_id = {}
    async def get_by_idempotency_key(self, key):
        return None
    async def get_by_id(self, sub_id):
        return self._by_id.get(sub_id)
    async def save(self, sub):
        self.saved.append(sub)
        self._by_id[sub.id] = sub
        return sub
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_recurring_service.py -v`
Expected: FAIL — `contract()` ainda tenta chamar `bc.get_job`, `ensure_checkout` nao existe.

- [ ] **Step 3: Rewrite `contract()`**

Em `app/application/services/recurring_service.py`, substituir o corpo de `contract()` (do `customer_provider_id = await self._ensure_customer(...)` ate o fim do metodo) por:

```python
        customer_provider_id = await self._ensure_customer(user, doc)
        value = _price(plan, subscription_type)
        webhook_link = self._webhook_link()

        from infra.database.models import BillingSubscriptionModel
        sub = BillingSubscriptionModel(
            owner_id=user.id, plan_id=plan_id,
            billing_system=self._settings.BILLING_CORE_SYSTEM,
            billing_mode="recurring",
            customer_provider_id=customer_provider_id,
            status="pending",
            subscription_type=subscription_type,
            value=value,
            expires_at=None,
            idempotency_key=idempotency_key,
        )
        sub = await self._sub.save(sub)
        sub.billing_system_sub_id = str(sub.id)

        job = await self._bc.create_subscription(
            system_sub_id=str(sub.id),
            customer_provider_id=customer_provider_id,
            description=f"Marketfy {plan.name}",
            value=float(value),
            subscription_type=CYCLE_MAP[subscription_type],
            expires_at=datetime.utcnow() + timedelta(days=365 * 5),  # teto do billing-core; a validade real e local
            webhook_link=webhook_link,
            idempotency_key=f"bc-sub-{sub.id}",
            back_url=self._back_url(sub.id),
        )
        sub.billing_job_id = job.get("job_id")
        sub = await self._sub.save(sub)

        await self._analytics.track_event(
            str(user.id), "subscription_created",
            {"plan_id": str(plan_id), "subscription_type": subscription_type, "billing_mode": "recurring"},
        )

        return {"subscription_id": str(sub.id), "job_id": sub.billing_job_id}

    def _back_url(self, local_subscription_id) -> str | None:
        base = getattr(self._settings, "PUBLIC_FRONTEND_URL", None)
        if not base:
            return None
        return f"{base.rstrip('/')}/billing/retorno?tipo=subscription&ref={local_subscription_id}"

    async def ensure_checkout(self, local_subscription_id) -> Dict[str, Any]:
        """Consulta o job do billing-core e persiste o checkout_url quando pronto.

        Chamado pelo endpoint POST /billing/subscriptions/{id}/checkout, com o
        mesmo padrao de polling que InvoiceService.refresh_checkout ja usa.
        """
        sub = await self._sub.get_by_id(local_subscription_id)
        if sub is None:
            return {"status": "not_found", "checkout_url": None}
        if sub.checkout_url:
            return {"status": "completed", "checkout_url": sub.checkout_url}
        if not sub.billing_job_id:
            return {"status": "pending", "checkout_url": None}

        job = await self._bc.get_job(sub.billing_job_id)
        job_status = job.get("status", "processing")
        if job_status != "completed":
            return {"status": job_status, "checkout_url": None}

        result = job.get("result") or {}
        checkout_url = result.get("checkout_url")
        billing_subscription_id = result.get("subscription_id")
        if checkout_url:
            sub.checkout_url = checkout_url
        if billing_subscription_id:
            sub.billing_subscription_id = billing_subscription_id
        if checkout_url or billing_subscription_id:
            await self._sub.save(sub)

        return {"status": "completed", "checkout_url": checkout_url}
```

Remover o metodo `_poll_subscription_job` antigo (nao e mais usado). Remover tambem o `expires_at = datetime.utcnow() + timedelta(days=365 * 5)` que havia como variavel solta antes do `job = await self._bc.create_subscription(...)` na versao anterior — ele agora e inline na chamada, como mostrado acima.

- [ ] **Step 4: Run tests to verify they pass, then run the full suite**

Run: `python -m pytest tests/unit/test_recurring_service.py -v`
Expected: PASS.

Run: `python -m pytest tests/unit -q`
Expected: todos os testes passam (a Task 6 ainda vai atualizar o router que chama `contract()` — ate la, `billing.py` continua funcionando porque o retorno de `contract()` so perde a chave `checkout_url`, que o router de hoje repassa direto ao front sem validar; a Task 6 corrige o router no mesmo commit logico).

- [ ] **Step 5: Commit**

```bash
git add app/application/services/recurring_service.py tests/unit/test_recurring_service.py
git commit -m "fix(billing): create the local subscription before calling billing-core and stop polling the job inline"
```

---

### Task 6: Endpoints novos e ajuste do `subscribe`

**Files:**
- Modify: `app/infra/web/routers/billing.py`
- Test: `tests/unit/test_subscription_checkout_endpoints.py`

**Interfaces:**
- Consumes: `RecurringService.contract`/`.ensure_checkout` (Task 5), `BillingCoreClient.cancel_subscription`/`.get_subscription_status` (Task 3), `SQLAlchemyBillingSubscriptionRepository.get_current_for_owner` (Task 4).
- Produces: `POST /billing/subscriptions/{id}/checkout`, `POST /billing/subscription/cancel`, `GET /billing/subscriptions/{id}/status`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_subscription_checkout_endpoints.py
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from infra.database.models import BillingSubscriptionModel
from infra.web.routers import billing
from infra.web.dependencies import get_current_user, get_db


@pytest.fixture
def app_with_billing_router():
    app = FastAPI()
    app.include_router(billing.router, prefix="/api/v1/billing")
    return app


class FakeUser:
    id = uuid.uuid4()


@pytest.mark.asyncio
async def test_subscriptions_checkout_endpoint_confirms_pending_checkout(app_with_billing_router, monkeypatch):
    sub_id = uuid.uuid4()
    local_sub = BillingSubscriptionModel(
        id=sub_id, owner_id=FakeUser.id, billing_mode="recurring",
        status="pending", billing_job_id="job_1", checkout_url=None,
    )

    recurring_service = AsyncMock()
    recurring_service.ensure_checkout.return_value = {"status": "completed", "checkout_url": "https://pay/mp/x"}
    monkeypatch.setattr(billing, "RecurringService", lambda **kwargs: recurring_service)

    sub_repo = AsyncMock()
    sub_repo.get_by_id.return_value = local_sub
    monkeypatch.setattr(
        "infra.repositories.billing_repo.SQLAlchemyBillingSubscriptionRepository",
        lambda db: sub_repo,
    )

    app_with_billing_router.dependency_overrides[get_current_user] = lambda: FakeUser()
    app_with_billing_router.dependency_overrides[get_db] = lambda: AsyncMock()

    transport = ASGITransport(app=app_with_billing_router)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/v1/billing/subscriptions/{sub_id}/checkout")

    assert response.status_code == 202
    assert response.json()["checkout_url"] == "https://pay/mp/x"


@pytest.mark.asyncio
async def test_subscriptions_checkout_endpoint_404_for_other_owner(app_with_billing_router, monkeypatch):
    sub_id = uuid.uuid4()
    local_sub = BillingSubscriptionModel(
        id=sub_id, owner_id=uuid.uuid4(), billing_mode="recurring", status="pending",
    )
    sub_repo = AsyncMock()
    sub_repo.get_by_id.return_value = local_sub
    monkeypatch.setattr(
        "infra.repositories.billing_repo.SQLAlchemyBillingSubscriptionRepository",
        lambda db: sub_repo,
    )
    app_with_billing_router.dependency_overrides[get_current_user] = lambda: FakeUser()
    app_with_billing_router.dependency_overrides[get_db] = lambda: AsyncMock()

    transport = ASGITransport(app=app_with_billing_router)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/v1/billing/subscriptions/{sub_id}/checkout")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cancel_subscription_marks_cancel_at_period_end_and_keeps_access(app_with_billing_router, monkeypatch):
    now = datetime.utcnow()
    current_sub = BillingSubscriptionModel(
        id=uuid.uuid4(), owner_id=FakeUser.id, billing_mode="recurring", status="active",
        billing_subscription_id="preapproval_1", expires_at=now + timedelta(days=10),
        cancel_at_period_end=False,
    )
    sub_repo = AsyncMock()
    sub_repo.get_current_for_owner.return_value = current_sub
    monkeypatch.setattr(
        "infra.repositories.billing_repo.SQLAlchemyBillingSubscriptionRepository",
        lambda db: sub_repo,
    )

    bc_client = AsyncMock()
    monkeypatch.setattr(billing, "BillingCoreClient", lambda: bc_client)

    app_with_billing_router.dependency_overrides[get_current_user] = lambda: FakeUser()
    app_with_billing_router.dependency_overrides[get_db] = lambda: AsyncMock()

    transport = ASGITransport(app=app_with_billing_router)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/billing/subscription/cancel")

    assert response.status_code == 200
    assert current_sub.cancel_at_period_end is True
    assert current_sub.canceled_at is not None
    assert current_sub.status == "active"  # acesso continua ate expires_at
    bc_client.cancel_subscription.assert_awaited_once()
    sub_repo.save.assert_awaited_once_with(current_sub)


@pytest.mark.asyncio
async def test_cancel_subscription_404_when_nothing_to_cancel(app_with_billing_router, monkeypatch):
    sub_repo = AsyncMock()
    sub_repo.get_current_for_owner.return_value = None
    monkeypatch.setattr(
        "infra.repositories.billing_repo.SQLAlchemyBillingSubscriptionRepository",
        lambda db: sub_repo,
    )
    app_with_billing_router.dependency_overrides[get_current_user] = lambda: FakeUser()
    app_with_billing_router.dependency_overrides[get_db] = lambda: AsyncMock()

    transport = ASGITransport(app=app_with_billing_router)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/billing/subscription/cancel")

    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_subscription_checkout_endpoints.py -v`
Expected: FAIL — as rotas ainda nao existem (404 generico do FastAPI para path nao registrado, ou `AttributeError` para `billing.RecurringService` nao importado no modulo).

- [ ] **Step 3: Import what the router needs**

No topo de `app/infra/web/routers/billing.py`, garantir os imports (alguns ja estao la — conferir antes de duplicar):

```python
from application.services.recurring_service import RecurringService
```

(o resto — `BillingCoreClient`, `SQLAlchemyBillingSubscriptionRepository`, `settings` — ja esta importado no arquivo).

- [ ] **Step 4: Add `POST /subscriptions/{id}/checkout`**

Apos o endpoint `ensure_invoice_checkout` existente, adicionar:

```python
@router.post("/subscriptions/{subscription_id}/checkout", status_code=status.HTTP_202_ACCEPTED)
async def ensure_subscription_checkout(
    subscription_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirma o checkout de uma assinatura recorrente ja criada em POST /subscribe.

    O front chama esta rota em polling ate `checkout_url` sair, igual ja faz
    hoje para faturas via POST /invoices/{id}/checkout.
    """
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository

    repo = SQLAlchemyBillingSubscriptionRepository(db)
    sub = await repo.get_by_id(subscription_id)
    if sub is None or sub.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Assinatura não encontrada.")

    rec = RecurringService(
        subscription_repo=repo, plan_repo=None, user_repo=None,
        billing_client=BillingCoreClient(), settings=settings,
    )
    try:
        result = await rec.ensure_checkout(subscription_id)
        await db.commit()
    except BillingCoreError as exc:
        logger.warning(f"[billing] Billing Core indisponível ao preparar assinatura={subscription_id}: {exc}")
        raise HTTPException(status_code=503, detail="Serviço de cobrança temporariamente indisponível.")

    return {
        "subscription_id": str(subscription_id),
        "status": result.get("status"),
        "checkout_url": result.get("checkout_url"),
    }
```

`RecurringService.ensure_checkout` so usa `self._sub`/`self._bc`/`self._settings` (ver Task 5) — passar `plan_repo=None, user_repo=None` e seguro porque nenhum dos dois e tocado por esse metodo.

- [ ] **Step 5: Add `POST /subscription/cancel`**

```python
@router.post("/subscription/cancel", status_code=status.HTTP_200_OK)
async def cancel_subscription(
    request: Request,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
):
    """Cancela a assinatura vigente do usuário. O acesso continua até
    expires_at (D1) — nada é bloqueado nesta chamada."""
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository

    repo = SQLAlchemyBillingSubscriptionRepository(db)
    sub = await repo.get_current_for_owner(current_user.id)
    if sub is None or sub.status in ("canceled", "expired", "failed"):
        raise HTTPException(status_code=404, detail="Nenhuma assinatura ativa para cancelar.")
    if sub.cancel_at_period_end:
        return {"status": "already_canceled", "expires_at": sub.expires_at.isoformat() if sub.expires_at else None}

    sub.cancel_at_period_end = True
    sub.canceled_at = datetime.utcnow()

    if sub.billing_mode == "recurring" and sub.billing_subscription_id:
        try:
            await BillingCoreClient().cancel_subscription(
                sub.billing_subscription_id,
                idempotency_key=f"cancel-{sub.id}",
                reason="Cancelado pelo cliente via Marketfy.",
            )
        except BillingCoreError as exc:
            logger.warning(f"[billing] Falha ao cancelar assinatura={sub.id} no Billing Core: {exc}")
            raise HTTPException(status_code=503, detail="Serviço de cobrança temporariamente indisponível.")

    await repo.save(sub)
    await db.commit()

    await record_audit_event(
        audit, request, actor=current_user, action="billing.subscription.cancel",
        resource_type="billing_subscription", resource_id=str(sub.id),
        result="success", metadata={"billing_mode": sub.billing_mode},
    )

    return {"status": "canceled_at_period_end", "expires_at": sub.expires_at.isoformat() if sub.expires_at else None}
```

- [ ] **Step 6: Add `GET /subscriptions/{id}/status` (acesso provisorio, D2)**

```python
@router.get("/subscriptions/{subscription_id}/status")
async def get_subscription_status_live(
    subscription_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Consulta o status ao vivo no gateway e concede acesso provisório de 24h
    quando o cartão já foi autorizado mas a primeira fatura ainda não chegou
    (D2). O webhook real, quando chegar, recalcula expires_at e substitui o
    valor provisório (ver SubscriptionService.process_recurring_event)."""
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository

    repo = SQLAlchemyBillingSubscriptionRepository(db)
    sub = await repo.get_by_id(subscription_id)
    if sub is None or sub.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Assinatura não encontrada.")

    if sub.status != "pending" or not sub.billing_subscription_id:
        return {"status": sub.status, "provisional": bool(sub.provisional)}

    try:
        remote = await BillingCoreClient().get_subscription_status(sub.billing_subscription_id)
    except BillingCoreError as exc:
        logger.warning(f"[billing] Falha ao consultar status ao vivo da assinatura={sub.id}: {exc}")
        return {"status": sub.status, "provisional": bool(sub.provisional)}

    if remote.get("gateway_status") == "ACTIVE":
        sub.status = "active"
        sub.provisional = True
        sub.expires_at = datetime.utcnow() + timedelta(hours=24)
        await repo.save(sub)
        await db.commit()

    return {"status": sub.status, "provisional": bool(sub.provisional)}
```

Adicionar `from datetime import timedelta` ao topo do arquivo se `datetime` ja estiver importado sem `timedelta` (conferir — o arquivo ja importa `from datetime import datetime`).

- [ ] **Step 7: Point `POST /subscribe` (recurring branch) at the new contract() shape**

No mesmo arquivo, dentro de `subscribe()`, o bloco `# recurring` ja chama `rec.contract(...)` e devolve `result` direto — isso continua funcionando sem alteracao porque `result` agora e `{"subscription_id", "job_id"}` (sem `checkout_url`) e o endpoint so repassa o dict como esta. Nenhuma mudanca de codigo aqui; so confirmar (proximo passo) que o teste de integracao do `subscribe` recorrente nao afirma mais a presenca de `checkout_url` na resposta direta.

- [ ] **Step 8: Update `tests/unit/test_subscribe_endpoint.py` expectations if needed**

Abrir `tests/unit/test_subscribe_endpoint.py` e conferir se algum teste asserta `data["checkout_url"]` para `billing_mode="recurring"`. Se sim, trocar a asserção para conferir `data["subscription_id"]` e `data["job_id"]` presentes e `"checkout_url" not in data`, documentando no proprio teste que o link agora vem de `POST /billing/subscriptions/{id}/checkout`.

- [ ] **Step 9: Run tests to verify they pass, then run the full suite**

Run: `python -m pytest tests/unit/test_subscription_checkout_endpoints.py tests/unit/test_subscribe_endpoint.py -v`
Expected: PASS.

Run: `python -m pytest tests/unit -q`
Expected: todos os testes passam.

- [ ] **Step 10: Commit**

```bash
git add app/infra/web/routers/billing.py tests/unit/test_subscription_checkout_endpoints.py tests/unit/test_subscribe_endpoint.py
git commit -m "feat(billing): add subscription checkout confirmation, cancel and live status endpoints"
```

---

### Task 7: `SubscriptionService.process_recurring_event` — validade local e bloqueio imediato em estorno/chargeback

**Files:**
- Modify: `app/application/services/subscription_service.py`
- Test: `tests/unit/test_internal_webhook_recurring.py` (modificar/estender)

**Interfaces:**
- Consumes: `PERIOD_DAYS` de `domain.billing_periods` (Task 1).
- Produces: `process_recurring_event` deixa de escrever `subscription_expires_at` recebido do payload; calcula `expires_at = payment_date + PERIOD_DAYS[local_sub.subscription_type]` para `PAYMENT_RECEIVED`. `PAYMENT_REFUNDED`, `PAYMENT_CHARGEBACK_REQUESTED` e `SUBSCRIPTION_INACTIVATED` (quando nao ha `cancel_at_period_end` previo, isto e, cancelamento do lado do gateway) passam a chamar `_lock_immediately(local_sub)` em vez de setar `status="canceled"`.

- [ ] **Step 1: Write the failing tests**

Adicionar a `tests/unit/test_internal_webhook_recurring.py` (reaproveitando `StubSub`/`StubUser`/`SubRepo`/`EventRepo`/`UserRepo`/`PlanRepo` ja definidos no arquivo — acrescentar os campos que faltam ao `StubSub`):

```python
@dataclass
class StubSub:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    owner_id: uuid.UUID = field(default_factory=uuid.uuid4)
    plan_id: Optional[uuid.UUID] = None
    billing_subscription_id: Optional[str] = "sub_bc_1"
    status: str = "pending"
    expires_at: Optional[datetime] = None
    subscription_type: str = "monthly"
    cancel_at_period_end: bool = False
    canceled_at: Optional[datetime] = None


@pytest.mark.asyncio
async def test_payment_received_computes_expires_at_locally_ignoring_payload():
    sub = StubSub()
    user = StubUser(id=sub.owner_id)
    svc = SubscriptionService(UserRepo(user), PlanRepo(), SubRepo(sub), EventRepo())

    # o payload manda um valor absurdo (5 anos, como o Marketfy enviava para
    # o billing-core) — o Marketfy nunca deve confiar nesse eco.
    bogus_far_future = datetime(2099, 1, 1)
    await svc.process_recurring_event(
        event="PAYMENT_RECEIVED", billing_subscription_id="sub_bc_1",
        subscription_expires_at=bogus_far_future, payment_date=datetime(2026, 10, 1), raw_payload={},
    )

    assert sub.expires_at == datetime(2026, 10, 31)  # +30 dias, monthly
    assert sub.status == "active"


@pytest.mark.asyncio
async def test_payment_refunded_locks_access_immediately():
    sub = StubSub(status="active", expires_at=datetime(2026, 12, 1))
    user = StubUser(id=sub.owner_id)
    svc = SubscriptionService(UserRepo(user), PlanRepo(), SubRepo(sub), EventRepo())

    await svc.process_recurring_event(
        event="PAYMENT_REFUNDED", billing_subscription_id="sub_bc_1",
        subscription_expires_at=None, payment_date=None, raw_payload={},
    )

    assert sub.cancel_at_period_end is True
    assert sub.expires_at < datetime.utcnow()
    assert sub.status == "active"  # status nao muda; o bloqueio vem de expires_at + cancel_at_period_end


@pytest.mark.asyncio
async def test_subscription_inactivated_from_gateway_locks_when_no_prior_user_cancel():
    sub = StubSub(status="active", expires_at=datetime(2026, 12, 1), cancel_at_period_end=False)
    user = StubUser(id=sub.owner_id)
    svc = SubscriptionService(UserRepo(user), PlanRepo(), SubRepo(sub), EventRepo())

    await svc.process_recurring_event(
        event="SUBSCRIPTION_INACTIVATED", billing_subscription_id="sub_bc_1",
        subscription_expires_at=None, payment_date=None, raw_payload={},
    )

    assert sub.cancel_at_period_end is True
    assert sub.expires_at < datetime.utcnow()


@pytest.mark.asyncio
async def test_subscription_inactivated_after_user_requested_cancel_keeps_access_window():
    original_expires = datetime(2026, 12, 1)
    sub = StubSub(status="active", expires_at=original_expires, cancel_at_period_end=True, canceled_at=datetime(2026, 9, 15))
    user = StubUser(id=sub.owner_id)
    svc = SubscriptionService(UserRepo(user), PlanRepo(), SubRepo(sub), EventRepo())

    await svc.process_recurring_event(
        event="SUBSCRIPTION_INACTIVATED", billing_subscription_id="sub_bc_1",
        subscription_expires_at=None, payment_date=None, raw_payload={},
    )

    assert sub.expires_at == original_expires  # confirmacao do gateway nao antecipa o corte
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_internal_webhook_recurring.py -v`
Expected: FAIL — `sub.expires_at` fica igual ao payload (5 anos/None), `SUBSCRIPTION_INACTIVATED` ainda seta `status="canceled"` incondicionalmente.

- [ ] **Step 3: Rewrite `process_recurring_event`**

Em `app/application/services/subscription_service.py`, adicionar o import `from domain.billing_periods import PERIOD_DAYS` no topo.

Substituir o bloco a partir de `status_map = {...}` ate o fim do `try/except` que aplica o evento (mantendo tudo antes disso — busca do evento, dedupe por `event_id`, persistencia do `BillingEventModel` — exatamente como esta hoje) por:

```python
        try:
            if local_sub is not None:
                if event == "PAYMENT_RECEIVED" and payment_date is not None:
                    period_days = PERIOD_DAYS.get(local_sub.subscription_type, PERIOD_DAYS["monthly"])
                    local_sub.status = "active"
                    local_sub.expires_at = payment_date + timedelta(days=period_days)
                    local_sub.provisional = False
                    local_sub.last_event_at = datetime.utcnow()
                    await self._sub_repo.save(local_sub)

                    user = await self.user_repo.get_by_id(local_sub.owner_id)
                    if user is not None:
                        if local_sub.plan_id:
                            user.plan_id = local_sub.plan_id
                        user.plan_expiration = local_sub.expires_at
                        user.is_active = True
                        await self.user_repo.save(user)

                elif event in ("PAYMENT_REFUNDED", "PAYMENT_CHARGEBACK_REQUESTED"):
                    self._lock_immediately(local_sub)
                    await self._sub_repo.save(local_sub)

                elif event == "SUBSCRIPTION_INACTIVATED":
                    if local_sub.cancel_at_period_end:
                        # cancelamento ja solicitado pelo usuario (POST /billing/subscription/cancel);
                        # isto e so a confirmacao do gateway — o acesso continua ate expires_at.
                        if local_sub.canceled_at is None:
                            local_sub.canceled_at = datetime.utcnow()
                            await self._sub_repo.save(local_sub)
                    else:
                        # cancelado do lado do gateway (payer cancelou direto no Mercado Pago,
                        # ou 3 faturas recusadas) — nao ha periodo pago a proteger.
                        self._lock_immediately(local_sub)
                        await self._sub_repo.save(local_sub)

            event_model.processing_status = "processed"
            event_model.processed_at = datetime.utcnow()
        except Exception as exc:
            event_model.processing_status = "failed"
            event_model.processing_error = str(exc)[:500]
            logger.error(f"[webhook] Falha recorrente event_id={event_id}: {exc}")
        await self._event_repo.save(event_model)

        return {"result": event_model.processing_status, "event_id": event_id, "event": event}

    @staticmethod
    def _lock_immediately(sub) -> None:
        """Corta o acesso agora, sem carencia — usado por estorno, chargeback e
        cancelamento iniciado pelo gateway (sem periodo pago a proteger)."""
        sub.cancel_at_period_end = True
        if sub.canceled_at is None:
            sub.canceled_at = datetime.utcnow()
        sub.expires_at = datetime.utcnow() - timedelta(seconds=1)
```

Remover a linha antiga `status_map = {...}` e a variavel `new_status` que dependiam dela (nao sao mais usadas). Adicionar `from datetime import timedelta` ao import de `datetime` no topo do arquivo, se ainda nao estiver (o arquivo ja importa `from datetime import datetime, timedelta` — conferir antes de duplicar).

- [ ] **Step 4: Run tests to verify they pass, then run the full suite**

Run: `python -m pytest tests/unit/test_internal_webhook_recurring.py -v`
Expected: PASS.

Run: `python -m pytest tests/unit -q`
Expected: todos os testes passam.

- [ ] **Step 5: Commit**

```bash
git add app/application/services/subscription_service.py tests/unit/test_internal_webhook_recurring.py
git commit -m "fix(billing): compute subscription expires_at locally and lock immediately on refund/chargeback/gateway-cancel"
```

---

### Task 8: `PlanAccessService` — sem carencia para quem pediu para cancelar, escolhe a assinatura certa

**Files:**
- Modify: `app/application/services/plan_access_service.py`
- Test: `tests/unit/test_plan_access_service.py` (criar se nao existir; conferir antes — pode ja existir cobertura equivalente em outro arquivo, ex. `tests/unit/test_phase4_billing.py`)

**Interfaces:**
- Consumes: `SQLAlchemyBillingSubscriptionRepository.get_current_for_owner` (Task 4).
- Produces: `PlanAccessService.get_subscription_status` usa `get_current_for_owner` em vez de `get_active_by_owner`; `_effective_status` bloqueia sem carencia quando `cancel_at_period_end=True` e o prazo passou, e deixa de travar so por `status == "canceled"` (so `FAILED` trava na hora).

- [ ] **Step 1: Check for existing coverage before writing new tests**

Run: `grep -rn "_effective_status\|get_subscription_status" tests/unit/*.py`

Se `tests/unit/test_phase4_billing.py` ou outro arquivo ja testar `_effective_status` diretamente, adicionar os casos novos la, seguindo o padrao (`StubSub`-like) ja usado nesse arquivo, em vez de criar `test_plan_access_service.py` do zero. Os passos abaixo assumem que nenhuma cobertura direta existe; ajustar o arquivo-alvo conforme o resultado do grep.

- [ ] **Step 2: Write the failing tests**

```python
# tests/unit/test_plan_access_service.py
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest

from application.services.plan_access_service import PlanAccessService, SubscriptionStatus


@dataclass
class StubSub:
    status: str
    expires_at: Optional[datetime] = None
    cancel_at_period_end: bool = False
    plan_id: Optional[uuid.UUID] = None
    billing_mode: str = "invoice"


class SubRepo:
    def __init__(self, current):
        self._current = current
    async def get_current_for_owner(self, owner_id):
        return self._current


class PlanRepo:
    async def get_by_id(self, plan_id):
        return None


class UserRepo:
    async def get_by_id(self, owner_id):
        return None


def _service(sub):
    return PlanAccessService(UserRepo(), PlanRepo(), SubRepo(sub))


@pytest.mark.asyncio
async def test_cancel_at_period_end_stays_operational_until_expires_at():
    now = datetime.utcnow()
    sub = StubSub(status="active", expires_at=now + timedelta(days=5), cancel_at_period_end=True)
    result = await _service(sub).get_subscription_status(uuid.uuid4())
    assert result.allowed is True
    assert result.subscription_status == "active"


@pytest.mark.asyncio
async def test_cancel_at_period_end_blocks_immediately_after_expires_at_no_grace():
    now = datetime.utcnow()
    sub = StubSub(status="active", expires_at=now - timedelta(hours=1), cancel_at_period_end=True)
    result = await _service(sub).get_subscription_status(uuid.uuid4())
    assert result.allowed is False
    assert result.subscription_status == SubscriptionStatus.EXPIRED


@pytest.mark.asyncio
async def test_late_payment_without_cancel_still_gets_grace_period():
    now = datetime.utcnow()
    sub = StubSub(status="active", expires_at=now - timedelta(hours=1), cancel_at_period_end=False)
    result = await _service(sub).get_subscription_status(uuid.uuid4())
    assert result.subscription_status == SubscriptionStatus.PAST_DUE
    assert result.allowed is False  # past_due nao esta em OPERATIONAL, mas locked=False (RESTRICTED)


@pytest.mark.asyncio
async def test_failed_status_locks_regardless_of_expires_at():
    sub = StubSub(status="failed", expires_at=datetime.utcnow() + timedelta(days=30))
    result = await _service(sub).get_subscription_status(uuid.uuid4())
    assert result.allowed is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_plan_access_service.py -v`
Expected: FAIL — `get_subscription_status` ainda chama `get_active_by_owner` (nao existe no `SubRepo` do teste, `AttributeError`), e `_effective_status` trava `status="canceled"`/nao conhece `cancel_at_period_end`.

- [ ] **Step 4: Update `get_subscription_status` and `_effective_status`**

Em `app/application/services/plan_access_service.py`, trocar `sub = await self._sub_repo.get_active_by_owner(owner_id)` por `sub = await self._sub_repo.get_current_for_owner(owner_id)` dentro de `get_subscription_status`.

Substituir o metodo `_effective_status` inteiro por:

```python
    def _effective_status(self, sub) -> tuple[str, bool]:
        """Deriva (status_efetivo, locked) de status persistido + expires_at + grace.

        Regras:
          - FAILED persistido => bloqueado na hora, sempre.
          - Dentro de expires_at => opera com o status persistido (inclui o caso
            de cancel_at_period_end=True: o usuario continua com acesso ate o
            fim do periodo, D1).
          - Passou de expires_at com cancel_at_period_end=True => expirado na
            hora, sem carencia (o usuario pediu para parar; nao ha o que tolerar).
          - Passou de expires_at sem cancel_at_period_end (atraso de pagamento
            comum) => carencia de 3 dias como past_due antes de expirar.
        """
        from datetime import timedelta
        status = sub.status
        if status == SubscriptionStatus.FAILED:
            return status, True
        expires_at = getattr(sub, "expires_at", None)
        if expires_at is None:
            return status, status in SubscriptionStatus.BLOCKED
        now = datetime.utcnow()
        if now <= expires_at:
            return status, status in SubscriptionStatus.BLOCKED
        if getattr(sub, "cancel_at_period_end", False):
            return SubscriptionStatus.EXPIRED, True
        grace_end = expires_at + timedelta(days=SubscriptionStatus.GRACE_DAYS)
        if now <= grace_end:
            return SubscriptionStatus.PAST_DUE, False
        return SubscriptionStatus.EXPIRED, True
```

Nota: `status in SubscriptionStatus.BLOCKED` no ramo `now <= expires_at` so importa quando `status` ja e um valor terminal persistido diretamente (ex.: `expired`/`canceled` escritos por outro caminho, como `retry_canceled_invoice`) — isso preserva o comportamento antigo para esses casos sem reintroduzir o bloqueio imediato por `cancel_at_period_end` que a Task 7 agora evita escrever como `status="canceled"`.

Tambem atualizar a interface abstrata `BillingSubscriptionRepositoryInterface` no mesmo arquivo: renomear (ou acrescentar, mantendo o metodo antigo se outro lugar ainda o usa — conferir com `grep -rn "get_active_by_owner" app/` antes de remover) `get_active_by_owner` para `get_current_for_owner` na assinatura declarada.

- [ ] **Step 5: Run tests to verify they pass, then run the full suite**

Run: `python -m pytest tests/unit/test_plan_access_service.py -v`
Expected: PASS.

Run: `grep -rn "get_active_by_owner" app/ tests/`
Expected: so a definicao do metodo no repositorio (Task 4 nao removeu `get_active_by_owner`, apenas acrescentou `get_current_for_owner` — se nenhum outro caller depender dele, remove-lo do repositorio e da interface neste passo; se algo ainda chamar, deixar os dois metodos).

Run: `python -m pytest tests/unit -q`
Expected: todos os testes passam.

- [ ] **Step 6: Commit**

```bash
git add app/application/services/plan_access_service.py tests/unit/test_plan_access_service.py
git commit -m "fix(billing): pick the governing subscription and skip payment grace when the user asked to cancel"
```

---

### Task 9: Job de reconciliacao do acesso provisorio + fatura pula assinatura cancelada

**Files:**
- Modify: `app/application/jobs/billing_jobs.py`
- Modify: `worker.py`
- Test: `tests/unit/test_billing_jobs_reconcile.py` (estender)

**Interfaces:**
- Consumes: `SQLAlchemyBillingSubscriptionRepository.list_pending_recurring_with_gateway_id` (Task 4), `BillingCoreClient.get_subscription_status` (Task 3).
- Produces: `reconcile_provisional_subscriptions(ctx, *, sub_repo=None, bc_client=None) -> dict`.

- [ ] **Step 1: Write the failing test**

Adicionar a `tests/unit/test_billing_jobs_reconcile.py` (reaproveitando o padrao ja usado la para `reconcile_pending_invoices` — fakes injetados via kwargs, sem sessao real):

```python
@pytest.mark.asyncio
async def test_reconcile_provisional_subscriptions_grants_provisional_access_when_gateway_active():
    from application.jobs.billing_jobs import reconcile_provisional_subscriptions

    sub = SimpleNamespace(
        id=uuid.uuid4(), status="pending", provisional=False,
        billing_subscription_id="preapproval_1", subscription_type="monthly",
    )

    sub_repo = AsyncMock()
    sub_repo.list_pending_recurring_with_gateway_id.return_value = [sub]

    bc_client = AsyncMock()
    bc_client.get_subscription_status.return_value = {"gateway_status": "ACTIVE"}

    result = await reconcile_provisional_subscriptions(ctx={}, sub_repo=sub_repo, bc_client=bc_client)

    assert result == {"checked": 1, "granted": 1, "errors": 0}
    assert sub.status == "active"
    assert sub.provisional is True
    sub_repo.save.assert_awaited_once_with(sub)


@pytest.mark.asyncio
async def test_reconcile_provisional_subscriptions_skips_when_gateway_still_pending():
    from application.jobs.billing_jobs import reconcile_provisional_subscriptions

    sub = SimpleNamespace(
        id=uuid.uuid4(), status="pending", provisional=False,
        billing_subscription_id="preapproval_1", subscription_type="monthly",
    )
    sub_repo = AsyncMock()
    sub_repo.list_pending_recurring_with_gateway_id.return_value = [sub]
    bc_client = AsyncMock()
    bc_client.get_subscription_status.return_value = {"gateway_status": "PENDING"}

    result = await reconcile_provisional_subscriptions(ctx={}, sub_repo=sub_repo, bc_client=bc_client)

    assert result == {"checked": 1, "granted": 0, "errors": 0}
    sub_repo.save.assert_not_awaited()
```

Adicionar `from types import SimpleNamespace` e `import uuid` ao topo do arquivo se ainda nao estiverem la (conferir — o arquivo ja testa jobs similares e provavelmente ja importa `uuid`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_billing_jobs_reconcile.py -v -k provisional`
Expected: FAIL — `ImportError: cannot import name 'reconcile_provisional_subscriptions'`.

- [ ] **Step 3: Implement the job**

Em `app/application/jobs/billing_jobs.py`, apos `reconcile_pending_invoices` (e antes de `_run_reconcile_pending_invoices_with_session`, ou no fim do arquivo — manter o padrao de "funcao testavel + wrapper que monta sessao real" ja usado pelas outras duas jobs do arquivo):

```python
async def reconcile_provisional_subscriptions(
    ctx: dict,
    *,
    sub_repo=None,
    bc_client=None,
) -> dict:
    """Concede acesso provisorio de 24h (D2) para quem autorizou o cartao no
    Mercado Pago mas ainda nao voltou pelo navegador — cobre quem fechou a
    aba antes do redirecionamento para /billing/retorno."""
    if sub_repo is None:
        return await _run_reconcile_provisional_subscriptions_with_session(ctx)

    subs = await sub_repo.list_pending_recurring_with_gateway_id()
    checked = granted = errors = 0
    for sub in subs:
        checked += 1
        try:
            remote = await bc_client.get_subscription_status(sub.billing_subscription_id)
            if remote.get("gateway_status") == "ACTIVE":
                sub.status = "active"
                sub.provisional = True
                sub.expires_at = datetime.utcnow() + timedelta(hours=24)
                await sub_repo.save(sub)
                granted += 1
        except Exception as exc:
            errors += 1
            logger.error("reconcile_provisional_error", extra={"extra_data": {"sub_id": str(sub.id), "error": str(exc)}})
    return {"checked": checked, "granted": granted, "errors": errors}


async def _run_reconcile_provisional_subscriptions_with_session(ctx: dict) -> dict:
    from infra.database.setup import async_session_factory
    from infra.clients.billing_core_client import BillingCoreClient
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository

    async with async_session_factory() as session:
        sub_repo = SQLAlchemyBillingSubscriptionRepository(session)
        result = await reconcile_provisional_subscriptions(ctx, sub_repo=sub_repo, bc_client=BillingCoreClient())
        await session.commit()
        return result
```

- [ ] **Step 4: Register the cron job**

Em `worker.py`, adicionar `reconcile_provisional_subscriptions` ao import de `from application.jobs.billing_jobs import (...)` e registrar no `cron_jobs`, seguindo o mesmo espacamento de minutos ja usado por `reconcile_pending_invoices` (offset diferente para nao competir no mesmo minuto):

```python
        cron(reconcile_provisional_subscriptions, minute={4, 14, 24, 34, 44, 54}),
```

- [ ] **Step 5: Run tests to verify they pass, then run the full suite**

Run: `python -m pytest tests/unit/test_billing_jobs_reconcile.py -v`
Expected: PASS.

Run: `python -m pytest tests/unit -q`
Expected: todos os testes passam.

- [ ] **Step 6: Commit**

```bash
git add app/application/jobs/billing_jobs.py worker.py tests/unit/test_billing_jobs_reconcile.py
git commit -m "feat(billing): reconcile provisional 24h access for card subscriptions authorized without a browser return"
```

---

### Task 10: Descricao sem UUID e periodo da fatura a partir do pagamento

**Files:**
- Modify: `app/application/services/invoice_service.py`
- Test: `tests/unit/test_invoice_service.py` (estender)

**Interfaces:**
- Produces: descricao enviada ao billing-core (`_create_checkout`) usa `plan.name`, nunca o UUID da assinatura/fatura; `activate_invoice` recalcula `period_end` a partir da data de pagamento real quando o pagamento chega depois do periodo planejado originalmente.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_checkout_description_uses_plan_name_not_uuid():
    # reaproveitar os fakes ja definidos no arquivo (InvoiceRepo, SubRepo, PlanRepo, bc)
    ...  # seguir o padrao ja existente no arquivo para montar InvoiceService e chamar _create_checkout
    _, kwargs = bc.create_payment.call_args
    assert "Plano Pro" in kwargs["description"]
    import re
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", kwargs["description"])


@pytest.mark.asyncio
async def test_activate_invoice_recomputes_period_end_from_actual_payment_date():
    # fatura criada com due_date=period_start=D0, mas o pagamento so confirma em D0+3
    ...  # ativar via activate_invoice com paid_at simulado 3 dias depois de period_start
    assert sub.expires_at == invoice.period_start + timedelta(days=3) + timedelta(days=PERIOD_DAYS[sub.subscription_type])
```

Escrever os dois testes completos seguindo exatamente os fakes/fixtures ja presentes em `tests/unit/test_invoice_service.py` (o arquivo ja instancia `InvoiceService` com repos fake para os testes existentes de `contract`/`ensure_checkout`/`activate_invoice` — reaproveitar a mesma fabrica de fakes, so variando os asserts acima).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_invoice_service.py -v -k "description_uses_plan_name or recomputes_period_end"`
Expected: FAIL — a descricao atual inclui `invoice.subscription_id`; `activate_invoice` usa `invoice.period_end` fixo desde a criacao, nao a data real de pagamento.

- [ ] **Step 3: Fix the description**

Em `app/application/services/invoice_service.py`, `_create_checkout`, trocar:

```python
            description=f"Assinatura Marketfy {plan.name} — {invoice.subscription_id}",
```

por:

```python
            description=f"Assinatura Marketfy {plan.name}",
```

- [ ] **Step 4: Recompute the period from the real payment date**

Em `activate_invoice`, apos `sub = await self._sub.get_by_id(invoice.subscription_id)` e antes de `if sub is not None:`, adicionar o recalculo do periodo quando o pagamento chegou depois do planejado:

```python
        if sub is not None:
            plan = await self._plan.get_by_id(invoice.plan_id)
            period_days = PERIOD_DAYS.get(sub.subscription_type, PERIOD_DAYS["monthly"]) if plan else 0
            paid_at = datetime.utcnow()
            actual_period_end = max(invoice.period_end, invoice.period_start + timedelta(days=period_days)) if period_days else invoice.period_end
            # se o pagamento veio depois do inicio planejado, a validade conta a
            # partir de agora, nao do period_start original (ninguem perde dias
            # por causa de um checkout que demorou a ser pago).
            if paid_at > invoice.period_start:
                actual_period_end = paid_at + timedelta(days=period_days) if period_days else invoice.period_end
            sub.status = "active"
            sub.expires_at = actual_period_end
            sub.last_event_at = datetime.utcnow()
            await self._sub.save(sub)
```

(substitui as 3 linhas `sub.status = "active"` / `sub.expires_at = invoice.period_end` / `sub.last_event_at = datetime.utcnow()` que ja existem la).

Adicionar `from domain.billing_periods import PERIOD_DAYS` ao topo do arquivo (a Task 1 ja trocou o `PERIOD_DAYS` local por esse import — conferir que a linha ja existe antes de duplicar).

- [ ] **Step 5: Run tests to verify they pass, then run the full suite**

Run: `python -m pytest tests/unit/test_invoice_service.py -v`
Expected: PASS.

Run: `python -m pytest tests/unit -q`
Expected: todos os testes passam.

- [ ] **Step 6: Commit**

```bash
git add app/application/services/invoice_service.py tests/unit/test_invoice_service.py
git commit -m "fix(billing): drop the raw UUID from the checkout description and count the invoice period from the real payment date"
```

---

## Self-Review Notes

- Cobertura da spec: item 1 (Task 5-6), item 2 (Task 5), item 3 (Task 7), item 4 (Task 6, cancelamento), item 5 (Task 6, D3 superado por acesso — ver observacao abaixo), item 6 (Task 4, 8), item 7 (Task 7), item 8 (frontend, plano separado), item 9 (Task 6, 9), item 10 (Task 10).
- **Observacao sobre o item 5 (D3, troca de plano):** este plano cobre a mecanica de selecao (Task 4) e cancelamento (Task 6-7), que ja evita cobranca dobrada — uma segunda assinatura ativa vira a `get_current_for_owner` automaticamente (rank 0, `updated_at` mais recente) sem exigir codigo novo. O passo explicito de "cancelar a assinatura anterior quando a nova confirma o primeiro pagamento" fica como trabalho de acompanhamento: hoje, se o usuario contrata um plano novo tendo um plano recorrente ainda ativo, as duas assinaturas continuam cobrando em paralelo no gateway ate alguem cancelar a antiga manualmente. Sinalizar como tarefa separada antes de habilitar troca de plano com recorrencia ativa na UI (a Task 6 do plano de frontend deve, por enquanto, esconder "trocar de plano" quando `billing_mode == "recurring"` e ja houver assinatura ativa, mostrando "cancele primeiro" — ver plano do frontend).
- Nenhum placeholder: todo passo de codigo tem o codigo completo ou a substituicao exata de linhas existentes.
- Consistencia de tipos: `select_current_subscription`, `get_current_for_owner`, `ensure_checkout`, `cancel_subscription`, `get_subscription_status` usam os mesmos nomes em todas as tasks que os consomem.
