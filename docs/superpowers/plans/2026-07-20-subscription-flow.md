# Fluxo Completo de Assinatura — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar contratação self-service de plano no Marketfy em dois modos — Cobrança Recorrente (subscription do billing core) e Por Pagamento (faturas via checkout /payments) — com liberação/bloqueio automáticos por webhook, geração de faturas por worker, aviso banner + e-mail, e aba de Faturas acessível mesmo com plano vencido.

**Architecture:** O backend (FastAPI + SQLAlchemy async + ARQ) orquestra o billing core; o frontend nunca fala com o billing. Faturas reaproveitam o fluxo de checkout `/payments` já usado por créditos fiscais; recorrente usa `POST /v1/subscriptions`. O acesso é decidido no `PlanAccessService` a partir de `status + expires_at + grace(3d) + billing_mode`. Webhooks assinados (HMAC) são a única fonte de liberação.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, ARQ (Redis), httpx, Pydantic 2, pytest (unit com stubs in-memory). Frontend: React + Vite + react-router + react-hook-form + axios + lucide-react + react-hot-toast.

## Global Constraints

- **Não modificar o repo `billing`** — apenas consumir via `BillingCoreClient`.
- Billing core só é chamado no backend; API key/URL/webhook_link nunca vão ao frontend.
- Toda liberação de acesso vem de webhook assinado (HMAC-SHA256/base64 em `X-Webhook-Signature-256`), nunca de retorno de navegador.
- Idempotência obrigatória: criação por `idempotency_key`; ativação por `bc_payment_id`; evento recorrente por `event_id` sintetizado.
- `billing_mode ∈ {recurring, invoice}`; `subscription_type ∈ {trial, monthly, semiannual, annual}`; status de assinatura `∈ {pending, trialing, active, past_due, canceled, expired, failed}`; status de fatura `∈ {pending, paid, overdue, canceled}`.
- Grace após vencimento (modo invoice): **3 dias** (`past_due` ainda operacional) antes de `expired`.
- Fatura gerada **~5 dias** antes do vencimento; 1ª fatura vence na hora (paga para ativar).
- Bloqueio quando `expired`: trava tudo, **exceto Faturas, Configurações e Suporte**.
- Aba de Faturas só para `billing_mode == invoice`.
- E-mail via Mailgun (padrão Neectify Food), mesmas credenciais; falha de e-mail nunca bloqueia geração de fatura.
- Documento (CPF/CNPJ) é sensível: nunca logar em claro.
- Testes seguem o padrão do repo: `tests/unit/`, stubs in-memory (dataclass), `sys.path.append(app_dir)`, `pytest`.
- Rodar testes a partir de `marketfy/backend`: `python -m pytest tests/unit/<arquivo> -v`.
- Commits frequentes; mensagens em português; trabalhar nos worktrees `feature/subscription-flow` (backend e frontend).

---

## FASE 1 — Modelo de dados + controle de acesso

### Task 1: Migração — resolver heads + `billing_mode` + `billing_invoices`

**Files:**
- Create: `alembic/versions/a1b2c3d4e5f6_subscription_billing_mode_and_invoices.py`
- Modify: `app/infra/database/models.py` (adicionar `billing_mode` em `BillingSubscriptionModel`; criar `BillingInvoiceModel`)

**Interfaces:**
- Produces: coluna `billing_subscriptions.billing_mode` (String, not null, default `'recurring'`); tabela `billing_invoices` com `BillingInvoiceModel` (campos em Step 3).

- [ ] **Step 1: Resolver múltiplos heads do Alembic**

O repo tem 3 heads. Verifique e faça merge antes de criar a nova migração:

Run:
```bash
cd marketfy/backend
python -m alembic heads
```
Se listar mais de um head, crie um merge:
```bash
python -m alembic merge -m "merge heads antes de subscription billing" heads
```
Anote o revision resultante (será o `down_revision` da próxima migração). Se `alembic heads` já mostrar um único head, use esse valor.

- [ ] **Step 2: Adicionar `billing_mode` ao model e criar `BillingInvoiceModel`**

Em `app/infra/database/models.py`, dentro de `BillingSubscriptionModel`, logo após a linha `subscription_type = Column(...)` adicione:

```python
    billing_mode = Column(String, default="recurring", nullable=False)  # recurring | invoice
```

E ao final do bloco de billing (após `BillingEventModel`), adicione o novo model:

```python
class BillingInvoiceModel(Base):
    """Fatura de assinatura no modo 'por pagamento' (invoice).

    Cada fatura cobre um período de acesso. Paga via checkout /payments do
    billing core. Ativação idempotente por bc_payment_id.
    """

    __tablename__ = "billing_invoices"
    __table_args__ = (
        Index("ix_billing_invoice_owner", "owner_id"),
        Index("ix_billing_invoice_sub", "subscription_id"),
        Index("ix_billing_invoice_sub_status", "subscription_id", "status"),
        Index("ix_billing_invoice_status_due", "status", "due_date"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("billing_subscriptions.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=True)

    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    due_date = Column(DateTime, nullable=False)
    amount = Column(Numeric(10, 2), default=0, nullable=False)

    # pending | paid | overdue | canceled
    status = Column(String, default="pending", nullable=False)

    bc_job_id = Column(String, nullable=True)
    bc_payment_id = Column(String, nullable=True)
    checkout_url = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=True, unique=True)

    paid_at = Column(DateTime, nullable=True)
    notified_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
```

- [ ] **Step 3: Escrever a migração**

Crie `alembic/versions/a1b2c3d4e5f6_subscription_billing_mode_and_invoices.py`. Substitua `<HEAD>` pelo head único do Step 1:

```python
"""subscription billing_mode and billing_invoices

Revision ID: a1b2c3d4e5f6
Revises: <HEAD>
Create Date: 2026-07-20 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "<HEAD>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "billing_subscriptions",
        sa.Column("billing_mode", sa.String(), nullable=False, server_default="recurring"),
    )
    op.create_table(
        "billing_invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("billing_subscriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("plans.id"), nullable=True),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("due_date", sa.DateTime(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("bc_job_id", sa.String(), nullable=True),
        sa.Column("bc_payment_id", sa.String(), nullable=True),
        sa.Column("checkout_url", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("notified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_unique_constraint("uq_billing_invoice_idem", "billing_invoices", ["idempotency_key"])
    op.create_index("ix_billing_invoice_owner", "billing_invoices", ["owner_id"])
    op.create_index("ix_billing_invoice_sub", "billing_invoices", ["subscription_id"])
    op.create_index("ix_billing_invoice_sub_status", "billing_invoices", ["subscription_id", "status"])
    op.create_index("ix_billing_invoice_status_due", "billing_invoices", ["status", "due_date"])


def downgrade() -> None:
    op.drop_index("ix_billing_invoice_status_due", table_name="billing_invoices")
    op.drop_index("ix_billing_invoice_sub_status", table_name="billing_invoices")
    op.drop_index("ix_billing_invoice_sub", table_name="billing_invoices")
    op.drop_index("ix_billing_invoice_owner", table_name="billing_invoices")
    op.drop_constraint("uq_billing_invoice_idem", "billing_invoices", type_="unique")
    op.drop_table("billing_invoices")
    op.drop_column("billing_subscriptions", "billing_mode")
```

- [ ] **Step 4: Aplicar e verificar a migração**

Run:
```bash
cd marketfy/backend
python -m alembic upgrade head
python -m alembic current
```
Expected: `alembic current` mostra `a1b2c3d4e5f6 (head)` sem erros. Se o banco local não estiver disponível, valide ao menos que `python -m alembic upgrade head --sql` gera o SQL sem exceção.

- [ ] **Step 5: Commit**

```bash
git add app/infra/database/models.py alembic/versions/a1b2c3d4e5f6_subscription_billing_mode_and_invoices.py
git commit -m "feat(billing): billing_mode e tabela billing_invoices"
```

---

### Task 2: `PlanAccessService` — status efetivo com grace e billing_mode

**Files:**
- Modify: `app/application/services/plan_access_service.py`
- Test: `tests/unit/test_plan_access_grace.py`

**Interfaces:**
- Consumes: `SQLAlchemyBillingSubscriptionRepository.get_active_by_owner` (retorna model com `.status`, `.expires_at`, `.billing_mode`, `.plan_id`).
- Produces: `PlanAccessService.get_subscription_status(owner_id)` retornando `PlanAccessResult` cujo `subscription_status` já reflete grace/expiração; `PlanAccessResult` ganha campos `billing_mode: Optional[str]` e `locked: bool`.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/unit/test_plan_access_grace.py`:

```python
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.plan_access_service import PlanAccessService, SubscriptionStatus


@dataclass
class StubSub:
    owner_id: uuid.UUID
    plan_id: Optional[uuid.UUID] = None
    status: str = "active"
    billing_mode: str = "invoice"
    expires_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class StubPlan:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "PRO"
    type: str = "pago"
    is_active: bool = True
    max_markets: int = 5
    max_terminals: int = 10


class SubRepo:
    def __init__(self, sub):
        self._sub = sub
    async def get_active_by_owner(self, owner_id):
        return self._sub


class PlanRepo:
    def __init__(self, plan):
        self._plan = plan
    async def get_by_id(self, pid):
        return self._plan


class UserRepo:
    async def get_by_id(self, uid):
        return None


def _svc(sub, plan):
    return PlanAccessService(UserRepo(), PlanRepo(plan), SubRepo(sub))


@pytest.mark.asyncio
async def test_within_grace_is_past_due_and_not_locked():
    owner = uuid.uuid4()
    plan = StubPlan()
    sub = StubSub(owner_id=owner, plan_id=plan.id, status="active",
                  billing_mode="invoice", expires_at=datetime.utcnow() - timedelta(days=1))
    res = await _svc(sub, plan).get_subscription_status(owner)
    assert res.subscription_status == SubscriptionStatus.PAST_DUE
    assert res.locked is False


@pytest.mark.asyncio
async def test_after_grace_is_expired_and_locked():
    owner = uuid.uuid4()
    plan = StubPlan()
    sub = StubSub(owner_id=owner, plan_id=plan.id, status="active",
                  billing_mode="invoice", expires_at=datetime.utcnow() - timedelta(days=4))
    res = await _svc(sub, plan).get_subscription_status(owner)
    assert res.subscription_status == SubscriptionStatus.EXPIRED
    assert res.locked is True


@pytest.mark.asyncio
async def test_active_before_expiry_not_locked():
    owner = uuid.uuid4()
    plan = StubPlan()
    sub = StubSub(owner_id=owner, plan_id=plan.id, status="active",
                  billing_mode="invoice", expires_at=datetime.utcnow() + timedelta(days=10))
    res = await _svc(sub, plan).get_subscription_status(owner)
    assert res.subscription_status == SubscriptionStatus.ACTIVE
    assert res.locked is False
    assert res.billing_mode == "invoice"
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `python -m pytest tests/unit/test_plan_access_grace.py -v`
Expected: FAIL (`PlanAccessResult` sem `locked`/`billing_mode`, ou status não recalculado).

- [ ] **Step 3: Implementar**

Em `plan_access_service.py`:

Adicione a constante de grace no topo da classe `SubscriptionStatus` (após `FAILED`):
```python
    GRACE_DAYS = 3
```

Adicione campos ao `PlanAccessResult` (dataclass), após `expires_at`:
```python
    billing_mode: Optional[str] = None
    locked: bool = False
```

Substitua o corpo de `get_subscription_status` por:
```python
    async def get_subscription_status(self, owner_id: uuid.UUID) -> PlanAccessResult:
        """Retorna o status consolidado, recalculando grace/expiração por data."""
        sub = await self._sub_repo.get_active_by_owner(owner_id)

        if sub is not None:
            plan = await self._plan_repo.get_by_id(sub.plan_id) if sub.plan_id else None
            effective, locked = self._effective_status(sub)
            return PlanAccessResult(
                allowed=effective in SubscriptionStatus.OPERATIONAL,
                reason="Assinatura ativa." if effective in SubscriptionStatus.OPERATIONAL else f"Status: {effective}",
                subscription_status=effective,
                plan_name=plan.name if plan else None,
                expires_at=sub.expires_at,
                billing_mode=getattr(sub, "billing_mode", None),
                locked=locked,
            )

        return await self._fallback_from_user(owner_id)

    def _effective_status(self, sub) -> tuple[str, bool]:
        """Deriva (status_efetivo, locked) de status persistido + expires_at + grace.

        Regras:
          - Estados terminais persistidos (canceled/failed) => bloqueado.
          - Com expires_at no futuro => mantém status persistido (active/trialing/pending).
          - Passou de expires_at mas dentro do grace (3d) => past_due, não bloqueado.
          - Passou do grace => expired, bloqueado.
        """
        from datetime import timedelta
        status = sub.status
        if status in (SubscriptionStatus.CANCELED, SubscriptionStatus.FAILED):
            return status, True
        expires_at = getattr(sub, "expires_at", None)
        if expires_at is None:
            # sem data: respeita status persistido; bloqueia se já expired
            return status, status in SubscriptionStatus.BLOCKED
        now = datetime.utcnow()
        if now <= expires_at:
            return status, status in SubscriptionStatus.BLOCKED
        grace_end = expires_at + timedelta(days=SubscriptionStatus.GRACE_DAYS)
        if now <= grace_end:
            return SubscriptionStatus.PAST_DUE, False
        return SubscriptionStatus.EXPIRED, True
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `python -m pytest tests/unit/test_plan_access_grace.py -v`
Expected: PASS (3 testes).

- [ ] **Step 5: Rodar a suíte de billing existente para não regredir**

Run: `python -m pytest tests/unit/test_phase4_billing.py -v`
Expected: PASS (sem regressões).

- [ ] **Step 6: Commit**

```bash
git add app/application/services/plan_access_service.py tests/unit/test_plan_access_grace.py
git commit -m "feat(billing): status efetivo com grace de 3 dias e flag locked"
```

---

### Task 3: `GET /billing/subscription` estendido (billing_mode, locked, fatura pendente)

**Files:**
- Modify: `app/application/dtos.py` (estender `BillingSubscriptionResponseDTO`)
- Modify: `app/infra/web/routers/billing.py` (`get_subscription_status`)
- Test: `tests/unit/test_billing_subscription_response.py`

**Interfaces:**
- Consumes: `PlanAccessService.get_subscription_status` (agora com `billing_mode`, `locked`), `PlanAccessService.get_plan_features`.
- Produces: DTO `BillingSubscriptionResponseDTO` com `billing_mode: Optional[str]`, `locked: bool`, `invoice_pending: bool`, `pending_invoice: Optional[dict]`.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/unit/test_billing_subscription_response.py`:

```python
from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.dtos import BillingSubscriptionResponseDTO


def test_response_dto_has_new_fields():
    dto = BillingSubscriptionResponseDTO(
        status="active",
        billing_mode="invoice",
        locked=False,
        invoice_pending=True,
        pending_invoice={"invoice_id": "x", "amount": "50.00"},
    )
    assert dto.billing_mode == "invoice"
    assert dto.locked is False
    assert dto.invoice_pending is True
    assert dto.pending_invoice["amount"] == "50.00"
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `python -m pytest tests/unit/test_billing_subscription_response.py -v`
Expected: FAIL (campos inexistentes no DTO).

- [ ] **Step 3: Estender o DTO**

Em `app/application/dtos.py`, dentro de `BillingSubscriptionResponseDTO`, após `limits`:
```python
    billing_mode: Optional[str] = None
    locked: bool = False
    invoice_pending: bool = False
    pending_invoice: Optional[Dict[str, Any]] = None
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `python -m pytest tests/unit/test_billing_subscription_response.py -v`
Expected: PASS.

- [ ] **Step 5: Preencher os campos no router**

Em `app/infra/web/routers/billing.py`, no factory `_get_subscription_service`/`_get_plan_access_service` já existem. Modifique `get_subscription_status` para injetar também o repositório de faturas e popular os novos campos. Substitua o corpo por:

```python
@router.get("/subscription", response_model=BillingSubscriptionResponseDTO)
async def get_subscription_status(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: PlanAccessService = Depends(_get_plan_access_service),
):
    from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository
    try:
        features_dict = await service.get_plan_features(current_user.id)
        sub_result = await service.get_subscription_status(current_user.id)
        is_pending = sub_result.subscription_status == SubscriptionStatus.PENDING

        pending_invoice = None
        invoice_pending = False
        if sub_result.billing_mode == "invoice":
            inv_repo = SQLAlchemyBillingInvoiceRepository(db)
            inv = await inv_repo.get_latest_pending_by_owner(current_user.id)
            if inv is not None:
                invoice_pending = True
                pending_invoice = {
                    "invoice_id": str(inv.id),
                    "amount": str(inv.amount),
                    "due_date": inv.due_date.isoformat() if inv.due_date else None,
                    "checkout_url": inv.checkout_url,
                    "status": inv.status,
                }

        return BillingSubscriptionResponseDTO(
            status=sub_result.subscription_status,
            plan_name=sub_result.plan_name,
            expires_at=sub_result.expires_at,
            billing_pending=is_pending,
            billing_mode=sub_result.billing_mode,
            locked=sub_result.locked,
            invoice_pending=invoice_pending,
            pending_invoice=pending_invoice,
            features=features_dict.get("features"),
            limits=features_dict.get("limits"),
        )
    except Exception as exc:
        logger.error(f"[billing] Erro ao buscar assinatura user={current_user.id}: {exc}")
        raise HTTPException(status_code=500, detail="Erro ao consultar assinatura.")
```

> `SQLAlchemyBillingInvoiceRepository.get_latest_pending_by_owner` é criado na Task 4. O import é feito dentro da função para evitar erro de coleta antes da Task 4. O teste desta task (Step 1) valida apenas o DTO, sem tocar o router.

- [ ] **Step 6: Commit**

```bash
git add app/application/dtos.py app/infra/web/routers/billing.py tests/unit/test_billing_subscription_response.py
git commit -m "feat(billing): expor billing_mode, locked e fatura pendente no status"
```

---

## FASE 2 — Faturas (núcleo do modo "por pagamento")

### Task 4: Repositório de faturas `SQLAlchemyBillingInvoiceRepository`

**Files:**
- Create: `app/infra/repositories/billing_invoice_repo.py`
- Test: `tests/unit/test_billing_invoice_repo.py` (usa SQLite async em memória)

**Interfaces:**
- Consumes: `BillingInvoiceModel` (Task 1), `BillingSubscriptionModel`.
- Produces: classe `SQLAlchemyBillingInvoiceRepository(db)` com métodos:
  `create(**fields) -> BillingInvoiceModel`,
  `get_by_id(id)`, `get_by_idempotency_key(key)`, `get_by_payment_id(pid)`,
  `get_by_job_id(job_id)`, `get_latest_pending_by_owner(owner_id)`,
  `get_open_invoice_for_subscription(sub_id)` (status `pending`),
  `list_by_owner(owner_id, limit, offset)`,
  `update_checkout(id, bc_job_id, checkout_url, bc_payment_id)`,
  `mark_paid(id, bc_payment_id, paid_at) -> int` (idempotente por status=pending),
  `mark_status(id, status)`, `mark_notified(id, when)`,
  `get_pending_with_payment_id_older_than(cutoff, limit)`.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/unit/test_billing_invoice_repo.py`:

```python
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.database.setup import Base
import infra.database.models  # noqa: F401  (registra os models)
from infra.database.models import UserModel, PlanModel, BillingSubscriptionModel
from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _seed(session):
    owner_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    session.add(PlanModel(id=plan_id, name="PRO", type="pago", max_markets=5,
                          max_terminals=10, price_monthly=Decimal("50")))
    session.add(UserModel(id=owner_id, name="x", email=f"{owner_id}@t.com",
                          password_hash="h", role="owner"))
    sub = BillingSubscriptionModel(owner_id=owner_id, plan_id=plan_id,
                                   billing_mode="invoice", status="pending",
                                   billing_system_sub_id=str(owner_id))
    session.add(sub)
    await session.flush()
    return owner_id, plan_id, sub.id


@pytest.mark.asyncio
async def test_create_and_get_latest_pending(session):
    owner_id, plan_id, sub_id = await _seed(session)
    repo = SQLAlchemyBillingInvoiceRepository(session)
    now = datetime.utcnow()
    inv = await repo.create(
        owner_id=owner_id, subscription_id=sub_id, plan_id=plan_id,
        period_start=now, period_end=now + timedelta(days=30), due_date=now,
        amount=Decimal("50.00"), idempotency_key=f"inv-{sub_id}-1",
    )
    assert inv.status == "pending"
    latest = await repo.get_latest_pending_by_owner(owner_id)
    assert latest is not None and latest.id == inv.id


@pytest.mark.asyncio
async def test_mark_paid_is_idempotent(session):
    owner_id, plan_id, sub_id = await _seed(session)
    repo = SQLAlchemyBillingInvoiceRepository(session)
    now = datetime.utcnow()
    inv = await repo.create(
        owner_id=owner_id, subscription_id=sub_id, plan_id=plan_id,
        period_start=now, period_end=now + timedelta(days=30), due_date=now,
        amount=Decimal("50.00"), idempotency_key=f"inv-{sub_id}-1",
    )
    first = await repo.mark_paid(inv.id, bc_payment_id="pay_1", paid_at=now)
    second = await repo.mark_paid(inv.id, bc_payment_id="pay_1", paid_at=now)
    assert first == 1
    assert second == 0
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `python -m pytest tests/unit/test_billing_invoice_repo.py -v`
Expected: FAIL (módulo `billing_invoice_repo` inexistente).

> Se `aiosqlite`/`pytest_asyncio` não estiverem instalados, rode `pip install aiosqlite pytest-asyncio` (já presentes no ambiente de teste do projeto — confirme com `pip show pytest-asyncio`).

- [ ] **Step 3: Implementar o repositório**

Crie `app/infra/repositories/billing_invoice_repo.py`:

```python
"""Repositório SQLAlchemy para BillingInvoiceModel (faturas de assinatura)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.models import BillingInvoiceModel


class SQLAlchemyBillingInvoiceRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(self, **fields) -> BillingInvoiceModel:
        m = BillingInvoiceModel(id=uuid.uuid4(), **fields)
        self._db.add(m)
        try:
            await self._db.flush()
        except IntegrityError:
            await self._db.rollback()
            existing = await self.get_by_idempotency_key(fields.get("idempotency_key"))
            if existing:
                return existing
            raise
        return m

    async def get_by_id(self, invoice_id: uuid.UUID) -> Optional[BillingInvoiceModel]:
        r = await self._db.execute(select(BillingInvoiceModel).where(BillingInvoiceModel.id == invoice_id))
        return r.scalar_one_or_none()

    async def get_by_idempotency_key(self, key: Optional[str]) -> Optional[BillingInvoiceModel]:
        if not key:
            return None
        r = await self._db.execute(select(BillingInvoiceModel).where(BillingInvoiceModel.idempotency_key == key))
        return r.scalar_one_or_none()

    async def get_by_payment_id(self, payment_id: str) -> Optional[BillingInvoiceModel]:
        r = await self._db.execute(select(BillingInvoiceModel).where(BillingInvoiceModel.bc_payment_id == payment_id))
        return r.scalar_one_or_none()

    async def get_by_job_id(self, job_id: str) -> Optional[BillingInvoiceModel]:
        r = await self._db.execute(select(BillingInvoiceModel).where(BillingInvoiceModel.bc_job_id == job_id))
        return r.scalar_one_or_none()

    async def get_latest_pending_by_owner(self, owner_id: uuid.UUID) -> Optional[BillingInvoiceModel]:
        r = await self._db.execute(
            select(BillingInvoiceModel)
            .where(BillingInvoiceModel.owner_id == owner_id, BillingInvoiceModel.status == "pending")
            .order_by(BillingInvoiceModel.created_at.desc())
            .limit(1)
        )
        return r.scalar_one_or_none()

    async def get_open_invoice_for_subscription(self, subscription_id: uuid.UUID) -> Optional[BillingInvoiceModel]:
        r = await self._db.execute(
            select(BillingInvoiceModel)
            .where(BillingInvoiceModel.subscription_id == subscription_id, BillingInvoiceModel.status == "pending")
            .order_by(BillingInvoiceModel.created_at.desc())
            .limit(1)
        )
        return r.scalar_one_or_none()

    async def list_by_owner(self, owner_id: uuid.UUID, limit: int = 20, offset: int = 0) -> List[BillingInvoiceModel]:
        r = await self._db.execute(
            select(BillingInvoiceModel)
            .where(BillingInvoiceModel.owner_id == owner_id)
            .order_by(BillingInvoiceModel.created_at.desc())
            .limit(limit).offset(offset)
        )
        return list(r.scalars().all())

    async def update_checkout(self, invoice_id: uuid.UUID, *, bc_job_id: str | None = None,
                              checkout_url: str | None = None, bc_payment_id: str | None = None) -> None:
        values = {}
        if bc_job_id is not None:
            values["bc_job_id"] = bc_job_id
        if checkout_url is not None:
            values["checkout_url"] = checkout_url
        if bc_payment_id is not None:
            values["bc_payment_id"] = bc_payment_id
        if not values:
            return
        await self._db.execute(update(BillingInvoiceModel).where(BillingInvoiceModel.id == invoice_id).values(**values))
        await self._db.flush()

    async def mark_paid(self, invoice_id: uuid.UUID, *, bc_payment_id: str, paid_at: datetime) -> int:
        result = await self._db.execute(
            update(BillingInvoiceModel)
            .where(BillingInvoiceModel.id == invoice_id, BillingInvoiceModel.status == "pending")
            .values(status="paid", bc_payment_id=bc_payment_id, paid_at=paid_at)
        )
        await self._db.flush()
        return result.rowcount

    async def mark_status(self, invoice_id: uuid.UUID, status: str) -> None:
        await self._db.execute(update(BillingInvoiceModel).where(BillingInvoiceModel.id == invoice_id).values(status=status))
        await self._db.flush()

    async def mark_notified(self, invoice_id: uuid.UUID, when: datetime) -> None:
        await self._db.execute(update(BillingInvoiceModel).where(BillingInvoiceModel.id == invoice_id).values(notified_at=when))
        await self._db.flush()

    async def get_pending_with_payment_id_older_than(self, cutoff: datetime, limit: int = 50) -> List[BillingInvoiceModel]:
        r = await self._db.execute(
            select(BillingInvoiceModel)
            .where(
                BillingInvoiceModel.status == "pending",
                BillingInvoiceModel.bc_payment_id.isnot(None),
                BillingInvoiceModel.created_at < cutoff,
            )
            .limit(limit)
        )
        return list(r.scalars().all())
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `python -m pytest tests/unit/test_billing_invoice_repo.py -v`
Expected: PASS (2 testes).

- [ ] **Step 5: Commit**

```bash
git add app/infra/repositories/billing_invoice_repo.py tests/unit/test_billing_invoice_repo.py
git commit -m "feat(billing): repositório de faturas billing_invoices"
```

---

### Task 5: `InvoiceService` — contratação, ativação e próxima fatura

**Files:**
- Create: `app/application/services/invoice_service.py`
- Test: `tests/unit/test_invoice_service.py`

**Interfaces:**
- Consumes: `SQLAlchemyBillingInvoiceRepository` (Task 4), `SQLAlchemyBillingSubscriptionRepository`, `SQLAlchemyPlanRepository`, `BillingCoreClient.create_payment`/`get_job`.
- Produces:
  - módulo com `PERIOD_DAYS = {"monthly": 30, "semiannual": 180, "annual": 365}` e `price_for_period(plan, subscription_type) -> Decimal`.
  - `InvoiceService.contract(owner_id, plan_id, subscription_type, idempotency_key) -> dict` (`{subscription_id, invoice_id, job_id, checkout_url}`).
  - `InvoiceService.activate_invoice(invoice_id, bc_payment_id, payment_data) -> None` (idempotente).
  - `InvoiceService.generate_next_invoice(subscription) -> Optional[BillingInvoiceModel]`.
  - `InvoiceService.refresh_checkout(invoice_id) -> dict` (poll do job).

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/unit/test_invoice_service.py`:

```python
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.invoice_service import InvoiceService, price_for_period, PERIOD_DAYS


@dataclass
class StubPlan:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "PRO"
    type: str = "pago"
    is_active: bool = True
    price_monthly: Decimal = Decimal("50.00")
    price_180days: Decimal = Decimal("270.00")
    price_annual: Decimal = Decimal("510.00")


@dataclass
class StubInvoice:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    owner_id: uuid.UUID = field(default_factory=uuid.uuid4)
    subscription_id: uuid.UUID = field(default_factory=uuid.uuid4)
    plan_id: Optional[uuid.UUID] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    due_date: Optional[datetime] = None
    amount: Decimal = Decimal("0")
    status: str = "pending"
    bc_payment_id: Optional[str] = None
    checkout_url: Optional[str] = None
    bc_job_id: Optional[str] = None
    idempotency_key: Optional[str] = None


@dataclass
class StubSub:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    owner_id: uuid.UUID = field(default_factory=uuid.uuid4)
    plan_id: Optional[uuid.UUID] = None
    billing_mode: str = "invoice"
    subscription_type: str = "monthly"
    status: str = "pending"
    expires_at: Optional[datetime] = None


class InvoiceRepo:
    def __init__(self):
        self.items = {}
        self.open_by_sub = {}
    async def get_by_idempotency_key(self, key):
        for i in self.items.values():
            if i.idempotency_key == key:
                return i
        return None
    async def get_open_invoice_for_subscription(self, sub_id):
        return self.open_by_sub.get(sub_id)
    async def create(self, **f):
        inv = StubInvoice(id=uuid.uuid4(), **f)
        self.items[inv.id] = inv
        self.open_by_sub[inv.subscription_id] = inv
        return inv
    async def get_by_id(self, iid):
        return self.items.get(iid)
    async def get_by_payment_id(self, pid):
        for i in self.items.values():
            if i.bc_payment_id == pid:
                return i
        return None
    async def update_checkout(self, iid, **kw):
        inv = self.items[iid]
        for k, v in kw.items():
            if v is not None:
                setattr(inv, k, v)
    async def mark_paid(self, iid, *, bc_payment_id, paid_at):
        inv = self.items[iid]
        if inv.status != "pending":
            return 0
        inv.status = "paid"; inv.bc_payment_id = bc_payment_id
        self.open_by_sub.pop(inv.subscription_id, None)
        return 1


class SubRepo:
    def __init__(self, sub):
        self._sub = sub
        self.saved = []
    async def get_by_id(self, sid):
        return self._sub
    async def get_by_idempotency_key(self, key):
        return None
    async def save(self, sub):
        self.saved.append(sub)
        return sub


class PlanRepo:
    def __init__(self, plan):
        self._plan = plan
    async def get_by_id(self, pid):
        return self._plan


@dataclass
class StubSettings:
    BILLING_CORE_SYSTEM: str = "marketfy"
    BILLING_CORE_WEBHOOK_INVOICE_URL: str = "https://api-marketfy/api/v1/webhooks/billing-invoices"
    BILLING_CORE_CHECKOUT_EXPIRATION_MINUTES: int = 30
    PUBLIC_FRONTEND_URL: str = "https://app.marketfy.com"


def test_price_for_period_maps_correctly():
    plan = StubPlan()
    assert price_for_period(plan, "monthly") == Decimal("50.00")
    assert price_for_period(plan, "semiannual") == Decimal("270.00")
    assert price_for_period(plan, "annual") == Decimal("510.00")
    assert PERIOD_DAYS["monthly"] == 30


@pytest.mark.asyncio
async def test_contract_creates_subscription_invoice_and_checkout():
    owner = uuid.uuid4()
    plan = StubPlan()
    inv_repo = InvoiceRepo()
    sub_repo = SubRepo(None)
    plan_repo = PlanRepo(plan)
    bc = AsyncMock()
    bc.create_payment.return_value = {"job_id": "job_1"}
    bc.get_job.return_value = {"status": "done", "result": {"checkout_url": "https://pay/x", "payment_id": "pay_1"}}

    svc = InvoiceService(inv_repo, sub_repo, plan_repo, bc, StubSettings())
    result = await svc.contract(owner, plan.id, "monthly", idempotency_key="idem-1")

    assert result["checkout_url"] == "https://pay/x"
    assert result["invoice_id"] is not None
    bc.create_payment.assert_awaited_once()


@pytest.mark.asyncio
async def test_activate_invoice_activates_subscription_idempotently():
    owner = uuid.uuid4()
    plan = StubPlan()
    now = datetime.utcnow()
    sub = StubSub(owner_id=owner, plan_id=plan.id, status="pending")
    inv_repo = InvoiceRepo()
    inv = await inv_repo.create(owner_id=owner, subscription_id=sub.id, plan_id=plan.id,
                                period_start=now, period_end=now + timedelta(days=30),
                                due_date=now, amount=Decimal("50.00"), idempotency_key="idem-1")
    sub_repo = SubRepo(sub)
    svc = InvoiceService(inv_repo, sub_repo, PlanRepo(plan), AsyncMock(), StubSettings())

    await svc.activate_invoice(inv.id, "pay_1", {})
    await svc.activate_invoice(inv.id, "pay_1", {})  # idempotente

    assert sub.status == "active"
    assert sub.expires_at is not None
    assert inv.status == "paid"
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `python -m pytest tests/unit/test_invoice_service.py -v`
Expected: FAIL (módulo `invoice_service` inexistente).

- [ ] **Step 3: Implementar o serviço**

Crie `app/application/services/invoice_service.py`:

```python
"""InvoiceService — assinatura no modo 'por pagamento' (faturas via /payments)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Optional

from infra.config.logger import get_logger

logger = get_logger("invoice_service")

PERIOD_DAYS = {"monthly": 30, "semiannual": 180, "annual": 365}


def price_for_period(plan, subscription_type: str) -> Decimal:
    mapping = {
        "monthly": getattr(plan, "price_monthly", 0) or 0,
        "semiannual": getattr(plan, "price_180days", 0) or 0,
        "annual": getattr(plan, "price_annual", 0) or 0,
    }
    return Decimal(str(mapping.get(subscription_type, mapping["monthly"])))


class InvoiceService:
    def __init__(self, invoice_repo, subscription_repo, plan_repo, billing_client, settings):
        self._inv = invoice_repo
        self._sub = subscription_repo
        self._plan = plan_repo
        self._bc = billing_client
        self._settings = settings

    # -- Contratação -------------------------------------------------------
    async def contract(self, owner_id: uuid.UUID, plan_id: uuid.UUID,
                        subscription_type: str, idempotency_key: str) -> Dict[str, Any]:
        if subscription_type not in PERIOD_DAYS:
            raise ValueError("subscription_type inválido para faturas.")

        # Idempotência: já contratado com essa chave?
        existing_inv = await self._inv.get_by_idempotency_key(idempotency_key)
        if existing_inv is not None:
            return {
                "subscription_id": str(existing_inv.subscription_id),
                "invoice_id": str(existing_inv.id),
                "job_id": existing_inv.bc_job_id,
                "checkout_url": existing_inv.checkout_url,
            }

        plan = await self._plan.get_by_id(plan_id)
        if plan is None or not plan.is_active:
            raise ValueError("Plano não disponível.")

        from infra.database.models import BillingSubscriptionModel
        sub = BillingSubscriptionModel(
            owner_id=owner_id, plan_id=plan_id,
            billing_system=self._settings.BILLING_CORE_SYSTEM,
            billing_system_sub_id=str(owner_id),
            billing_mode="invoice",
            status="pending",
            subscription_type=subscription_type,
            value=price_for_period(plan, subscription_type),
            idempotency_key=f"invsub-{owner_id}-{plan_id}-{subscription_type}",
        )
        sub = await self._sub.save(sub)

        now = datetime.utcnow()
        invoice = await self._create_invoice(
            owner_id=owner_id, subscription=sub, plan=plan,
            period_start=now, due_date=now,
            idempotency_key=idempotency_key,
        )
        await self._create_checkout(invoice, plan)
        checkout = await self.refresh_checkout(invoice.id)

        return {
            "subscription_id": str(sub.id),
            "invoice_id": str(invoice.id),
            "job_id": invoice.bc_job_id,
            "checkout_url": checkout.get("checkout_url"),
        }

    async def _create_invoice(self, *, owner_id, subscription, plan, period_start,
                              due_date, idempotency_key):
        days = PERIOD_DAYS[subscription.subscription_type]
        period_end = period_start + timedelta(days=days)
        return await self._inv.create(
            owner_id=owner_id,
            subscription_id=subscription.id,
            plan_id=plan.id,
            period_start=period_start,
            period_end=period_end,
            due_date=due_date,
            amount=price_for_period(plan, subscription.subscription_type),
            status="pending",
            idempotency_key=idempotency_key,
        )

    async def _create_checkout(self, invoice, plan) -> None:
        s = self._settings
        result = await self._bc.create_payment(
            value=f"{Decimal(str(invoice.amount)):.2f}",
            description=f"Assinatura Marketfy {plan.name} — {invoice.subscription_id}",
            system=s.BILLING_CORE_SYSTEM,
            system_payment_id=str(invoice.id),
            webhook_link=s.BILLING_CORE_WEBHOOK_INVOICE_URL,
            idempotency_key=str(invoice.id),
            minutes_to_expire=s.BILLING_CORE_CHECKOUT_EXPIRATION_MINUTES,
            items=[{
                "external_reference": str(invoice.id),
                "name": f"Assinatura {plan.name}",
                "description": "Fatura de assinatura Marketfy",
                "quantity": 1,
                "value": f"{Decimal(str(invoice.amount)):.2f}",
            }],
            success_url=f"{s.PUBLIC_FRONTEND_URL.rstrip('/')}/billing/success",
            cancel_url=f"{s.PUBLIC_FRONTEND_URL.rstrip('/')}/billing/cancel",
            expired_url=f"{s.PUBLIC_FRONTEND_URL.rstrip('/')}/billing/expired",
        )
        await self._inv.update_checkout(invoice.id, bc_job_id=result.get("job_id"))
        invoice.bc_job_id = result.get("job_id")

    async def refresh_checkout(self, invoice_id: uuid.UUID) -> Dict[str, Any]:
        invoice = await self._inv.get_by_id(invoice_id)
        if invoice is None:
            return {"status": "not_found", "checkout_url": None}
        if invoice.checkout_url:
            return {"status": "ready", "checkout_url": invoice.checkout_url}
        if not invoice.bc_job_id:
            return {"status": "pending", "checkout_url": None}
        job = await self._bc.get_job(invoice.bc_job_id)
        result = job.get("result") or {}
        checkout_url = result.get("checkout_url") or job.get("checkout_url")
        bc_payment_id = result.get("payment_id") or job.get("payment_id")
        if checkout_url or bc_payment_id:
            await self._inv.update_checkout(invoice_id, checkout_url=checkout_url, bc_payment_id=bc_payment_id)
        return {"status": job.get("status", "processing"), "checkout_url": checkout_url}

    # -- Ativação (webhook) ------------------------------------------------
    async def activate_invoice(self, invoice_id: uuid.UUID, bc_payment_id: str,
                               payment_data: Dict[str, Any]) -> None:
        invoice = await self._inv.get_by_id(invoice_id)
        if invoice is None:
            logger.warning("invoice_not_found", extra={"extra_data": {"invoice_id": str(invoice_id)}})
            return
        rows = await self._inv.mark_paid(invoice_id, bc_payment_id=bc_payment_id, paid_at=datetime.utcnow())
        if rows == 0:
            logger.info("invoice_already_paid", extra={"extra_data": {"invoice_id": str(invoice_id)}})
            return
        sub = await self._sub.get_by_id(invoice.subscription_id)
        if sub is not None:
            sub.status = "active"
            sub.expires_at = invoice.period_end
            sub.last_event_at = datetime.utcnow()
            await self._sub.save(sub)
        logger.info("invoice_activated", extra={"extra_data": {
            "invoice_id": str(invoice_id), "subscription_id": str(invoice.subscription_id)}})

    async def mark_invoice_failed(self, invoice_id: uuid.UUID, reason: str = "") -> None:
        await self._inv.mark_status(invoice_id, "canceled")
        logger.info("invoice_failed", extra={"extra_data": {"invoice_id": str(invoice_id), "reason": reason}})

    # -- Próxima fatura (worker) ------------------------------------------
    async def generate_next_invoice(self, subscription):
        """Gera a próxima fatura de uma assinatura invoice ativa, se necessário."""
        open_inv = await self._inv.get_open_invoice_for_subscription(subscription.id)
        if open_inv is not None:
            return None  # já existe fatura pendente
        plan = await self._plan.get_by_id(subscription.plan_id)
        if plan is None:
            return None
        period_start = subscription.expires_at or datetime.utcnow()
        idem = f"inv-{subscription.id}-{period_start.strftime('%Y%m%d')}"
        invoice = await self._create_invoice(
            owner_id=subscription.owner_id, subscription=subscription, plan=plan,
            period_start=period_start, due_date=period_start, idempotency_key=idem,
        )
        await self._create_checkout(invoice, plan)
        await self.refresh_checkout(invoice.id)
        return invoice
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `python -m pytest tests/unit/test_invoice_service.py -v`
Expected: PASS (3 testes).

- [ ] **Step 5: Commit**

```bash
git add app/application/services/invoice_service.py tests/unit/test_invoice_service.py
git commit -m "feat(billing): InvoiceService (contratação, ativação, próxima fatura)"
```

---

### Task 6: Endpoint `POST /billing/subscribe` (modo invoice) + settings

**Files:**
- Modify: `app/infra/config/settings.py` (adicionar `BILLING_CORE_WEBHOOK_INVOICE_URL`)
- Modify: `app/application/dtos.py` (novo `SubscribeRequestDTO`)
- Modify: `app/infra/web/routers/billing.py` (novo endpoint + factory do InvoiceService)
- Test: `tests/unit/test_subscribe_endpoint.py`

**Interfaces:**
- Consumes: `InvoiceService.contract` (Task 5).
- Produces: `POST /api/v1/billing/subscribe` aceitando `{plan_id, subscription_type, billing_mode, document?, idempotency_key?}`. Para `billing_mode="invoice"` retorna `{subscription_id, invoice_id, job_id, checkout_url}`. `billing_mode="recurring"` é tratado na Task 12 (por ora retorna 400 "em breve").

- [ ] **Step 1: Adicionar setting do webhook de faturas**

Em `app/infra/config/settings.py`, após `BILLING_CORE_WEBHOOK_CALLBACK_URL`:
```python
    BILLING_CORE_WEBHOOK_INVOICE_URL: str = ""  # webhook_link dedicado das faturas de assinatura
```

- [ ] **Step 2: Escrever o teste que falha**

Crie `tests/unit/test_subscribe_endpoint.py`:

```python
from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.dtos import SubscribeRequestDTO


def test_subscribe_dto_requires_mode_and_period():
    import uuid
    dto = SubscribeRequestDTO(
        plan_id=uuid.uuid4(),
        subscription_type="monthly",
        billing_mode="invoice",
    )
    assert dto.billing_mode == "invoice"
    assert dto.subscription_type == "monthly"
    assert dto.document is None
```

- [ ] **Step 3: Rodar o teste e ver falhar**

Run: `python -m pytest tests/unit/test_subscribe_endpoint.py -v`
Expected: FAIL (`SubscribeRequestDTO` inexistente).

- [ ] **Step 4: Criar o DTO**

Em `app/application/dtos.py`, após `InitiateSubscriptionRequestDTO`:
```python
class SubscribeRequestDTO(BaseModel):
    """Contratação self-service (novo fluxo)."""
    plan_id: UUID
    subscription_type: str            # monthly | semiannual | annual
    billing_mode: str                 # recurring | invoice
    document: Optional[str] = None    # CPF/CNPJ (obrigatório no recorrente)
    idempotency_key: Optional[str] = None
```

- [ ] **Step 5: Rodar o teste e ver passar**

Run: `python -m pytest tests/unit/test_subscribe_endpoint.py -v`
Expected: PASS.

- [ ] **Step 6: Implementar o endpoint**

Em `app/infra/web/routers/billing.py`, adicione o factory e o endpoint:

```python
def _get_invoice_service(db: AsyncSession = Depends(get_db)):
    from application.services.invoice_service import InvoiceService
    from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
    from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository
    return InvoiceService(
        invoice_repo=SQLAlchemyBillingInvoiceRepository(db),
        subscription_repo=SQLAlchemyBillingSubscriptionRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        billing_client=BillingCoreClient(),
        settings=settings,
    )


@router.post("/subscribe", status_code=status.HTTP_202_ACCEPTED)
async def subscribe(
    request: Request,
    dto: "SubscribeRequestDTO",
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    invoice_service=Depends(_get_invoice_service),
    audit: AuditService = Depends(get_audit_service),
):
    from application.dtos import SubscribeRequestDTO  # noqa
    if dto.subscription_type not in ("monthly", "semiannual", "annual"):
        raise HTTPException(status_code=400, detail="subscription_type inválido.")
    if dto.billing_mode not in ("invoice", "recurring"):
        raise HTTPException(status_code=400, detail="billing_mode inválido.")

    idem = dto.idempotency_key or f"sub-{current_user.id}-{dto.plan_id}-{dto.subscription_type}-{dto.billing_mode}"

    if dto.billing_mode == "invoice":
        try:
            result = await invoice_service.contract(
                owner_id=current_user.id, plan_id=dto.plan_id,
                subscription_type=dto.subscription_type, idempotency_key=idem,
            )
            await db.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except BillingCoreError as exc:
            logger.warning(f"[billing] Billing Core indisponível subscribe user={current_user.id}: {exc}")
            raise HTTPException(status_code=503, detail="Serviço de cobrança temporariamente indisponível.")
        await record_audit_event(
            audit, request, actor=current_user, action="billing.subscribe.invoice",
            resource_type="billing_subscription", resource_id=result.get("subscription_id"),
            result="success", metadata={"plan_id": str(dto.plan_id), "subscription_type": dto.subscription_type},
        )
        return result

    # recurring — implementado na Task 12
    raise HTTPException(status_code=400, detail="Cobrança recorrente em configuração. Use faturas por enquanto.")
```

Ajuste o import no topo de `billing.py` para incluir o DTO:
`from application.dtos import (... , SubscribeRequestDTO)`.

- [ ] **Step 7: Rodar suíte de billing e o teste do DTO**

Run: `python -m pytest tests/unit/test_subscribe_endpoint.py tests/unit/test_phase4_billing.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/infra/config/settings.py app/application/dtos.py app/infra/web/routers/billing.py tests/unit/test_subscribe_endpoint.py
git commit -m "feat(billing): endpoint POST /billing/subscribe (modo faturas)"
```

---

### Task 7: Webhook `POST /webhooks/billing-invoices`

**Files:**
- Create: `app/infra/web/routers/billing_invoice_webhooks.py`
- Modify: `app/infra/web/main.py` (registrar o router)
- Test: `tests/unit/test_billing_invoice_webhook.py`

**Interfaces:**
- Consumes: `InvoiceService.activate_invoice`/`mark_invoice_failed` (Task 5), validação HMAC (mesma de `billing_core_webhooks.validate_bc_signature`).
- Produces: rota `POST /api/v1/webhooks/billing-invoices` que ativa a fatura em `PAID/CONFIRMED/RECEIVED`.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/unit/test_billing_invoice_webhook.py`:

```python
from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import AsyncMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.web.routers.billing_invoice_webhooks import InvoiceWebhookProcessor


@pytest.mark.asyncio
async def test_paid_status_activates_invoice():
    invoice_service = AsyncMock()
    invoice_id = uuid.uuid4()
    proc = InvoiceWebhookProcessor(invoice_service)
    payload = {"payment_id": "pay_1", "system_payment_id": str(invoice_id), "payment_status": "PAID"}
    code = await proc.process(payload)
    assert code == 200
    invoice_service.activate_invoice.assert_awaited_once()


@pytest.mark.asyncio
async def test_overdue_status_marks_failed():
    invoice_service = AsyncMock()
    invoice_id = uuid.uuid4()
    proc = InvoiceWebhookProcessor(invoice_service)
    payload = {"payment_id": "pay_2", "system_payment_id": str(invoice_id), "payment_status": "OVERDUE"}
    code = await proc.process(payload)
    assert code == 200
    invoice_service.mark_invoice_failed.assert_awaited_once()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `python -m pytest tests/unit/test_billing_invoice_webhook.py -v`
Expected: FAIL (módulo inexistente).

- [ ] **Step 3: Implementar o webhook**

Crie `app/infra/web/routers/billing_invoice_webhooks.py`:

```python
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from infra.config.logger import get_logger
from infra.config.settings import get_settings
from infra.database.setup import get_db
from infra.web.routers.billing_core_webhooks import validate_bc_signature

logger = get_logger("billing_invoice_webhooks")
router = APIRouter()

_PAID = ("PAID", "CONFIRMED", "RECEIVED", "RECEIVED_IN_CASH")
_FAILED = ("OVERDUE", "REFUNDED", "REFUND_IN_PROGRESS", "CHARGEBACK_REQUESTED", "CANCELED", "EXPIRED")


class InvoiceWebhookProcessor:
    def __init__(self, invoice_service):
        self._svc = invoice_service

    async def process(self, payload: dict) -> int:
        payment_id = str(payload.get("payment_id") or "")
        system_payment_id = str(payload.get("system_payment_id") or "")
        payment_status = str(payload.get("payment_status") or "").upper()
        if not payment_id or not payment_status:
            logger.warning("invoice_webhook_invalid_payload")
            return 200
        try:
            invoice_id = uuid.UUID(system_payment_id)
        except (TypeError, ValueError):
            logger.warning("invoice_webhook_invalid_id", extra={"extra_data": {"system_payment_id": system_payment_id}})
            return 200
        if payment_status in _PAID:
            await self._svc.activate_invoice(invoice_id, payment_id, payload)
        elif payment_status in _FAILED:
            await self._svc.mark_invoice_failed(invoice_id, reason=f"Status: {payment_status}")
        return 200


@router.post("/billing-invoices")
async def billing_invoice_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}

    signature = request.headers.get("X-Webhook-Signature-256")
    if not signature or not validate_bc_signature(payload, signature, settings.BILLING_CORE_WEBHOOK_SECRET):
        logger.warning("invoice_webhook_invalid_signature")
        return Response(status_code=401)

    from application.services.invoice_service import InvoiceService
    from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
    from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository
    from infra.clients.billing_core_client import BillingCoreClient

    svc = InvoiceService(
        invoice_repo=SQLAlchemyBillingInvoiceRepository(db),
        subscription_repo=SQLAlchemyBillingSubscriptionRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        billing_client=BillingCoreClient(),
        settings=settings,
    )
    processor = InvoiceWebhookProcessor(svc)
    code = await processor.process(payload)
    await db.commit()
    return Response(status_code=code)
```

- [ ] **Step 4: Registrar o router**

Em `app/infra/web/main.py`, localize onde `billing_core_webhooks` é incluído (grep `billing_core_webhooks`) e adicione, no mesmo prefixo de webhooks (`/api/v1/webhooks`):
```python
from infra.web.routers import billing_invoice_webhooks
app.include_router(billing_invoice_webhooks.router, prefix="/api/v1/webhooks", tags=["Billing Webhooks"])
```
(Use o mesmo padrão/prefixo já usado pela linha de `billing_core_webhooks`.)

- [ ] **Step 5: Rodar o teste e ver passar**

Run: `python -m pytest tests/unit/test_billing_invoice_webhook.py -v`
Expected: PASS (2 testes).

- [ ] **Step 6: Commit**

```bash
git add app/infra/web/routers/billing_invoice_webhooks.py app/infra/web/main.py tests/unit/test_billing_invoice_webhook.py
git commit -m "feat(billing): webhook dedicado de faturas /webhooks/billing-invoices"
```

---

### Task 8: Endpoints de listagem de faturas

**Files:**
- Modify: `app/infra/web/routers/billing.py` (novos endpoints `GET /invoices`, `GET /invoices/{id}`)
- Test: `tests/unit/test_invoice_list_endpoint.py`

**Interfaces:**
- Consumes: `SQLAlchemyBillingInvoiceRepository.list_by_owner`, `InvoiceService.refresh_checkout`.
- Produces: `GET /api/v1/billing/invoices` → lista de faturas do owner; `GET /api/v1/billing/invoices/{id}` → detalhe com `checkout_url` atualizado.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/unit/test_invoice_list_endpoint.py`:

```python
from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.web.routers import billing as billing_router


def test_invoice_endpoints_registered():
    paths = {r.path for r in billing_router.router.routes}
    assert "/invoices" in paths
    assert "/invoices/{invoice_id}" in paths
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `python -m pytest tests/unit/test_invoice_list_endpoint.py -v`
Expected: FAIL (rotas inexistentes).

- [ ] **Step 3: Implementar os endpoints**

Em `app/infra/web/routers/billing.py`:

```python
@router.get("/invoices")
async def list_invoices(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository
    repo = SQLAlchemyBillingInvoiceRepository(db)
    items = await repo.list_by_owner(current_user.id, limit=50, offset=0)
    return {
        "items": [
            {
                "invoice_id": str(i.id),
                "amount": str(i.amount),
                "status": i.status,
                "period_start": i.period_start.isoformat() if i.period_start else None,
                "period_end": i.period_end.isoformat() if i.period_end else None,
                "due_date": i.due_date.isoformat() if i.due_date else None,
                "checkout_url": i.checkout_url,
                "paid_at": i.paid_at.isoformat() if i.paid_at else None,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in items
        ]
    }


@router.get("/invoices/{invoice_id}")
async def get_invoice(
    invoice_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    invoice_service=Depends(_get_invoice_service),
):
    from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository
    repo = SQLAlchemyBillingInvoiceRepository(db)
    inv = await repo.get_by_id(invoice_id)
    if inv is None or inv.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Fatura não encontrada.")
    checkout = await invoice_service.refresh_checkout(invoice_id)
    await db.commit()
    return {
        "invoice_id": str(inv.id),
        "amount": str(inv.amount),
        "status": inv.status,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "checkout_url": checkout.get("checkout_url") or inv.checkout_url,
    }
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `python -m pytest tests/unit/test_invoice_list_endpoint.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/infra/web/routers/billing.py tests/unit/test_invoice_list_endpoint.py
git commit -m "feat(billing): endpoints de listagem/detalhe de faturas"
```

---

## FASE 3 — Worker (geração e reconciliação de faturas)

### Task 9: Query de assinaturas a faturar + job `generate_due_invoices`

**Files:**
- Modify: `app/infra/repositories/billing_repo.py` (novo método em `SQLAlchemyBillingSubscriptionRepository`)
- Create: `app/application/jobs/billing_jobs.py`
- Test: `tests/unit/test_billing_jobs_generate.py`

**Interfaces:**
- Produces:
  - `SQLAlchemyBillingSubscriptionRepository.list_invoice_subs_expiring_within(cutoff) -> List[BillingSubscriptionModel]` (modo invoice, status active, `expires_at <= cutoff`).
  - `generate_due_invoices(ctx, *, sub_repo=None, invoice_service=None, email_gateway=None) -> dict` — para cada assinatura, gera a próxima fatura (via `InvoiceService.generate_next_invoice`), marca `notified_at` e (se `email_gateway`) envia e-mail.

- [ ] **Step 1: Adicionar o método de query ao repositório**

Em `app/infra/repositories/billing_repo.py`, dentro de `SQLAlchemyBillingSubscriptionRepository`:
```python
    async def list_invoice_subs_expiring_within(self, cutoff: datetime) -> List[BillingSubscriptionModel]:
        """Assinaturas modo invoice, ativas, com expires_at até o cutoff (janela de faturamento)."""
        result = await self._db.execute(
            select(BillingSubscriptionModel).where(
                BillingSubscriptionModel.billing_mode == "invoice",
                BillingSubscriptionModel.status == "active",
                BillingSubscriptionModel.expires_at.isnot(None),
                BillingSubscriptionModel.expires_at <= cutoff,
            )
        )
        return list(result.scalars().all())
```

- [ ] **Step 2: Escrever o teste que falha**

Crie `tests/unit/test_billing_jobs_generate.py`:

```python
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from unittest.mock import AsyncMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.jobs.billing_jobs import generate_due_invoices


@dataclass
class StubSub:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    owner_id: uuid.UUID = field(default_factory=uuid.uuid4)
    plan_id: uuid.UUID = field(default_factory=uuid.uuid4)
    expires_at: Optional[datetime] = None


@dataclass
class StubInvoice:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    owner_id: uuid.UUID = field(default_factory=uuid.uuid4)
    amount: str = "50.00"
    due_date: Optional[datetime] = None


class SubRepo:
    def __init__(self, subs):
        self._subs = subs
    async def list_invoice_subs_expiring_within(self, cutoff):
        return self._subs


@pytest.mark.asyncio
async def test_generates_invoice_for_expiring_subscription():
    sub = StubSub(expires_at=datetime.utcnow() + timedelta(days=3))
    invoice_service = AsyncMock()
    invoice_service.generate_next_invoice.return_value = StubInvoice(owner_id=sub.owner_id)
    invoice_repo = AsyncMock()
    result = await generate_due_invoices(
        {}, sub_repo=SubRepo([sub]), invoice_service=invoice_service,
        invoice_repo=invoice_repo, email_gateway=None, user_repo=AsyncMock(),
    )
    assert result["generated"] == 1
    invoice_service.generate_next_invoice.assert_awaited_once()
    invoice_repo.mark_notified.assert_awaited_once()


@pytest.mark.asyncio
async def test_skips_when_no_invoice_generated():
    sub = StubSub(expires_at=datetime.utcnow() + timedelta(days=3))
    invoice_service = AsyncMock()
    invoice_service.generate_next_invoice.return_value = None  # já existe pendente
    result = await generate_due_invoices(
        {}, sub_repo=SubRepo([sub]), invoice_service=invoice_service,
        invoice_repo=AsyncMock(), email_gateway=None, user_repo=AsyncMock(),
    )
    assert result["generated"] == 0
```

- [ ] **Step 3: Rodar o teste e ver falhar**

Run: `python -m pytest tests/unit/test_billing_jobs_generate.py -v`
Expected: FAIL (módulo `billing_jobs` inexistente).

- [ ] **Step 4: Implementar o job**

Crie `app/application/jobs/billing_jobs.py`:

```python
"""Jobs ARQ de billing/assinatura: geração e reconciliação de faturas."""

from __future__ import annotations

from datetime import datetime, timedelta

from infra.config.logger import get_logger

logger = get_logger("billing_jobs")

INVOICE_LEAD_DAYS = 5  # gera a próxima fatura ~5 dias antes do vencimento


async def generate_due_invoices(
    ctx: dict,
    *,
    sub_repo=None,
    invoice_service=None,
    invoice_repo=None,
    email_gateway=None,
    user_repo=None,
) -> dict:
    """Gera a próxima fatura para assinaturas invoice próximas do vencimento.

    Injetável para teste. Em produção (sub_repo=None), monta as dependências
    a partir de uma sessão nova.
    """
    if sub_repo is None:
        return await _run_generate_due_invoices_with_session(ctx)

    cutoff = datetime.utcnow() + timedelta(days=INVOICE_LEAD_DAYS)
    subs = await sub_repo.list_invoice_subs_expiring_within(cutoff)
    generated = 0
    emailed = 0
    for sub in subs:
        try:
            invoice = await invoice_service.generate_next_invoice(sub)
            if invoice is None:
                continue
            generated += 1
            await invoice_repo.mark_notified(invoice.id, datetime.utcnow())
            if email_gateway is not None and user_repo is not None:
                try:
                    user = await user_repo.get_by_id(sub.owner_id)
                    if user is not None:
                        email = user.email.value if hasattr(user.email, "value") else user.email
                        await email_gateway.send_invoice_available(
                            to_email=email,
                            to_name=getattr(user, "full_name", getattr(user, "name", "")),
                            amount=str(invoice.amount),
                            due_date=invoice.due_date.strftime("%d/%m/%Y") if invoice.due_date else "",
                            checkout_url=invoice.checkout_url or "",
                        )
                        emailed += 1
                except Exception as exc:  # e-mail nunca bloqueia a geração
                    logger.warning("invoice_email_failed", extra={"extra_data": {"error": str(exc)}})
        except Exception as exc:
            logger.error("generate_invoice_error", extra={"extra_data": {"sub_id": str(sub.id), "error": str(exc)}})
    logger.info("generate_due_invoices_done", extra={"extra_data": {"generated": generated, "emailed": emailed}})
    return {"generated": generated, "emailed": emailed}


async def _run_generate_due_invoices_with_session(ctx: dict) -> dict:
    from infra.database.setup import async_session_factory
    from infra.config.settings import get_settings
    from infra.clients.billing_core_client import BillingCoreClient
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
    from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository
    from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository, SQLAlchemyUserRepository
    from application.services.invoice_service import InvoiceService

    settings = get_settings()
    async with async_session_factory() as session:
        sub_repo = SQLAlchemyBillingSubscriptionRepository(session)
        invoice_repo = SQLAlchemyBillingInvoiceRepository(session)
        invoice_service = InvoiceService(
            invoice_repo=invoice_repo,
            subscription_repo=sub_repo,
            plan_repo=SQLAlchemyPlanRepository(session),
            billing_client=BillingCoreClient(),
            settings=settings,
        )
        email_gateway = _build_email_gateway(settings)  # ver Task 14 (retorna None se sem config)
        result = await generate_due_invoices(
            ctx, sub_repo=sub_repo, invoice_service=invoice_service,
            invoice_repo=invoice_repo, email_gateway=email_gateway,
            user_repo=SQLAlchemyUserRepository(session),
        )
        await session.commit()
        return result


def _build_email_gateway(settings):
    """Placeholder até a Task 14; retorna None (sem e-mail) por ora."""
    return None
```

- [ ] **Step 5: Rodar o teste e ver passar**

Run: `python -m pytest tests/unit/test_billing_jobs_generate.py -v`
Expected: PASS (2 testes).

- [ ] **Step 6: Commit**

```bash
git add app/infra/repositories/billing_repo.py app/application/jobs/billing_jobs.py tests/unit/test_billing_jobs_generate.py
git commit -m "feat(billing): job generate_due_invoices + query de assinaturas a faturar"
```

---

### Task 10: Job `reconcile_pending_invoices` + registro no worker

**Files:**
- Modify: `app/application/jobs/billing_jobs.py` (adicionar `reconcile_pending_invoices`)
- Modify: `worker.py` (registrar funções e crons)
- Test: `tests/unit/test_billing_jobs_reconcile.py`

**Interfaces:**
- Consumes: `SQLAlchemyBillingInvoiceRepository.get_pending_with_payment_id_older_than`, `BillingCoreClient.get_payment`, `InvoiceService.activate_invoice`/`mark_invoice_failed`.
- Produces: `reconcile_pending_invoices(ctx, *, invoice_repo=None, bc_client=None, invoice_service=None) -> dict`.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/unit/test_billing_jobs_reconcile.py`:

```python
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from unittest.mock import AsyncMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.jobs.billing_jobs import reconcile_pending_invoices


@dataclass
class StubInvoice:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    bc_payment_id: str = "pay_1"


class InvRepo:
    def __init__(self, invs):
        self._invs = invs
    async def get_pending_with_payment_id_older_than(self, cutoff, limit=50):
        return self._invs


@pytest.mark.asyncio
async def test_reconcile_activates_confirmed_payment():
    inv = StubInvoice()
    bc = AsyncMock()
    bc.get_payment.return_value = {"payment_id": "pay_1", "payment_status": "CONFIRMED"}
    svc = AsyncMock()
    result = await reconcile_pending_invoices({}, invoice_repo=InvRepo([inv]), bc_client=bc, invoice_service=svc)
    assert result["activated"] == 1
    svc.activate_invoice.assert_awaited_once()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `python -m pytest tests/unit/test_billing_jobs_reconcile.py -v`
Expected: FAIL (função inexistente).

- [ ] **Step 3: Implementar `reconcile_pending_invoices`**

Adicione ao final de `app/application/jobs/billing_jobs.py`:

```python
async def reconcile_pending_invoices(
    ctx: dict,
    *,
    invoice_repo=None,
    bc_client=None,
    invoice_service=None,
) -> dict:
    """Reconcilia faturas pendentes com bc_payment_id contra o billing core."""
    if invoice_repo is None:
        return await _run_reconcile_pending_invoices_with_session(ctx)

    from infra.clients.billing_core_client import BillingCoreRateLimitError
    from infra.config.settings import get_settings

    settings = get_settings()
    cutoff = datetime.utcnow() - timedelta(minutes=settings.RECONCILE_PENDING_MINUTES)
    invoices = await invoice_repo.get_pending_with_payment_id_older_than(cutoff)

    checked = activated = failed = errors = 0
    for inv in invoices:
        checked += 1
        try:
            payment = await bc_client.get_payment(inv.bc_payment_id)
            status = (payment.get("payment_status") or "").upper()
            if status in ("CONFIRMED", "RECEIVED", "RECEIVED_IN_CASH", "PAID"):
                await invoice_service.activate_invoice(inv.id, payment["payment_id"], payment)
                activated += 1
            elif status in ("OVERDUE", "REFUNDED", "CANCELED", "EXPIRED"):
                await invoice_service.mark_invoice_failed(inv.id, reason=f"Status: {status}")
                failed += 1
        except BillingCoreRateLimitError:
            logger.warning("invoice_reconcile_rate_limited")
            break
        except Exception as exc:
            errors += 1
            logger.error("invoice_reconcile_error", extra={"extra_data": {"invoice_id": str(inv.id), "error": str(exc)}})
    return {"checked": checked, "activated": activated, "failed": failed, "errors": errors}


async def _run_reconcile_pending_invoices_with_session(ctx: dict) -> dict:
    from infra.database.setup import async_session_factory
    from infra.config.settings import get_settings
    from infra.clients.billing_core_client import BillingCoreClient
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
    from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository
    from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository
    from application.services.invoice_service import InvoiceService

    settings = get_settings()
    async with async_session_factory() as session:
        invoice_repo = SQLAlchemyBillingInvoiceRepository(session)
        invoice_service = InvoiceService(
            invoice_repo=invoice_repo,
            subscription_repo=SQLAlchemyBillingSubscriptionRepository(session),
            plan_repo=SQLAlchemyPlanRepository(session),
            billing_client=BillingCoreClient(),
            settings=settings,
        )
        result = await reconcile_pending_invoices(
            ctx, invoice_repo=invoice_repo, bc_client=BillingCoreClient(), invoice_service=invoice_service,
        )
        await session.commit()
        return result
```

- [ ] **Step 4: Registrar os jobs no worker**

Em `worker.py`, importe e registre. Adicione ao import de jobs:
```python
from application.jobs.billing_jobs import (
    generate_due_invoices,
    reconcile_pending_invoices,
)
```
Adicione ambas em `WorkerSettings.functions` (na lista existente).
Adicione aos `cron_jobs`:
```python
        cron(generate_due_invoices, hour=8, minute=0),               # diário 08:00 UTC
        cron(reconcile_pending_invoices, minute={2, 12, 22, 32, 42, 52}),
```

- [ ] **Step 5: Rodar o teste e verificar import do worker**

Run:
```bash
python -m pytest tests/unit/test_billing_jobs_reconcile.py -v
python -c "import sys, os; sys.path.insert(0, 'app'); import worker; print('worker OK', [f.__name__ for f in worker.WorkerSettings.functions if 'invoice' in f.__name__])"
```
Expected: teste PASS; import do worker sem erro e lista contendo `generate_due_invoices`/`reconcile_pending_invoices`.

- [ ] **Step 6: Commit**

```bash
git add app/application/jobs/billing_jobs.py worker.py tests/unit/test_billing_jobs_reconcile.py
git commit -m "feat(billing): job reconcile_pending_invoices + registro no worker"
```

---

## FASE 4 — Cobrança recorrente

> Contexto verificado: billing `POST /v1/subscriptions` exige `customer_provider_id`
> (criado em `POST /v1/customers`), espera `subscription_type ∈ {MONTHLY, SEMIANNUALLY, YEARLY}`
> (valores do enum), retorna 202 + `job_id`; o resultado do job traz `checkout_url` +
> `subscription_id` (mesmo padrão de polling dos créditos fiscais). Eventos internos:
> `{event, subscription_id, subscription_expires_at, payment_date}` com
> `event ∈ {PAYMENT_RECEIVED, PAYMENT_REFUNDED, SUBSCRIPTION_INACTIVATED}`.

### Task 11: `RecurringService` — documento, customer e assinatura recorrente

**Files:**
- Create: `app/application/services/recurring_service.py`
- Modify: `app/infra/web/routers/billing.py` (branch `recurring` do `POST /subscribe`)
- Test: `tests/unit/test_recurring_service.py`

**Interfaces:**
- Consumes: `BillingCoreClient.create_customer`/`create_subscription`/`get_job`, `SQLAlchemyBillingSubscriptionRepository`, `SQLAlchemyPlanRepository`, `SQLAlchemyUserRepository.update_asaas_customer_id`.
- Produces:
  - `CYCLE_MAP = {"monthly": "MONTHLY", "semiannual": "SEMIANNUALLY", "annual": "YEARLY"}`.
  - `RecurringService.contract(user, plan_id, subscription_type, document, idempotency_key) -> dict` (`{subscription_id, job_id, checkout_url}`).

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/unit/test_recurring_service.py`:

```python
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.recurring_service import RecurringService, CYCLE_MAP


@dataclass
class StubPlan:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "PRO"
    is_active: bool = True
    price_monthly: Decimal = Decimal("50.00")
    price_180days: Decimal = Decimal("270.00")
    price_annual: Decimal = Decimal("510.00")


@dataclass
class StubUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Fulano"
    email: str = "f@t.com"
    asaas_customer_id: Optional[str] = None


class SubRepo:
    def __init__(self):
        self.saved = []
    async def get_by_idempotency_key(self, key):
        return None
    async def save(self, sub):
        self.saved.append(sub)
        return sub


class PlanRepo:
    def __init__(self, plan):
        self._plan = plan
    async def get_by_id(self, pid):
        return self._plan


class UserRepo:
    def __init__(self):
        self.updated = []
    async def update_asaas_customer_id(self, uid, cid):
        self.updated.append((uid, cid))


@dataclass
class StubSettings:
    BILLING_CORE_SYSTEM: str = "marketfy"
    BILLING_CORE_WEBHOOK_HOST: Optional[str] = "https://api-marketfy.neectify.com"


def test_cycle_map_uses_billing_enum_values():
    assert CYCLE_MAP["monthly"] == "MONTHLY"
    assert CYCLE_MAP["semiannual"] == "SEMIANNUALLY"
    assert CYCLE_MAP["annual"] == "YEARLY"


@pytest.mark.asyncio
async def test_contract_creates_customer_and_subscription():
    plan = StubPlan()
    user = StubUser()
    bc = AsyncMock()
    bc.create_customer.return_value = {"provider_customer_id": "cus_1"}
    bc.create_subscription.return_value = {"job_id": "job_1"}
    bc.get_job.return_value = {"status": "done", "result": {"checkout_url": "https://pay/x", "subscription_id": "sub_bc_1"}}

    svc = RecurringService(SubRepo(), PlanRepo(plan), UserRepo(), bc, StubSettings())
    result = await svc.contract(user, plan.id, "monthly", document="12345678901", idempotency_key="idem-1")

    assert result["checkout_url"] == "https://pay/x"
    bc.create_customer.assert_awaited_once()
    bc.create_subscription.assert_awaited_once()
    # ciclo mapeado corretamente
    _, kwargs = bc.create_subscription.call_args
    assert kwargs["subscription_type"] == "MONTHLY"
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `python -m pytest tests/unit/test_recurring_service.py -v`
Expected: FAIL (módulo inexistente).

- [ ] **Step 3: Implementar o serviço**

Crie `app/application/services/recurring_service.py`:

```python
"""RecurringService — assinatura recorrente (cartão) via billing core."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict

from infra.config.logger import get_logger

logger = get_logger("recurring_service")

CYCLE_MAP = {"monthly": "MONTHLY", "semiannual": "SEMIANNUALLY", "annual": "YEARLY"}
PERIOD_DAYS = {"monthly": 30, "semiannual": 180, "annual": 365}


def _price(plan, subscription_type: str) -> Decimal:
    mapping = {
        "monthly": getattr(plan, "price_monthly", 0) or 0,
        "semiannual": getattr(plan, "price_180days", 0) or 0,
        "annual": getattr(plan, "price_annual", 0) or 0,
    }
    return Decimal(str(mapping.get(subscription_type, mapping["monthly"])))


def _only_digits(doc: str) -> str:
    return re.sub(r"\D", "", doc or "")


class RecurringService:
    def __init__(self, subscription_repo, plan_repo, user_repo, billing_client, settings):
        self._sub = subscription_repo
        self._plan = plan_repo
        self._user = user_repo
        self._bc = billing_client
        self._settings = settings

    async def contract(self, user, plan_id: uuid.UUID, subscription_type: str,
                       document: str, idempotency_key: str) -> Dict[str, Any]:
        if subscription_type not in CYCLE_MAP:
            raise ValueError("subscription_type inválido para recorrente.")
        doc = _only_digits(document)
        if len(doc) not in (11, 14):
            raise ValueError("Documento inválido. Informe um CPF (11) ou CNPJ (14 dígitos).")

        existing = await self._sub.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return {"subscription_id": str(existing.id), "job_id": existing.billing_job_id,
                    "checkout_url": existing.checkout_url if hasattr(existing, "checkout_url") else None}

        plan = await self._plan.get_by_id(plan_id)
        if plan is None or not plan.is_active:
            raise ValueError("Plano não disponível.")

        customer_provider_id = await self._ensure_customer(user, doc)

        value = _price(plan, subscription_type)
        expires_at = datetime.utcnow() + timedelta(days=365 * 5)  # validade longa; billing controla ciclo
        webhook_link = self._webhook_link()

        job = await self._bc.create_subscription(
            system_sub_id=str(user.id),
            customer_provider_id=customer_provider_id,
            description=f"Assinatura Marketfy {plan.name}",
            value=float(value),
            subscription_type=CYCLE_MAP[subscription_type],
            expires_at=expires_at,
            webhook_link=webhook_link,
            idempotency_key=idempotency_key,
        )
        job_id = job.get("job_id")

        checkout_url, billing_sub_id = await self._poll_subscription_job(job_id)

        from infra.database.models import BillingSubscriptionModel
        sub = BillingSubscriptionModel(
            owner_id=user.id, plan_id=plan_id,
            billing_system=self._settings.BILLING_CORE_SYSTEM,
            billing_system_sub_id=str(user.id),
            billing_mode="recurring",
            billing_subscription_id=billing_sub_id,
            billing_job_id=job_id,
            customer_provider_id=customer_provider_id,
            status="pending",
            subscription_type=subscription_type,
            value=value,
            expires_at=None,
            idempotency_key=idempotency_key,
        )
        sub = await self._sub.save(sub)

        return {"subscription_id": str(sub.id), "job_id": job_id, "checkout_url": checkout_url}

    async def _ensure_customer(self, user, doc: str) -> str:
        if getattr(user, "asaas_customer_id", None):
            return user.asaas_customer_id
        email = user.email.value if hasattr(user.email, "value") else user.email
        kwargs = {"cpf": doc} if len(doc) == 11 else {"cnpj": doc}
        result = await self._bc.create_customer(
            nome_completo=getattr(user, "full_name", getattr(user, "name", "")),
            email=email,
            system_customer_id=str(user.id),
            system=self._settings.BILLING_CORE_SYSTEM,
            **kwargs,
        )
        provider_id = result["provider_customer_id"]
        await self._user.update_asaas_customer_id(user.id, provider_id)
        return provider_id

    async def _poll_subscription_job(self, job_id: str) -> tuple[str | None, str | None]:
        if not job_id:
            return None, None
        job = await self._bc.get_job(job_id)
        result = job.get("result") or {}
        checkout_url = result.get("checkout_url") or job.get("checkout_url")
        billing_sub_id = result.get("subscription_id") or job.get("subscription_id")
        return checkout_url, billing_sub_id

    def _webhook_link(self) -> str:
        host = self._settings.BILLING_CORE_WEBHOOK_HOST or "http://localhost:8000"
        return f"{host.rstrip('/')}/api/v1/billing/webhooks/internal"
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `python -m pytest tests/unit/test_recurring_service.py -v`
Expected: PASS (2 testes).

