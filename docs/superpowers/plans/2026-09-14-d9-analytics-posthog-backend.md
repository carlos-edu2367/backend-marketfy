# D9 — Analytics de produto com PostHog (Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrumentar 5 eventos de negócio do funil de conversão (trial, assinatura, pagamento, crédito fiscal) com PostHog, sem nunca derrubar a operação de negócio se o PostHog estiver fora do ar ou desabilitado.

**Architecture:** Um cliente HTTP novo (`PostHogClient`) seguindo o mesmo padrão de `BillingCoreClient`: timeout curto, nunca levanta exceção pro chamador. Cada serviço de negócio (`SubscriptionService`, `InvoiceService`, `RecurringService`, `FiscalCreditsService`) ganha um parâmetro `analytics=None` no construtor que, se não for passado, vira um `PostHogClient()` real internamente — **nenhum call site nos routers precisa mudar** (7 pontos diferentes constroem esses serviços hoje; todos continuam funcionando sem alteração porque o default já é um cliente funcional). Cada evento é disparado com `await` logo depois da escrita de sucesso no banco, nunca antes.

**Tech Stack:** Python 3.12, httpx assíncrono, pytest + pytest-asyncio, `respx` pra mockar a Capture API do PostHog sem rede real (mesmo padrão de `tests/unit/test_mercadopago_client_oauth.py`).

**Spec:** `docs/superpowers/specs/2026-09-14-product-analytics-posthog-design.md`. Companion: `frontend/docs/superpowers/plans/2026-09-14-d9-analytics-posthog-frontend.md`.

## Global Constraints

- `track_event()` nunca levanta exceção — falha de rede/PostHog fora do ar não pode derrubar uma operação de negócio real. Timeout de 2s.
- Evento só dispara **depois** da escrita de sucesso no banco (nunca antes de um possível rollback).
- `ANALYTICS_ENABLED=False` por padrão, sem validador obrigando `True` em produção (diferente de `BILLING_CORE_ENABLED`) — analytics é opcional mesmo em produção.
- Testes seguem o padrão existente: `sys.path.append(<repo>/app)` no topo, imports sem prefixo `app.`.
- Commits terminam com `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

## Evidência levantada relendo o código em 2026-09-14

| Achado | Evidência |
|---|---|
| Pontos de instrumentação do spec §5.3 conferidos linha a linha | `subscription_service.py:52` (`activate_trial`, salva usuário na linha ~74), `invoice_service.py:36` (`contract`, retorna sucesso ~linha 78) e `:269` (`activate_invoice`), `recurring_service.py:40` (`contract`, retorna sucesso ~linha 91), `fiscal_credits_service.py:165` (`activate_package`). Nenhuma linha mudou desde o spec. |
| **Achado extra (não estava no spec):** `BillingInvoiceModel` não tem campo `subscription_type` | Confirmado em `infra/database/models.py:528-565` — o modelo tem `owner_id`, `subscription_id`, `plan_id`, `amount`, `status`, mas **não** `subscription_type`. `activate_invoice` já busca a assinatura relacionada (`sub = await self._sub.get_by_id(invoice.subscription_id)`) — o evento `invoice_paid` deve usar `sub.subscription_type`, não `invoice.subscription_type` (que não existe). |
| **Achado extra:** `FiscalEmissionPackageModel` não tem campo `amount`, tem `quantity` e `price_gross` | Confirmado em `infra/database/models.py:971-1000`. O evento `fiscal_credits_purchased` usa `package.quantity` (inteiro) e `package.price_gross` (Decimal, o preço bruto pago) como `amount`. |
| `track_event` do spec (função solta) vs. injeção por construtor (spec §5.4) — desenho reconciliado | O spec mostra `track_event()` como função de módulo solta, mas também diz que os serviços recebem `analytics: Optional[...] = None` injetável em teste. Resolvido aqui como: `PostHogClient.track_event(...)` é o método real (o que os serviços chamam via `self._analytics`); uma função de módulo `track_event()` fica como conveniência que delega pra um singleton `PostHogClient()` — não é usada por nenhum dos 5 pontos de instrumentação desta rodada (todos passam pelo construtor dos serviços), mas fica disponível pra call sites futuros que não tenham DI de serviço. |
| Todos os 4 serviços já recebem outras dependências opcionais por construtor — `analytics=None` segue o mesmo padrão | `SubscriptionService.__init__(..., billing_client=None)`, `InvoiceService.__init__(..., user_repo=None)`, `RecurringService.__init__(subscription_repo, plan_repo, user_repo, billing_client, settings)` (todos posicionais, sem opcional ainda — `analytics=None` deve ir **depois** de `settings` pra não quebrar as chamadas posicionais existentes em `tests/unit/test_recurring_service.py:85`), `FiscalCreditsService.__init__(self, *, ..., user_repo=None)` (kw-only, sem risco de quebra posicional). |
| Nenhum call site nos routers precisa mudar | Constrções ad-hoc de `InvoiceService`/`RecurringService`/`FiscalCreditsService` em `billing.py:85,141`, `billing_core_webhooks.py:149`, `billing_invoice_webhooks.py:71`, `admin_fiscal.py:51`, `fiscal_credits.py:86`, e `SubscriptionService` via `dependencies.py:get_subscription_service` — nenhum passa `billing_client`/outros opcionais hoje sem precisar, e nenhum vai precisar passar `analytics` também, porque o default (`analytics or PostHogClient()`) já é funcional. |
| Testes existentes reaproveitáveis para cada task | `tests/unit/test_invoice_service.py` (`InvoiceService(inv_repo, sub_repo, plan_repo, bc, StubSettings())`, tem `test_activate_invoice_activates_subscription_idempotently` e `test_activate_invoice_syncs_user_plan_cache`), `tests/unit/test_recurring_service.py` (`RecurringService(SubRepo(), PlanRepo(plan), UserRepo(), bc, StubSettings())`), `tests/unit/test_billing_core_webhooks.py` (`_credits_service()` helper, `test_activate_package_increments_addon_credits`). Nenhum teste hoje cobre `SubscriptionService.activate_trial` — este plano cria o primeiro. |
| Padrão respx real (spec citou `@pytest.mark.respx`, que não existe) | Confirmado em `tests/unit/test_mercadopago_client_oauth.py`: `@pytest.mark.asyncio` + `@respx.mock`, `respx.post(url).mock(return_value=Response(...))`, `sm.get_settings.cache_clear()` depois de `monkeypatch.setenv(...)` pra recarregar `Settings()`. |

## Ordem de execução e deploy

1. Task 1 primeiro (cliente + settings — pré-requisito de tudo mais). Tasks 2-5 são independentes entre si depois da Task 1 (tocam arquivos diferentes) — podem ir em qualquer ordem, mas sequenciais neste plano por simplicidade de revisão.
2. Rodar `python -m pytest tests/unit -q` completo ao final de cada task.
3. Nenhuma migration. Variáveis de ambiente `ANALYTICS_ENABLED`, `POSTHOG_API_KEY`, `POSTHOG_HOST` são opcionais — sem configurá-las em produção, o comportamento é idêntico ao de hoje (analytics desligado, nenhuma chamada de rede).
4. Companion: `frontend/docs/superpowers/plans/2026-09-14-d9-analytics-posthog-frontend.md`.

---

### Task 1: `PostHogClient` + configuração (`infra/observability/analytics.py`, `settings.py`)

**Files:**
- Create: `app/infra/observability/analytics.py`
- Modify: `app/infra/config/settings.py`
- Test: `tests/unit/test_analytics_client.py` (novo)

**Interfaces:**
- Produces: `PostHogClient` (classe com `async def track_event(self, distinct_id: str, event: str, properties: Optional[dict] = None) -> None`) e `track_event(distinct_id, event, properties=None)` (função de módulo, conveniência) — consumidos pelas Tasks 2-5.

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/unit/test_analytics_client.py
from __future__ import annotations

import json
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import httpx
import pytest
import respx
from httpx import Response

from infra.observability.analytics import PostHogClient


@pytest.mark.asyncio
@respx.mock
async def test_track_event_posts_to_posthog_capture_api(monkeypatch):
    monkeypatch.setenv("ANALYTICS_ENABLED", "true")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test123")
    from infra.config import settings as sm
    sm.get_settings.cache_clear()

    route = respx.post("https://us.i.posthog.com/capture/").mock(return_value=Response(200, json={"status": 1}))

    client = PostHogClient()
    await client.track_event("user-1", "trial_activated", {"plan_name": "Trial"})

    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["event"] == "trial_activated"
    assert body["distinct_id"] == "user-1"
    assert body["properties"] == {"plan_name": "Trial"}
    assert body["api_key"] == "phc_test123"


@pytest.mark.asyncio
@respx.mock
async def test_track_event_is_a_noop_when_analytics_disabled(monkeypatch):
    monkeypatch.setenv("ANALYTICS_ENABLED", "false")
    from infra.config import settings as sm
    sm.get_settings.cache_clear()

    route = respx.post("https://us.i.posthog.com/capture/").mock(return_value=Response(200))

    client = PostHogClient()
    await client.track_event("user-1", "trial_activated")

    assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_track_event_never_raises_when_posthog_is_down(monkeypatch):
    monkeypatch.setenv("ANALYTICS_ENABLED", "true")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test123")
    from infra.config import settings as sm
    sm.get_settings.cache_clear()

    respx.post("https://us.i.posthog.com/capture/").mock(side_effect=httpx.ConnectTimeout("timeout"))

    client = PostHogClient()
    await client.track_event("user-1", "trial_activated")  # não deve levantar
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/unit/test_analytics_client.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'infra.observability.analytics'`