- [ ] **Step 5: Ligar o branch recorrente no endpoint**

Em `app/infra/web/routers/billing.py`, no endpoint `subscribe`, substitua a linha final
`raise HTTPException(status_code=400, detail="Cobrança recorrente em configuração...")` por:

```python
    # recurring
    if not dto.document:
        raise HTTPException(status_code=400, detail="Documento (CPF/CNPJ) é obrigatório para cobrança recorrente.")
    from application.services.recurring_service import RecurringService
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
    from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository, SQLAlchemyUserRepository
    rec = RecurringService(
        subscription_repo=SQLAlchemyBillingSubscriptionRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        user_repo=SQLAlchemyUserRepository(db),
        billing_client=BillingCoreClient(),
        settings=settings,
    )
    try:
        result = await rec.contract(
            user=current_user, plan_id=dto.plan_id,
            subscription_type=dto.subscription_type, document=dto.document, idempotency_key=idem,
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except BillingCoreError as exc:
        logger.warning(f"[billing] Billing Core indisponível recurring user={current_user.id}: {exc}")
        raise HTTPException(status_code=503, detail="Serviço de cobrança temporariamente indisponível.")
    await record_audit_event(
        audit, request, actor=current_user, action="billing.subscribe.recurring",
        resource_type="billing_subscription", resource_id=result.get("subscription_id"),
        result="success", metadata={"plan_id": str(dto.plan_id), "subscription_type": dto.subscription_type},
    )
    return result
```