- [ ] **Step 3: Adicionar settings**

Em `app/infra/config/settings.py`, logo depois do bloco `BILLING_CORE_*` (perto da linha 106, depois de `BILLING_CORE_ENABLED: bool = False`):

```python
    # Analytics de produto (PostHog) — Fase de conversão (D9)
    ANALYTICS_ENABLED: bool = False
    POSTHOG_API_KEY: Optional[str] = None
    POSTHOG_HOST: str = "https://us.i.posthog.com"
```

- [ ] **Step 4: Implementar `PostHogClient`**

Criar `app/infra/observability/analytics.py`:

```python
"""Cliente HTTP para a Capture API do PostHog (eventos de produto/funil, D9).

Nunca levanta exceção para o chamador — falha de rede ou PostHog fora do ar
não pode derrubar uma operação de negócio real. Timeout curto (2s) para
limitar o pior caso de latência.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from infra.config.logger import get_logger
from infra.config.settings import get_settings

logger = get_logger("analytics")


class PostHogClient:
    def __init__(self):
        settings = get_settings()
        self._api_key = settings.POSTHOG_API_KEY or ""
        self._host = settings.POSTHOG_HOST
        self._enabled = settings.ANALYTICS_ENABLED and bool(self._api_key)
        self._timeout = 2.0

    async def track_event(self, distinct_id: str, event: str, properties: Optional[dict] = None) -> None:
        if not self._enabled:
            return
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._host.rstrip('/')}/capture/",
                    json={
                        "api_key": self._api_key,
                        "event": event,
                        "distinct_id": distinct_id,
                        "properties": properties or {},
                    },
                )
                if response.status_code >= 400:
                    logger.warning(
                        "posthog_capture_failed",
                        extra={"extra_data": {"event": event, "status": response.status_code}},
                    )
        except Exception:
            logger.exception("posthog_capture_error", extra={"extra_data": {"event": event}})


_default_client: Optional[PostHogClient] = None


def _get_default_client() -> PostHogClient:
    global _default_client
    if _default_client is None:
        _default_client = PostHogClient()
    return _default_client


async def track_event(distinct_id: str, event: str, properties: Optional[dict] = None) -> None:
    """Conveniência para call sites sem injeção de serviço. Os 5 pontos de
    instrumentação desta rodada usam PostHogClient via construtor — esta
    função fica disponível para uso futuro fora desse padrão."""
    await _get_default_client().track_event(distinct_id, event, properties)
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `python -m pytest tests/unit/test_analytics_client.py -v`
Expected: PASS (3 testes)

- [ ] **Step 6: Rodar a suíte inteira**

Run: `python -m pytest tests/unit -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/infra/observability/analytics.py app/infra/config/settings.py tests/unit/test_analytics_client.py
git commit -m "feat(analytics): add PostHogClient for product analytics events (D9)"
```

---

### Task 2: `SubscriptionService.activate_trial` → `trial_activated`

**Files:**
- Modify: `app/application/services/subscription_service.py`
- Test: `tests/unit/test_subscription_service_analytics.py` (novo — nenhum teste unitário cobre `activate_trial` hoje)

**Interfaces:**
- Consumes: `PostHogClient` (Task 1).

- [ ] **Step 1: Escrever o teste que falha**

Antes de escrever, ler `app/application/services/subscription_service.py` por completo (linhas 1-90) pra confirmar os nomes exatos de `PlanRepositoryInterface`/`UserRepositoryInterface` fakes necessários — o construtor é `SubscriptionService(user_repo, plan_repo, subscription_repo=None, event_repo=None, billing_client=None)`.

```python
# tests/unit/test_subscription_service_analytics.py
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from unittest.mock import AsyncMock

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest

from application.services.subscription_service import SubscriptionService


@dataclass
class StubPlan:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Trial"
    type: object = None  # setado no teste com PlanType.TRIAL real


@dataclass
class StubUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    plan_id: Optional[uuid.UUID] = None
    plan_expiration: Optional[datetime] = None
    is_active: bool = False


class PlanRepo:
    def __init__(self, plans):
        self._plans = plans
    async def list_all(self):
        return self._plans


class UserRepo:
    def __init__(self):
        self.saved = []
    async def save(self, user):
        self.saved.append(user)
        return user


@pytest.mark.asyncio
async def test_activate_trial_tracks_trial_activated_event():
    from application.services.subscription_service import PlanType

    plan = StubPlan(name="Trial 14 dias", type=PlanType.TRIAL)
    user = StubUser()
    analytics = AsyncMock()

    svc = SubscriptionService(UserRepo(), PlanRepo([plan]), analytics=analytics)
    await svc.activate_trial(user)

    analytics.track_event.assert_awaited_once_with(
        str(user.id), "trial_activated", {"plan_name": "Trial 14 dias"}
    )
```

Conferir o import de `PlanType` (já usado dentro de `subscription_service.py` — confirmar se é reexportado do próprio módulo ou precisa vir de `domain.identity`) antes de rodar.

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/unit/test_subscription_service_analytics.py -v`
Expected: FAIL — `SubscriptionService.__init__()` não aceita `analytics=`.

- [ ] **Step 3: Editar `subscription_service.py`**

Import no topo do arquivo:
```python
from infra.observability.analytics import PostHogClient
```

Construtor (perto da linha 34-46) — trocar:
```python
    def __init__(
        self,
        user_repo: UserRepositoryInterface,
        plan_repo: PlanRepositoryInterface,
        subscription_repo=None,   # SQLAlchemyBillingSubscriptionRepository
        event_repo=None,          # SQLAlchemyBillingEventRepository
        billing_client=None,      # BillingCoreClient — injetado para testabilidade
    ):
        self.user_repo = user_repo
        self.plan_repo = plan_repo
        self._sub_repo = subscription_repo
        self._event_repo = event_repo
        self._billing_client = billing_client
```
por:
```python
    def __init__(
        self,
        user_repo: UserRepositoryInterface,
        plan_repo: PlanRepositoryInterface,
        subscription_repo=None,   # SQLAlchemyBillingSubscriptionRepository
        event_repo=None,          # SQLAlchemyBillingEventRepository
        billing_client=None,      # BillingCoreClient — injetado para testabilidade
        analytics=None,           # PostHogClient — injetado para testabilidade
    ):
        self.user_repo = user_repo
        self.plan_repo = plan_repo
        self._sub_repo = subscription_repo
        self._event_repo = event_repo
        self._billing_client = billing_client
        self._analytics = analytics or PostHogClient()
```

Em `activate_trial` (perto da linha 74, logo depois de `updated_user = await self.user_repo.save(user)`), adicionar:
```python
        await self._analytics.track_event(
            str(user.id), "trial_activated", {"plan_name": trial_plan.name}
        )
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/unit/test_subscription_service_analytics.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte inteira**

Run: `python -m pytest tests/unit -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/application/services/subscription_service.py tests/unit/test_subscription_service_analytics.py
git commit -m "feat(analytics): track trial_activated event (D9)"
```

---

### Task 3: `InvoiceService` → `subscription_created` (modo fatura) e `invoice_paid`

**Files:**
- Modify: `app/application/services/invoice_service.py`
- Modify: `tests/unit/test_invoice_service.py` (estender)

**Interfaces:**
- Consumes: `PostHogClient` (Task 1).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `tests/unit/test_invoice_service.py` (reaproveitando `StubPlan`, `StubSub`, `InvoiceRepo`, `SubRepo`, `PlanRepo`, `StubSettings` já definidos no arquivo):

```python
from unittest.mock import AsyncMock as _AsyncMock  # já importado no topo como AsyncMock; reaproveitar


@pytest.mark.asyncio
async def test_contract_tracks_subscription_created_for_invoice_mode():
    owner = uuid.uuid4()
    plan = StubPlan()
    analytics = AsyncMock()
    svc = InvoiceService(InvoiceRepo(), SubRepo(None), PlanRepo(plan), AsyncMock(), StubSettings(), analytics=analytics)

    result = await svc.contract(owner, plan.id, "monthly", idempotency_key="idem-analytics-1")

    analytics.track_event.assert_awaited_once_with(
        str(owner), "subscription_created",
        {"plan_id": str(plan.id), "subscription_type": "monthly", "billing_mode": "invoice"},
    )
    assert result["invoice_id"] is not None


@pytest.mark.asyncio
async def test_activate_invoice_tracks_invoice_paid():
    owner = uuid.uuid4()
    plan = StubPlan()
    now = datetime.utcnow()
    sub = StubSub(owner_id=owner, plan_id=plan.id, status="pending", subscription_type="annual")
    inv_repo = InvoiceRepo()
    inv = await inv_repo.create(owner_id=owner, subscription_id=sub.id, plan_id=plan.id,
                                period_start=now, period_end=now + timedelta(days=365),
                                due_date=now, amount=Decimal("510.00"), idempotency_key="idem-analytics-2")
    analytics = AsyncMock()
    svc = InvoiceService(inv_repo, SubRepo(sub), PlanRepo(plan), AsyncMock(), StubSettings(), analytics=analytics)

    await svc.activate_invoice(inv.id, "pay_1", {})

    analytics.track_event.assert_awaited_once_with(
        str(owner), "invoice_paid",
        {"amount": "510.00", "subscription_type": "annual"},
    )
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/unit/test_invoice_service.py -v`
Expected: FAIL — `InvoiceService.__init__()` não aceita `analytics=`.

- [ ] **Step 3: Editar `invoice_service.py`**

Import no topo:
```python
from infra.observability.analytics import PostHogClient
```

Construtor (linha 27) — trocar:
```python
    def __init__(self, invoice_repo, subscription_repo, plan_repo, billing_client, settings, user_repo=None):
        self._inv = invoice_repo
        self._sub = subscription_repo
        self._plan = plan_repo
        self._bc = billing_client
        self._settings = settings