Adicione `SQLAlchemyUserRepository.update_asaas_customer_id` se ainda não existir (verifique com `grep -n "update_asaas_customer_id" app/infra/repositories/sqlalchemy_repos.py`). Se ausente, implemente:
```python
    async def update_asaas_customer_id(self, user_id, customer_id):
        from infra.database.models import UserModel
        from sqlalchemy import update as _u
        await self._db.execute(_u(UserModel).where(UserModel.id == user_id).values(asaas_customer_id=customer_id))
        await self._db.flush()
```
(Use o nome de atributo de sessão real da classe — `self._db` ou `self.session` conforme o arquivo.)

- [ ] **Step 6: Rodar testes de billing**

Run: `python -m pytest tests/unit/test_recurring_service.py tests/unit/test_phase4_billing.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/application/services/recurring_service.py app/infra/web/routers/billing.py app/infra/repositories/sqlalchemy_repos.py tests/unit/test_recurring_service.py
git commit -m "feat(billing): fluxo recorrente (customer + subscription + checkout)"
```

---

### Task 12: Adaptar `/billing/webhooks/internal` ao payload real de assinatura

**Files:**
- Modify: `app/application/services/subscription_service.py` (`process_webhook_event` aceitar payload real)
- Modify: `app/infra/web/routers/billing.py` (`receive_billing_webhook` parsear o payload real)
- Test: `tests/unit/test_internal_webhook_recurring.py`

**Interfaces:**
- Consumes: payload `{event, subscription_id, subscription_expires_at, payment_date}`.
- Produces: `SubscriptionService.process_recurring_event(event, billing_subscription_id, subscription_expires_at, payment_date, raw_payload) -> dict` — idempotente por `event_id` sintetizado; mapeia status.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/unit/test_internal_webhook_recurring.py`:

```python
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.subscription_service import SubscriptionService


@dataclass
class StubSub:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    owner_id: uuid.UUID = field(default_factory=uuid.uuid4)
    plan_id: Optional[uuid.UUID] = None
    billing_subscription_id: Optional[str] = "sub_bc_1"
    status: str = "pending"
    expires_at: Optional[datetime] = None


@dataclass
class StubUser:
    id: uuid.UUID
    plan_id: Optional[uuid.UUID] = None
    plan_expiration: Optional[datetime] = None
    is_active: bool = True


class SubRepo:
    def __init__(self, sub):
        self._sub = sub
        self.saved = []
    async def get_by_billing_subscription_id(self, bid):
        return self._sub if self._sub and self._sub.billing_subscription_id == bid else None
    async def save(self, sub):
        self.saved.append(sub); return sub


class EventRepo:
    def __init__(self):
        self.events = {}
    async def get_by_event_id(self, eid):
        return self.events.get(eid)
    async def save(self, ev):
        self.events[ev.event_id] = ev; return ev


class UserRepo:
    def __init__(self, user):
        self._user = user
        self.saved = []
    async def get_by_id(self, uid):
        return self._user
    async def save(self, u):
        self.saved.append(u); return u


class PlanRepo:
    async def get_by_id(self, pid):
        return None


@pytest.mark.asyncio
async def test_payment_received_activates_and_is_idempotent():
    sub = StubSub()
    user = StubUser(id=sub.owner_id)
    svc = SubscriptionService(UserRepo(user), PlanRepo(), SubRepo(sub), EventRepo())
    exp = datetime(2027, 1, 1)
    r1 = await svc.process_recurring_event(
        event="PAYMENT_RECEIVED", billing_subscription_id="sub_bc_1",
        subscription_expires_at=exp, payment_date=datetime(2026, 1, 1), raw_payload={},
    )
    r2 = await svc.process_recurring_event(
        event="PAYMENT_RECEIVED", billing_subscription_id="sub_bc_1",
        subscription_expires_at=exp, payment_date=datetime(2026, 1, 1), raw_payload={},
    )
    assert r1["result"] == "processed"
    assert r2["result"] == "duplicate"
    assert sub.status == "active"


@pytest.mark.asyncio
async def test_subscription_inactivated_cancels():
    sub = StubSub(status="active")
    user = StubUser(id=sub.owner_id)
    svc = SubscriptionService(UserRepo(user), PlanRepo(), SubRepo(sub), EventRepo())
    r = await svc.process_recurring_event(
        event="SUBSCRIPTION_INACTIVATED", billing_subscription_id="sub_bc_1",
        subscription_expires_at=datetime(2027, 1, 1), payment_date=None, raw_payload={},
    )
    assert r["result"] == "processed"
    assert sub.status == "canceled"
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `python -m pytest tests/unit/test_internal_webhook_recurring.py -v`
Expected: FAIL (`process_recurring_event` inexistente).

- [ ] **Step 3: Implementar `process_recurring_event`**

Em `app/application/services/subscription_service.py`, adicione o método à classe:

```python
    async def process_recurring_event(
        self,
        event: str,
        billing_subscription_id: Optional[str],
        subscription_expires_at: Optional[datetime],
        payment_date: Optional[datetime],
        raw_payload: Dict[str, Any],
    ) -> Dict[str, str]:
        """Processa o webhook interno de assinatura do billing core.

        Payload real: {event, subscription_id, subscription_expires_at, payment_date}.
        event_id é sintetizado para idempotência.
        """
        from infra.database.models import BillingEventModel

        if self._event_repo is None:
            raise BusinessRuleException("Repositório de eventos de billing não configurado.")

        pd = payment_date.isoformat() if payment_date else "none"
        event_id = f"{billing_subscription_id}:{event}:{pd}"

        existing = await self._event_repo.get_by_event_id(event_id)
        if existing is not None:
            return {"result": "duplicate", "event_id": event_id}

        local_sub = None
        if billing_subscription_id:
            local_sub = await self._sub_repo.get_by_billing_subscription_id(billing_subscription_id)

        event_model = BillingEventModel(
            event_id=event_id, event_type=event, idempotency_key=event_id,
            raw_payload=json.dumps(raw_payload, default=str), processing_status="received",
        )
        if local_sub is not None:
            event_model.subscription_id = local_sub.id
            event_model.owner_id = local_sub.owner_id
        await self._event_repo.save(event_model)

        status_map = {
            "PAYMENT_RECEIVED": "active",
            "PAYMENT_REFUNDED": None,          # não altera acesso automaticamente
            "SUBSCRIPTION_INACTIVATED": "canceled",
        }
        new_status = status_map.get(event, None)

        try:
            if local_sub is not None and new_status is not None:
                local_sub.status = new_status
                local_sub.last_event_at = datetime.utcnow()
                if subscription_expires_at and new_status == "active":
                    local_sub.expires_at = subscription_expires_at
                await self._sub_repo.save(local_sub)

                user = await self.user_repo.get_by_id(local_sub.owner_id)
                if user is not None:
                    if local_sub.plan_id:
                        user.plan_id = local_sub.plan_id
                    if subscription_expires_at and new_status == "active":
                        user.plan_expiration = subscription_expires_at
                        user.is_active = True
                    await self.user_repo.save(user)

            event_model.processing_status = "processed"
            event_model.processed_at = datetime.utcnow()
        except Exception as exc:
            event_model.processing_status = "failed"
            event_model.processing_error = str(exc)[:500]
            logger.error(f"[webhook] Falha recorrente event_id={event_id}: {exc}")
        await self._event_repo.save(event_model)

        return {"result": event_model.processing_status, "event_id": event_id, "event": event}
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `python -m pytest tests/unit/test_internal_webhook_recurring.py -v`
Expected: PASS (2 testes).

- [ ] **Step 5: Adaptar o router para o payload real**

Em `app/infra/web/routers/billing.py`, substitua o corpo de `receive_billing_webhook` (após a validação HMAC, que permanece) para parsear o payload real e chamar `process_recurring_event`:

```python
    # 3. Parsear payload real de assinatura do billing
    event = raw_body.get("event")
    billing_subscription_id = raw_body.get("subscription_id")
    if not event or not billing_subscription_id:
        raise HTTPException(status_code=422, detail="Payload de webhook de assinatura inválido.")

    def _parse_dt(v):
        if not v:
            return None
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    try:
        result = await service.process_recurring_event(
            event=event,
            billing_subscription_id=str(billing_subscription_id),
            subscription_expires_at=_parse_dt(raw_body.get("subscription_expires_at")),
            payment_date=_parse_dt(raw_body.get("payment_date")),
            raw_payload=raw_body,
        )
        metrics_registry.record_billing_webhook(result.get("event_id"), result.get("result"))
        return result
    except BusinessRuleException as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"[webhook] Erro recorrente: {exc}")
        return {"result": "error_persisted"}