```
por:
```python
    def __init__(self, invoice_repo, subscription_repo, plan_repo, billing_client, settings, user_repo=None, analytics=None):
        self._inv = invoice_repo
        self._sub = subscription_repo
        self._plan = plan_repo
        self._bc = billing_client
        self._settings = settings
        self._analytics = analytics or PostHogClient()
```

(confirmar se a linha seguinte já atribui `self._user = user_repo` — manter como está, só adicionar a linha de `analytics` depois.)

Em `contract()` (perto da linha 78, logo antes do `return` de sucesso — não dentro do bloco de idempotência que já retorna cedo):
```python
        await self._analytics.track_event(
            str(owner_id), "subscription_created",
            {"plan_id": str(plan_id), "subscription_type": subscription_type, "billing_mode": "invoice"},
        )

        return {
            "subscription_id": str(sub.id),
            "invoice_id": str(invoice.id),
            "job_id": None,
            "checkout_url": None,
        }
```

Em `activate_invoice()` (perto da linha 285-291, depois do bloco `if self._user is not None: ...` e antes do `logger.info("invoice_activated", ...)`):
```python
        if sub is not None:
            await self._analytics.track_event(
                str(invoice.owner_id), "invoice_paid",
                {"amount": str(invoice.amount), "subscription_type": sub.subscription_type},
            )
```

(o evento fica dentro do `if sub is not None` porque `sub.subscription_type` vem da assinatura relacionada, não da fatura — `BillingInvoiceModel` não tem esse campo, ver achado da tabela de evidências acima.)

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/unit/test_invoice_service.py -v`
Expected: PASS — todos os testes, os antigos e os 2 novos.

- [ ] **Step 5: Rodar a suíte inteira**

Run: `python -m pytest tests/unit -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/application/services/invoice_service.py tests/unit/test_invoice_service.py
git commit -m "feat(analytics): track subscription_created (invoice) and invoice_paid events (D9)"
```

---

### Task 4: `RecurringService.contract` → `subscription_created` (modo recorrente)

**Files:**
- Modify: `app/application/services/recurring_service.py`
- Modify: `tests/unit/test_recurring_service.py` (estender)

**Interfaces:**
- Consumes: `PostHogClient` (Task 1).

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao final de `tests/unit/test_recurring_service.py`:

```python
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_contract_tracks_subscription_created_for_recurring_mode():
    plan = StubPlan()
    user = StubUser()
    bc = AsyncMock()
    bc.create_customer.return_value = {"provider_customer_id": "cus_1"}
    bc.create_subscription.return_value = {"job_id": "job_1"}
    bc.get_job.return_value = {"status": "done", "result": {"checkout_url": "https://pay/x", "subscription_id": "sub_bc_1"}}
    analytics = AsyncMock()

    svc = RecurringService(SubRepo(), PlanRepo(plan), UserRepo(), bc, StubSettings(), analytics=analytics)
    await svc.contract(user, plan.id, "monthly", document="12345678901", idempotency_key="idem-analytics-1")

    analytics.track_event.assert_awaited_once_with(
        str(user.id), "subscription_created",
        {"plan_id": str(plan.id), "subscription_type": "monthly", "billing_mode": "recurring"},
    )
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/unit/test_recurring_service.py -v`
Expected: FAIL — `RecurringService.__init__()` não aceita `analytics=`.

- [ ] **Step 3: Editar `recurring_service.py`**

Import no topo:
```python
from infra.observability.analytics import PostHogClient
```

Construtor (linha 33) — trocar:
```python
    def __init__(self, subscription_repo, plan_repo, user_repo, billing_client, settings):
        self._sub = subscription_repo
        self._plan = plan_repo
        self._user = user_repo
        self._bc = billing_client
        self._settings = settings
```
por:
```python
    def __init__(self, subscription_repo, plan_repo, user_repo, billing_client, settings, analytics=None):
        self._sub = subscription_repo
        self._plan = plan_repo
        self._user = user_repo
        self._bc = billing_client
        self._settings = settings
        self._analytics = analytics or PostHogClient()
```

Em `contract()` (perto da linha 90, logo antes do `return {"subscription_id": ...}` final):
```python
        await self._analytics.track_event(
            str(user.id), "subscription_created",
            {"plan_id": str(plan_id), "subscription_type": subscription_type, "billing_mode": "recurring"},
        )

        return {"subscription_id": str(sub.id), "job_id": job_id, "checkout_url": checkout_url}
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/unit/test_recurring_service.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte inteira**

Run: `python -m pytest tests/unit -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/application/services/recurring_service.py tests/unit/test_recurring_service.py
git commit -m "feat(analytics): track subscription_created event for recurring billing (D9)"
```

---

### Task 5: `FiscalCreditsService.activate_package` → `fiscal_credits_purchased`

**Files:**
- Modify: `app/application/services/fiscal/fiscal_credits_service.py`
- Modify: `tests/unit/test_billing_core_webhooks.py` (estender `_credits_service()` e adicionar teste)

**Interfaces:**
- Consumes: `PostHogClient` (Task 1).

- [ ] **Step 1: Escrever o teste que falha**

Editar `_credits_service()` em `tests/unit/test_billing_core_webhooks.py` (perto da linha 60) pra aceitar e propagar `analytics`:

```python
def _credits_service(package=None, existing_ledger=None, analytics=None):
    repo = AsyncMock()
    repo.get_package.return_value = package
    repo.activate_package.return_value = 1
    repo.mark_package_failed.return_value = None
    quota_repo = AsyncMock()
    quota_repo.get_ledger_entry_by_idempotency.return_value = existing_ledger
    quota_service = AsyncMock()
    notification_service = AsyncMock()
    audit_service = AsyncMock()
    return FiscalCreditsService(
        credits_repo=repo,
        quota_repo=quota_repo,
        mp_client=AsyncMock(),
        bc_client=AsyncMock(),
        user_repo=AsyncMock(),
        quota_service=quota_service,
        notification_service=notification_service,
        audit_service=audit_service,
        settings=SimpleNamespace(BILLING_CORE_ENABLED=True),
        analytics=analytics or AsyncMock(),
    )
```

Adicionar o teste novo, próximo dos outros `test_activate_package_*` (perto da linha 340):

```python
@pytest.mark.asyncio
async def test_activate_package_tracks_fiscal_credits_purchased():
    owner_id = uuid.uuid4()
    pkg = FakePackage(owner_id=owner_id, payment_status="pending", quantity=250, price_gross=Decimal("73.57"))
    analytics = AsyncMock()
    svc = _credits_service(package=pkg, analytics=analytics)

    await svc.activate_package(pkg.id, "pay-1", {"payment_id": "pay-1"})

    analytics.track_event.assert_awaited_once_with(
        str(owner_id), "fiscal_credits_purchased",
        {"quantity": 250, "amount": "73.57"},
    )
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/unit/test_billing_core_webhooks.py -v`
Expected: FAIL — `FiscalCreditsService.__init__()` não aceita `analytics=` e não dispara nenhum evento.

- [ ] **Step 3: Editar `fiscal_credits_service.py`**

Import no topo:
```python
from infra.observability.analytics import PostHogClient
```

Construtor (perto da linha 27-40) — trocar:
```python
    def __init__(
        self,
        *,
        credits_repo,
        mp_client=None,
        quota_service,
        settings,
        quota_repo=None,
        notification_service=None,
        audit_service=None,
        plan_access_service=None,
        bc_client=None,
        user_repo=None,
    ):
        self.credits_repo = credits_repo
        self.mp_client = mp_client
        self.quota_service = quota_service
        self.quota_repo = quota_repo or getattr(quota_service, "repo", None)
        self.notification_service = notification_service
        self.audit_service = audit_service
        self.settings = settings
        self.plan_access_service = plan_access_service
        self.bc_client = bc_client