```

Remova/ignore o parsing antigo via `BillingWebhookEventDTO` neste endpoint (o DTO permanece para compatibilidade de testes antigos, mas não é mais usado aqui). Garanta que `datetime` está importado no router.

- [ ] **Step 6: Rodar suíte de billing**

Run: `python -m pytest tests/unit/test_internal_webhook_recurring.py tests/unit/test_phase4_billing.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/application/services/subscription_service.py app/infra/web/routers/billing.py tests/unit/test_internal_webhook_recurring.py
git commit -m "feat(billing): webhook interno recorrente com payload real do billing"
```

---

## FASE 5 — E-mail (Mailgun)

### Task 13: `MailgunEmailGateway` + settings + template "fatura disponível"

**Files:**
- Create: `app/infra/integrations/__init__.py` (vazio)
- Create: `app/infra/integrations/mailgun.py`
- Modify: `app/infra/config/settings.py` (settings Mailgun)
- Test: `tests/unit/test_mailgun_gateway.py`

**Interfaces:**
- Produces: `MailgunEmailGateway(api_key, domain, from_email, from_name, api_base_url)` com
  `async send_invoice_available(*, to_email, to_name, amount, due_date, checkout_url) -> None`.
  Settings: `MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, `MAILGUN_FROM_EMAIL`, `MAILGUN_FROM_NAME`, `MAILGUN_API_BASE_URL`.

- [ ] **Step 1: Adicionar settings Mailgun**

Em `app/infra/config/settings.py`, junto às demais settings:
```python
    MAILGUN_API_KEY: str = ""
    MAILGUN_DOMAIN: str = ""
    MAILGUN_FROM_EMAIL: str = "noreply@neectify.com"
    MAILGUN_FROM_NAME: str = "Marketfy"
    MAILGUN_API_BASE_URL: str = "https://api.mailgun.net"
```

- [ ] **Step 2: Escrever o teste que falha**

Crie `tests/unit/test_mailgun_gateway.py`:

```python
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.integrations.mailgun import MailgunEmailGateway, EmailDeliveryError


@pytest.mark.asyncio
async def test_send_invoice_available_posts_to_mailgun():
    gw = MailgunEmailGateway(api_key="k", domain="mg.x.com", from_email="n@x.com", from_name="Marketfy")

    class Resp:
        status_code = 200
        text = "ok"

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post.return_value = Resp()

    with patch("infra.integrations.mailgun.httpx.AsyncClient", return_value=mock_client):
        await gw.send_invoice_available(
            to_email="c@x.com", to_name="Cliente", amount="50.00",
            due_date="25/07/2026", checkout_url="https://pay/x",
        )
    mock_client.post.assert_awaited_once()
    _, kwargs = mock_client.post.call_args
    assert kwargs["auth"] == ("api", "k")
    assert "c@x.com" in kwargs["data"]["to"]


@pytest.mark.asyncio
async def test_send_raises_on_non_2xx():
    gw = MailgunEmailGateway(api_key="k", domain="mg.x.com", from_email="n@x.com", from_name="Marketfy")

    class Resp:
        status_code = 500
        text = "err"

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post.return_value = Resp()

    with patch("infra.integrations.mailgun.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(EmailDeliveryError):
            await gw.send_invoice_available(
                to_email="c@x.com", to_name="Cliente", amount="50.00",
                due_date="25/07/2026", checkout_url="https://pay/x",
            )
```

- [ ] **Step 3: Rodar o teste e ver falhar**

Run: `python -m pytest tests/unit/test_mailgun_gateway.py -v`
Expected: FAIL (módulo inexistente).

- [ ] **Step 4: Implementar o gateway**

Crie `app/infra/integrations/__init__.py` (vazio) e `app/infra/integrations/mailgun.py`:

```python
"""Adapter Mailgun (API v3) via httpx — portado do padrão Neectify Food."""

from __future__ import annotations

import httpx

from infra.config.logger import get_logger

logger = get_logger("mailgun")


class EmailDeliveryError(Exception):
    pass


class MailgunEmailGateway:
    def __init__(self, api_key: str, domain: str, from_email: str, from_name: str,
                 api_base_url: str = "https://api.mailgun.net") -> None:
        if not api_key:
            raise ValueError("MAILGUN_API_KEY não configurado.")
        if not domain:
            raise ValueError("MAILGUN_DOMAIN não configurado.")
        self._api_key = api_key
        self._domain = domain
        self._from_email = from_email
        self._from_name = from_name
        self._send_url = f"{api_base_url.rstrip('/')}/v3/{domain}/messages"

    async def send_invoice_available(self, *, to_email: str, to_name: str, amount: str,
                                     due_date: str, checkout_url: str) -> None:
        data = {
            "from": f"{self._from_name} <{self._from_email}>",
            "to": f"{to_name} <{to_email}>",
            "subject": "Você tem uma fatura disponível para pagamento — Marketfy",
            "html": _build_invoice_html(to_name, amount, due_date, checkout_url),
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(self._send_url, data=data, auth=("api", self._api_key))
        if resp.status_code not in (200, 202):
            logger.error("mailgun_delivery_failed status=%s body=%s", resp.status_code, resp.text[:300])
            raise EmailDeliveryError(f"Mailgun retornou status {resp.status_code}.")


def _build_invoice_html(name: str, amount: str, due_date: str, checkout_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:system-ui,-apple-system,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:40px 16px;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);">
        <tr><td style="background:#18181b;padding:24px 32px;">
          <span style="color:#f97316;font-size:22px;font-weight:800;">Marketfy</span>
        </td></tr>
        <tr><td style="padding:36px 32px 28px;">
          <h1 style="margin:0 0 8px;font-size:20px;font-weight:700;color:#18181b;">Fatura disponível</h1>
          <p style="margin:0 0 24px;font-size:15px;color:#52525b;line-height:1.65;">
            Olá, {name}.<br>
            Sua fatura de assinatura no valor de <strong>R$ {amount}</strong> está disponível.
            Vencimento em <strong>{due_date}</strong>. Pague para manter seu acesso ativo.
          </p>
          <a href="{checkout_url}" style="display:inline-block;background:#f97316;color:#ffffff;text-decoration:none;font-size:15px;font-weight:700;padding:14px 32px;border-radius:10px;">
            Pagar fatura
          </a>
        </td></tr>
        <tr><td style="border-top:1px solid #f4f4f5;padding:16px 32px;">
          <p style="margin:0;font-size:11px;color:#a1a1aa;text-align:center;">© Marketfy · E-mail automático, não responda.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
```

- [ ] **Step 5: Rodar o teste e ver passar**

Run: `python -m pytest tests/unit/test_mailgun_gateway.py -v`
Expected: PASS (2 testes).

- [ ] **Step 6: Commit**

```bash
git add app/infra/integrations/__init__.py app/infra/integrations/mailgun.py app/infra/config/settings.py tests/unit/test_mailgun_gateway.py
git commit -m "feat(email): MailgunEmailGateway + template de fatura disponível"
```

---

### Task 14: Ligar o e-mail no `generate_due_invoices`

**Files:**
- Modify: `app/application/jobs/billing_jobs.py` (`_build_email_gateway`)
- Test: `tests/unit/test_build_email_gateway.py`

**Interfaces:**
- Consumes: `MailgunEmailGateway` (Task 13), settings Mailgun.
- Produces: `_build_email_gateway(settings)` retorna `MailgunEmailGateway` quando `MAILGUN_API_KEY` e `MAILGUN_DOMAIN` estão setados; `None` caso contrário.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/unit/test_build_email_gateway.py`:

```python
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.jobs.billing_jobs import _build_email_gateway


@dataclass
class S:
    MAILGUN_API_KEY: str = ""
    MAILGUN_DOMAIN: str = ""
    MAILGUN_FROM_EMAIL: str = "n@x.com"
    MAILGUN_FROM_NAME: str = "Marketfy"
    MAILGUN_API_BASE_URL: str = "https://api.mailgun.net"


def test_returns_none_without_config():
    assert _build_email_gateway(S()) is None


def test_returns_gateway_with_config():
    gw = _build_email_gateway(S(MAILGUN_API_KEY="k", MAILGUN_DOMAIN="mg.x.com"))
    assert gw is not None
    assert hasattr(gw, "send_invoice_available")
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `python -m pytest tests/unit/test_build_email_gateway.py -v`
Expected: FAIL (retorna sempre None — placeholder da Task 9).

- [ ] **Step 3: Implementar `_build_email_gateway`**

Em `app/application/jobs/billing_jobs.py`, substitua a função placeholder:
```python
def _build_email_gateway(settings):
    """Constrói o gateway Mailgun se configurado; senão None (e-mail desligado)."""
    if not getattr(settings, "MAILGUN_API_KEY", "") or not getattr(settings, "MAILGUN_DOMAIN", ""):
        return None
    from infra.integrations.mailgun import MailgunEmailGateway
    return MailgunEmailGateway(
        api_key=settings.MAILGUN_API_KEY,
        domain=settings.MAILGUN_DOMAIN,
        from_email=settings.MAILGUN_FROM_EMAIL,
        from_name=settings.MAILGUN_FROM_NAME,
        api_base_url=settings.MAILGUN_API_BASE_URL,
    )
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `python -m pytest tests/unit/test_build_email_gateway.py tests/unit/test_billing_jobs_generate.py -v`
Expected: PASS.

- [ ] **Step 5: Documentar as variáveis no `.env.example` (se existir)**

Run: `ls .env.example 2>/dev/null && echo exists || echo "sem .env.example"`.
Se existir, adicione:
```
MAILGUN_API_KEY=
MAILGUN_DOMAIN=
MAILGUN_FROM_EMAIL=noreply@neectify.com
MAILGUN_FROM_NAME=Marketfy
MAILGUN_API_BASE_URL=https://api.mailgun.net
BILLING_CORE_WEBHOOK_INVOICE_URL=
```

- [ ] **Step 6: Commit**

```bash
git add app/application/jobs/billing_jobs.py tests/unit/test_build_email_gateway.py
git commit -m "feat(email): ligar Mailgun no job de geração de faturas"
```

- [ ] **Step 7: Rodar a suíte completa de billing/invoices**

Run:
```bash
python -m pytest tests/unit/test_plan_access_grace.py tests/unit/test_billing_invoice_repo.py tests/unit/test_invoice_service.py tests/unit/test_subscribe_endpoint.py tests/unit/test_billing_invoice_webhook.py tests/unit/test_invoice_list_endpoint.py tests/unit/test_billing_jobs_generate.py tests/unit/test_billing_jobs_reconcile.py tests/unit/test_recurring_service.py tests/unit/test_internal_webhook_recurring.py tests/unit/test_mailgun_gateway.py tests/unit/test_build_email_gateway.py tests/unit/test_phase4_billing.py -v
```
Expected: todos PASS. Corrigir qualquer regressão antes de seguir ao frontend.

---

## FASE 6 — Frontend (worktree `frontend/.worktrees/subscription-flow`)

> Trabalhar no worktree do frontend. Rodar `npm install` uma vez. Testes: `npm test` (Vitest).

### Task 15: Helpers de API de assinatura/faturas

**Files:**
- Modify: `src/lib/api.js` (novos helpers)
- Test: `src/test/billingApi.test.js`

**Interfaces:**
- Produces: `subscribePlan({plan_id, subscription_type, billing_mode, document})`, `getInvoices()`, `getInvoice(invoiceId)`, `getSubscription()`.

- [ ] **Step 1: Escrever o teste que falha**

Crie `src/test/billingApi.test.js`:

```javascript
import { describe, it, expect, vi } from 'vitest';
import * as apiModule from '../lib/api';

describe('billing api helpers', () => {
  it('exports subscribe and invoice helpers', () => {
    expect(typeof apiModule.subscribePlan).toBe('function');
    expect(typeof apiModule.getInvoices).toBe('function');
    expect(typeof apiModule.getInvoice).toBe('function');
    expect(typeof apiModule.getSubscription).toBe('function');
  });
});
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `npm test -- src/test/billingApi.test.js`
Expected: FAIL (helpers inexistentes).

- [ ] **Step 3: Implementar os helpers**

Em `src/lib/api.js`, junto aos demais helpers nomeados (antes do `export default api`):
```javascript
export const getSubscription = () => api.get('/billing/subscription');

export const subscribePlan = ({ plan_id, subscription_type, billing_mode, document, idempotency_key }) =>
  api.post('/billing/subscribe', { plan_id, subscription_type, billing_mode, document, idempotency_key });

export const getInvoices = () => api.get('/billing/invoices');

export const getInvoice = (invoiceId) => api.get(`/billing/invoices/${invoiceId}`);
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `npm test -- src/test/billingApi.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/api.js src/test/billingApi.test.js
git commit -m "feat(billing): helpers de API de assinatura e faturas"
```

---

### Task 16: Reformular `/plans` (plano → modo → documento → checkout)

**Files:**
- Modify: `src/pages/auth/Plans.jsx`

**Interfaces:**
- Consumes: `subscribePlan` (Task 15), `getDurationKey` (já existe local), `useAuth`.
- Produces: fluxo que, ao contratar, redireciona `window.location.href = checkout_url`.

- [ ] **Step 1: Substituir o modal de "interesse" por seleção de modo + documento**

Em `src/pages/auth/Plans.jsx`, substitua o handler `onSubmitInterest` e o conteúdo do modal. Adicione estado no topo do componente (após os `useState` existentes):
```javascript
  const [billingMode, setBillingMode] = useState('invoice'); // 'invoice' | 'recurring'
  const [document, setDocument] = useState('');
  const [submitting, setSubmitting] = useState(false);