```
por:
```python
    def __init__(
        self,
        *,
        credits_repo,
        mp_client=None,
        quota_service,
        settings,
        quota_repo=None,
        notification_service=None,
        audit_service=None,
        plan_access_service=None,
        bc_client=None,
        user_repo=None,
        analytics=None,
    ):
        self.credits_repo = credits_repo
        self.mp_client = mp_client
        self.quota_service = quota_service
        self.quota_repo = quota_repo or getattr(quota_service, "repo", None)
        self.notification_service = notification_service
        self.audit_service = audit_service
        self.settings = settings
        self.plan_access_service = plan_access_service
        self.bc_client = bc_client
        self.analytics = analytics or PostHogClient()
```

(manter a linha existente `self.user_repo = user_repo`, que vem logo depois no arquivo real — conferir a ordem exata antes de aplicar o diff.)

Em `activate_package()` (perto da linha 226-227, logo depois de `await self._record_activation_audit(package, bc_payment_id, commit=False)` e antes de `await self.credits_repo.session.commit()`):
```python
        await self.analytics.track_event(
            str(package.owner_id), "fiscal_credits_purchased",
            {"quantity": package.quantity, "amount": str(package.price_gross)},
        )
```

Nota: o evento fica **antes** do `commit()` explícito nesta função porque o padrão de `activate_package` já é fazer todas as operações dentro de uma transação e só commitar no final — diferente da regra geral "depois da escrita de sucesso", aqui "sucesso" já está garantido pelo momento em que chegamos nesta linha (nenhum `raise`/`return` cedo depois dela até o commit). Se o `commit()` falhar depois do evento já ter sido disparado, o evento fica levemente adiantado em relação ao banco — risco aceito, consistente com o resto da função (a notificação em `_notify_activation`, chamada logo depois do commit, já assume que o commit vai funcionar).

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/unit/test_billing_core_webhooks.py -v`
Expected: PASS — todos os testes, os antigos e o novo.

- [ ] **Step 5: Rodar a suíte inteira**

Run: `python -m pytest tests/unit -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/application/services/fiscal/fiscal_credits_service.py tests/unit/test_billing_core_webhooks.py
git commit -m "feat(analytics): track fiscal_credits_purchased event (D9)"
```

---

## Self-Review

**Cobertura do spec:** §5.1 (cliente) → Task 1. §5.2 (config) → Task 1. §5.3 (tabela de instrumentação, 5 eventos) → `trial_activated`→Task 2, `subscription_created`×2→Tasks 3 e 4, `invoice_paid`→Task 3, `fiscal_credits_purchased`→Task 5. §5.4 (testes, injeção por construtor, respx) → cada task segue esse padrão.

**Placeholder scan:** nenhum "TODO"/"implementar depois". As duas notas de "conferir antes de aplicar" (ordem exata de atribuições no construtor de `InvoiceService`/`FiscalCreditsService`) são verificações contra o código real, não lacunas.

**Consistência de tipos:** todo serviço usa a mesma assinatura `analytics=None` no construtor e a mesma chamada `await self._analytics.track_event(distinct_id: str, event: str, properties: dict)` (ou `self.analytics` em `FiscalCreditsService`, que já usa `self.` sem underscore para os outros atributos — mantido consistente com o estilo do arquivo, que é `self.credits_repo`/`self.mp_client` sem underscore, diferente de `InvoiceService`/`RecurringService`/`SubscriptionService` que usam `self._sub`/`self._plan` com underscore — cada task segue a convenção já existente no próprio arquivo, não uma convenção nova inventada por este plano).