```

Substitua a função `onSubmitInterest` por `handleContract`:
```javascript
  const handleContract = async (e) => {
    e?.preventDefault?.();
    if (billingMode === 'recurring' && document.replace(/\D/g, '').length < 11) {
      toast.error('Informe um CPF ou CNPJ válido para cobrança recorrente.');
      return;
    }
    try {
      setSubmitting(true);
      const { data } = await subscribePlan({
        plan_id: selectedPlan.id,
        subscription_type: getDurationKey(selectedDuration),
        billing_mode: billingMode,
        document: billingMode === 'recurring' ? document : undefined,
      });
      if (data.checkout_url) {
        window.location.href = data.checkout_url;
        return;
      }
      toast.success('Assinatura iniciada. Acompanhe suas faturas em Configurações.');
      setShowModal(false);
      await refreshUser();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Erro ao iniciar assinatura. Tente novamente.');
    } finally {
      setSubmitting(false);
    }
  };
```

Adicione o import no topo:
```javascript
import { subscribePlan } from '../../lib/api';
```

Substitua o `<form onSubmit={handleSubmit(onSubmitInterest)} ...>` inteiro do modal por:
```jsx
                <form onSubmit={handleContract} className="space-y-4">
                    <div className="bg-blue-50 p-4 rounded-xl text-sm text-blue-800 border border-blue-100">
                        <p className="font-bold mb-1">Resumo do Pedido</p>
                        <p>Plano: <strong>{selectedPlan.name}</strong></p>
                        <p>Valor: <strong>{formatCurrency(getPrice(selectedPlan))}</strong> ({selectedDuration === 30 ? 'Mensal' : (selectedDuration === 180 ? 'Semestral' : 'Anual')})</p>
                    </div>

                    <div className="space-y-2">
                        <p className="text-sm font-bold text-gray-700">Forma de cobrança</p>
                        <div className="grid grid-cols-2 gap-2">
                            <button type="button" onClick={() => setBillingMode('invoice')}
                                className={`p-3 rounded-xl border-2 text-left text-sm font-bold ${billingMode === 'invoice' ? 'border-brand-yellow bg-yellow-50' : 'border-gray-200'}`}>
                                Por pagamento
                                <span className="block text-xs font-normal text-gray-500">Fatura por período</span>
                            </button>
                            <button type="button" onClick={() => setBillingMode('recurring')}
                                className={`p-3 rounded-xl border-2 text-left text-sm font-bold ${billingMode === 'recurring' ? 'border-brand-yellow bg-yellow-50' : 'border-gray-200'}`}>
                                Cobrança recorrente
                                <span className="block text-xs font-normal text-gray-500">Cartão automático</span>
                            </button>
                        </div>
                    </div>

                    {billingMode === 'recurring' && (
                        <Input label="CPF ou CNPJ" placeholder="Somente números" value={document}
                               onChange={(e) => setDocument(e.target.value)} autoFocus />
                    )}

                    <div className="pt-4">
                        <Button type="submit" variant="primary" size="lg" className="w-full font-bold" isLoading={submitting}>
                            Ir para o pagamento <ArrowRight size={20} />
                        </Button>
                    </div>
                </form>
```

Remova o uso agora obsoleto de `react-hook-form` neste componente se não for mais usado (o `register`/`handleSubmit`/`reset`), e a linha do `localStorage.setItem('marketfy_plan_interest', ...)`.

Atualize também o `<h1>` e subtítulo quando o usuário chega por expiração: mantenha o título principal, mas se `user?.plan_expiration` estiver no passado, exiba acima do grid um aviso:
```jsx
        {user?.plan_expiration && new Date(user.plan_expiration) < new Date() && (
            <div className="max-w-2xl mx-auto mb-8 bg-red-50 border border-red-200 text-red-700 rounded-xl p-4 text-center font-bold">
                Seu uso expirou. Contrate um plano para continuar usando o Marketfy.
            </div>
        )}
```

- [ ] **Step 2: Verificar build e lint**

Run:
```bash
npm run build
```
Expected: build sem erros de sintaxe/import.

- [ ] **Step 3: Commit**

```bash
git add src/pages/auth/Plans.jsx
git commit -m "feat(billing): contratação com modo (faturas/recorrente) e redirect ao checkout"
```

---

### Task 17: Aba de Faturas em Configurações

**Files:**
- Create: `src/pages/dashboard/BillingInvoices.jsx`
- Modify: `src/pages/dashboard/Settings.jsx` (adicionar aba `invoices`)

**Interfaces:**
- Consumes: `getInvoices`, `getInvoice`, `useAuth().subscription`.
- Produces: componente `BillingInvoices` listando faturas com botão "Pagar" (abre `checkout_url`).

- [ ] **Step 1: Criar o componente de faturas**

Crie `src/pages/dashboard/BillingInvoices.jsx`:

```jsx
import { useEffect, useState } from 'react';
import { getInvoices, getInvoice } from '../../lib/api';
import { Button } from '../../components/ui/Button';
import { Loader2, FileText } from 'lucide-react';
import toast from 'react-hot-toast';
import { formatCurrency } from '../../lib/utils';

const STATUS_LABEL = {
  pending: { text: 'Pendente', cls: 'bg-yellow-100 text-yellow-800' },
  paid: { text: 'Paga', cls: 'bg-green-100 text-green-800' },
  overdue: { text: 'Vencida', cls: 'bg-red-100 text-red-800' },
  canceled: { text: 'Cancelada', cls: 'bg-gray-100 text-gray-600' },
};

export default function BillingInvoices() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [paying, setPaying] = useState(null);

  const load = async () => {
    try {
      const { data } = await getInvoices();
      setItems(data.items || []);
    } catch {
      toast.error('Erro ao carregar faturas.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handlePay = async (invoice) => {
    try {
      setPaying(invoice.invoice_id);
      let url = invoice.checkout_url;
      if (!url) {
        const { data } = await getInvoice(invoice.invoice_id);
        url = data.checkout_url;
      }
      if (url) { window.location.href = url; return; }
      toast.error('Link de pagamento ainda não disponível. Tente novamente em instantes.');
    } catch {
      toast.error('Erro ao abrir o pagamento.');
    } finally {
      setPaying(null);
    }
  };

  if (loading) return <div className="flex justify-center py-12"><Loader2 className="animate-spin text-brand-yellow" size={36} /></div>;

  if (!items.length) {
    return (
      <div className="text-center py-12 text-gray-500">
        <FileText size={40} className="mx-auto mb-3 opacity-40" />
        Nenhuma fatura encontrada.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <h2 className="text-xl font-black text-gray-900 mb-4">Minhas Faturas</h2>
      {items.map((inv) => {
        const badge = STATUS_LABEL[inv.status] || STATUS_LABEL.pending;
        return (
          <div key={inv.invoice_id} className="flex items-center justify-between bg-white border border-gray-200 rounded-xl p-4">
            <div>
              <p className="font-bold text-gray-900">{formatCurrency(Number(inv.amount))}</p>
              <p className="text-xs text-gray-500">
                Vencimento: {inv.due_date ? new Date(inv.due_date).toLocaleDateString('pt-BR') : '—'}
              </p>
              <span className={`inline-block mt-1 text-[11px] font-bold px-2 py-0.5 rounded-full ${badge.cls}`}>{badge.text}</span>
            </div>
            {inv.status === 'pending' && (
              <Button onClick={() => handlePay(inv)} isLoading={paying === inv.invoice_id} className="font-bold">
                Pagar
              </Button>
            )}
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: Adicionar a aba em Settings**

Em `src/pages/dashboard/Settings.jsx`:

Import no topo:
```javascript
import BillingInvoices from './BillingInvoices';
import { useAuth } from '../../context/AuthContext';
```
(Se `useAuth` já estiver importado, não duplicar.)

Dentro do componente, obtenha `subscription`:
```javascript
  const { subscription } = useAuth();
```

Após o `<button>` da aba `fiscal` (linha ~103-110), adicione o botão da aba de faturas — visível apenas para modo invoice:
```jsx
          {subscription?.billing_mode === 'invoice' && (
            <button
              onClick={() => setActiveTab('invoices')}
              className={`flex items-center gap-3 px-4 py-3 rounded-xl font-bold transition-all text-left ${activeTab === 'invoices' ? 'bg-white text-brand-dark shadow-sm border border-gray-100' : 'text-gray-500 hover:bg-gray-100'}`}
            >
              Faturas
            </button>
          )}
```

Após o bloco `{activeTab === 'fiscal' && (...)}`, adicione:
```jsx
          {activeTab === 'invoices' && (
            <div className="animate-fade-in">
              <BillingInvoices />
            </div>
          )}
```

- [ ] **Step 3: Verificar build**

Run: `npm run build`
Expected: sem erros.

- [ ] **Step 4: Commit**

```bash
git add src/pages/dashboard/BillingInvoices.jsx src/pages/dashboard/Settings.jsx
git commit -m "feat(billing): aba de faturas em Configurações"
```

---

### Task 18: Roteamento de bloqueio + banner de fatura

**Files:**
- Modify: `src/components/layout/AdminLayout.jsx`

**Interfaces:**
- Consumes: `useAuth().subscription` (`locked`, `invoice_pending`, `pending_invoice`, `billing_mode`).
- Produces: expirado com fatura pendente (modo invoice) vai para `/dashboard/settings` (aba Faturas) em vez de `/plans`; banner de fatura pendente/vencida no topo.

- [ ] **Step 1: Ajustar o redirecionamento de expirado**

Em `src/components/layout/AdminLayout.jsx`, no `useEffect` que redireciona (linhas ~69-83), substitua a lógica por:
```javascript
  useEffect(() => {
    if (!authLoading && user) {
      const isPlansPage = location.pathname.includes('/plans');
      const isSettings = location.pathname.includes('/dashboard/settings');
      const isSupport = location.pathname.includes('/dashboard/support');
      const isAllowedExpired = isPlansPage || isSettings || isSupport;

      const invoiceMode = subscription?.billing_mode === 'invoice';
      const hasPendingInvoice = subscription?.invoice_pending;

      if (hasNoPlan && !isPlansPage) {
        navigate('/plans');
        return;
      }
      if (isExpired && !isAllowedExpired) {
        // Modo faturas com fatura pendente: manda para Configurações (aba Faturas), não prende em /plans
        if (invoiceMode && hasPendingInvoice) {
          navigate('/dashboard/settings');
        } else {
          navigate('/plans');
        }
      }
    }
  }, [user, authLoading, hasNoPlan, isExpired, location.pathname, navigate, subscription]);
```
Adicione `subscription` ao destructuring de `useAuth()` no componente (localize a linha `const { ... } = useAuth();` e inclua `subscription`).

- [ ] **Step 2: Adicionar o banner de fatura**

No JSX do layout, logo abaixo do header/topo do conteúdo (procure o container principal do `<Outlet />`), adicione:
```jsx
      {subscription?.invoice_pending && (
        <div className="bg-amber-50 border-b border-amber-200 px-4 py-2 text-sm text-amber-800 flex items-center justify-between">
          <span className="font-bold">
            Você tem uma fatura disponível para pagamento
            {subscription?.pending_invoice?.due_date
              ? ` (vence em ${new Date(subscription.pending_invoice.due_date).toLocaleDateString('pt-BR')})`
              : ''}.
          </span>
          <Link to="/dashboard/settings" className="underline font-bold">Ver faturas</Link>
        </div>
      )}
```
Garanta que `Link` está importado de `react-router-dom` (já está).

- [ ] **Step 3: Verificar build**

Run: `npm run build`
Expected: sem erros.

- [ ] **Step 4: Commit**

```bash
git add src/components/layout/AdminLayout.jsx
git commit -m "feat(billing): banner de fatura e roteamento de expirado para faturas"
```

---

## Verificação final (ponta a ponta)

- [ ] **Backend:** rodar a suíte inteira de unit tests: `python -m pytest tests/unit -q`. Corrigir regressões.
- [ ] **Frontend:** `npm run build` e `npm test` sem erros.
- [ ] **Fluxo faturas (manual, billing mockado `BILLING_CORE_ENABLED=false`):** contratar plano modo faturas → verificar subscription `pending` + fatura `pending` + checkout_url mock; simular webhook `CHECKOUT_PAID` em `/api/v1/webhooks/billing-invoices` (com assinatura HMAC) → subscription `active`, `expires_at` setado, fatura `paid`.
- [ ] **Fluxo recorrente (mock):** contratar modo recorrente com documento → customer criado + subscription `pending` + checkout_url; simular `PAYMENT_RECEIVED` em `/api/v1/billing/webhooks/internal` → `active`.
- [ ] **Bloqueio:** forçar `expires_at` no passado (> 3 dias) → `GET /billing/subscription` retorna `locked=true`; frontend mantém acesso só a Faturas/Config/Suporte.
- [ ] **Grace:** `expires_at` há 1 dia → `past_due`, `locked=false`, banner de fatura.
- [ ] Usar a skill `superpowers:verification-before-completion` antes de declarar concluído.

## Notas de integração (validar contra billing real)

- O resultado do job de `POST /v1/subscriptions` deve conter `checkout_url` e `subscription_id`. Se o billing real entregar esses dados apenas via webhook interno (e não no job), ajustar `RecurringService._poll_subscription_job` para tolerar ausência e depender do webhook `PAYMENT_RECEIVED` para ativar (o `expires_at` já vem no evento).
- Registrar no billing: host do `BILLING_CORE_WEBHOOK_INVOICE_URL` em `ALLOWED_INTERNAL_WEBHOOK_HOSTS` e os hosts das URLs de retorno em `ALLOWED_CHECKOUT_REDIRECT_HOSTS`.
- Cliente interno do billing precisa dos scopes: `payments:create/read`, `jobs:read`, `customers:create`, `subscriptions:create` (+ `subscriptions:cancel` se implementar cancelamento).
