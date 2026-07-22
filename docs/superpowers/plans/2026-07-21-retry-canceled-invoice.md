# Retry de Fatura Cancelada Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que uma fatura de contratação cancelada seja substituída, sob demanda, por nova assinatura e fatura pendentes sem gerar checkout antes de o usuário clicar em Pagar.

**Architecture:** O `InvoiceService` cria uma nova assinatura e uma nova fatura a partir do plano e do período da assinatura histórica, usando chaves de idempotência derivadas da fatura original. A rota HTTP expõe o retry apenas ao dono da fatura, e o frontend mostra a ação exclusivamente para faturas canceladas; a criação de checkout continua no endpoint lazy já existente.

**Tech Stack:** FastAPI, SQLAlchemy async, pytest, React, Axios, Vitest e Testing Library.

## Global Constraints

- Preservar assinatura e fatura históricas; nunca excluir linhas de billing.
- Aceitar retry somente para fatura `canceled` e nunca para `paid`.
- Criar a nova fatura com `status="pending"` e sem `bc_job_id`, `bc_payment_id` ou `checkout_url`.
- Manter checkout lazy: somente `POST /billing/invoices/{id}/checkout` pode chamar o Billing Core.
- Usar TDD: cada mudança de produção deve ser precedida por teste falhando observado.

---

## File Structure

- `app/application/services/invoice_service.py`: regra de domínio que preserva a contratação antiga e cria a substituta.
- `app/infra/web/routers/billing.py`: endpoint autenticado de retry e resposta serializável.
- `tests/unit/test_invoice_service.py`: stubs e testes do comportamento de retry sem checkout.
- `tests/unit/test_invoice_list_endpoint.py`: contrato de registro do endpoint.
- `marketfy/frontend/src/lib/api.js`: helper HTTP para o retry.
- `marketfy/frontend/src/pages/dashboard/BillingInvoices.jsx`: botão e estado de retry para fatura cancelada.
- `marketfy/frontend/src/test/billingApi.test.js` e `marketfy/frontend/src/test/billingInvoices.test.jsx`: regressões da interface.

## Task 1: Serviço de substituição de fatura cancelada

**Files:**
- Modify: `app/application/services/invoice_service.py:33-228`
- Modify: `tests/unit/test_invoice_service.py:35-190`

**Interfaces:**
- Consumes: `BillingInvoiceModel`, `BillingSubscriptionModel`, `SQLAlchemyBillingInvoiceRepository.create`, `SQLAlchemyBillingSubscriptionRepository.save`.
- Produces: `InvoiceService.retry_canceled_invoice(invoice_id: UUID) -> dict[str, str | None]` com `subscription_id`, `invoice_id`, `job_id=None` e `checkout_url=None`.

- [ ] **Step 1: Write the failing service test**

```python
@pytest.mark.asyncio
async def test_retry_canceled_invoice_creates_new_pending_subscription_and_invoice_without_checkout():
    plan = StubPlan()
    old_sub = StubSub(plan_id=plan.id, status="canceled", subscription_type="monthly")
    repo = InvoiceRepo()
    old = await repo.create(
        owner_id=old_sub.owner_id, subscription_id=old_sub.id, plan_id=old_sub.plan_id,
        period_start=datetime.utcnow(), period_end=datetime.utcnow(), due_date=datetime.utcnow(),
        amount=Decimal("50.00"), status="canceled", idempotency_key="old",
    )
    service = InvoiceService(repo, SubRepo(old_sub), PlanRepo(plan), AsyncMock(), StubSettings())

    result = await service.retry_canceled_invoice(old.id)

    new_invoice = repo.items[uuid.UUID(result["invoice_id"])]
    assert new_invoice.id != old.id
    assert new_invoice.status == "pending"
    assert new_invoice.checkout_url is None
    assert new_invoice.subscription_id != old.subscription_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_invoice_service.py::test_retry_canceled_invoice_creates_new_pending_subscription_and_invoice_without_checkout -q`

Expected: FAIL with `AttributeError: 'InvoiceService' object has no attribute 'retry_canceled_invoice'`.

- [ ] **Step 3: Extend the test stubs for deterministic retry idempotency**

```python
class InvoiceRepo:
    async def get_by_idempotency_key(self, key):
        return next((item for item in self.items.values() if item.idempotency_key == key), None)

class SubRepo:
    def __init__(self, sub):
        self._sub = sub
        self.by_idempotency_key = {}

    async def get_by_idempotency_key(self, key):
        return self.by_idempotency_key.get(key)

    async def save(self, sub):
        if sub.id is None:
            sub.id = uuid.uuid4()
        self.saved.append(sub)
        if getattr(sub, "idempotency_key", None):
            self.by_idempotency_key[sub.idempotency_key] = sub
        return sub
```

- [ ] **Step 4: Implement the minimal retry method**

```python
async def retry_canceled_invoice(self, invoice_id: uuid.UUID) -> Dict[str, Any]:
    original = await self._inv.get_by_id(invoice_id)
    if original is None or original.status != "canceled":
        raise ValueError("Somente faturas canceladas podem ser tentadas novamente.")

    retry_key = f"invoice-retry:{original.id}"
    existing_subscription = await self._sub.get_by_idempotency_key(retry_key)
    if existing_subscription is not None:
        existing_invoice = await self._inv.get_by_idempotency_key(retry_key)
        return {"subscription_id": str(existing_subscription.id), "invoice_id": str(existing_invoice.id), "job_id": None, "checkout_url": None}

    original_subscription = await self._sub.get_by_id(original.subscription_id)
    if original_subscription is None:
        raise ValueError("Assinatura da fatura não encontrada.")
    if await self._inv.get_open_invoice_for_subscription(original.subscription_id):
        raise ValueError("A assinatura já possui uma fatura pendente.")
    plan = await self._plan.get_by_id(original.plan_id)
    if plan is None or not plan.is_active:
        raise ValueError("Plano não disponível.")

    original_subscription.status = "canceled"
    original_subscription.last_event_at = datetime.utcnow()
    await self._sub.save(original_subscription)
    from infra.database.models import BillingSubscriptionModel
    replacement = BillingSubscriptionModel(
        owner_id=original.owner_id, plan_id=original.plan_id,
        billing_system=self._settings.BILLING_CORE_SYSTEM,
        billing_system_sub_id=str(original.owner_id), billing_mode="invoice",
        status="pending", subscription_type=original_subscription.subscription_type,
        value=price_for_period(plan, original_subscription.subscription_type), idempotency_key=retry_key,
    )
    replacement = await self._sub.save(replacement)
    now = datetime.utcnow()
    invoice = await self._create_invoice(
        owner_id=original.owner_id, subscription=replacement, plan=plan,
        period_start=now, due_date=now, idempotency_key=retry_key,
    )
    return {"subscription_id": str(replacement.id), "invoice_id": str(invoice.id), "job_id": None, "checkout_url": None}
```

- [ ] **Step 5: Run service tests to verify they pass**

Run: `python -m pytest tests/unit/test_invoice_service.py -q`

Expected: PASS including the new retry test and no Billing Core `create_payment` call in it.

- [ ] **Step 6: Add idempotency regression test**

```python
@pytest.mark.asyncio
async def test_retry_canceled_invoice_returns_same_replacement_for_repeat_request():
    first = await service.retry_canceled_invoice(old_invoice.id)
    second = await service.retry_canceled_invoice(old_invoice.id)
    assert second == first
    assert len([item for item in repo.items.values() if item.status == "pending"]) == 1
```

- [ ] **Step 7: Run the idempotency regression**

Run: `python -m pytest tests/unit/test_invoice_service.py::test_retry_canceled_invoice_returns_same_replacement_for_repeat_request -q`

Expected: PASS.

- [ ] **Step 8: Commit the service slice**

```bash
git add app/application/services/invoice_service.py tests/unit/test_invoice_service.py
git commit -m "feat: retry canceled invoice subscriptions"
```

## Task 2: Endpoint autenticado de retry

**Files:**
- Modify: `app/infra/web/routers/billing.py:190-218`
- Modify: `tests/unit/test_invoice_list_endpoint.py:12-20`

**Interfaces:**
- Consumes: `InvoiceService.retry_canceled_invoice(invoice_id)` from Task 1.
- Produces: `POST /billing/invoices/{invoice_id}/retry` returning `{subscription_id, invoice_id, job_id, checkout_url}` with HTTP 202.

- [ ] **Step 1: Write the failing route-registration test**

```python
def test_invoice_endpoints_registered():
    paths = {route.path for route in billing_router.router.routes}
    assert "/invoices/{invoice_id}/retry" in paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_invoice_list_endpoint.py -q`

Expected: FAIL because `/invoices/{invoice_id}/retry` is absent.

- [ ] **Step 3: Add the owner-scoped retry route before the generic GET route**

```python
@router.post("/invoices/{invoice_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_invoice(
    invoice_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    invoice_service=Depends(_get_invoice_service),
):
    repo = SQLAlchemyBillingInvoiceRepository(db)
    invoice = await repo.get_by_id(invoice_id)
    if invoice is None or invoice.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Fatura não encontrada.")
    try:
        result = await invoice_service.retry_canceled_invoice(invoice_id)
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
```

- [ ] **Step 4: Run route tests to verify they pass**

Run: `python -m pytest tests/unit/test_invoice_list_endpoint.py tests/unit/test_invoice_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the HTTP slice**

```bash
git add app/infra/web/routers/billing.py tests/unit/test_invoice_list_endpoint.py
git commit -m "feat: expose canceled invoice retry"
```

## Task 3: Ação de retry na interface

**Files:**
- Modify: `C:/Users/reali/Documents/Neectify/marketfy/frontend/src/lib/api.js`
- Modify: `C:/Users/reali/Documents/Neectify/marketfy/frontend/src/pages/dashboard/BillingInvoices.jsx`
- Modify: `C:/Users/reali/Documents/Neectify/marketfy/frontend/src/test/billingApi.test.js`
- Modify: `C:/Users/reali/Documents/Neectify/marketfy/frontend/src/test/billingInvoices.test.jsx`

**Interfaces:**
- Consumes: `POST /billing/invoices/{invoice_id}/retry` from Task 2.
- Produces: `retryInvoice(invoiceId)` and the **Tentar novamente** action for `canceled` invoices.

- [ ] **Step 1: Write failing API-helper assertion**

```javascript
expect(typeof apiModule.retryInvoice).toBe('function');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --run src/test/billingApi.test.js`

Expected: FAIL because `retryInvoice` is undefined.

- [ ] **Step 3: Add the API helper**

```javascript
export const retryInvoice = (invoiceId) =>
  api.post(`/billing/invoices/${invoiceId}/retry`);
```

- [ ] **Step 4: Write failing component test for retry**

```jsx
it('creates a replacement invoice only after the user clicks retry', async () => {
  getInvoices.mockResolvedValue({ data: { items: [{ invoice_id: 'old-1', amount: '50.00', status: 'canceled' }] } });
  retryInvoice.mockResolvedValue({ data: { invoice_id: 'new-1', checkout_url: null } });
  render(<BillingInvoices />);
  await user.click(await screen.findByRole('button', { name: /tentar novamente/i }));
  expect(retryInvoice).toHaveBeenCalledWith('old-1');
  expect(requestInvoiceCheckout).not.toHaveBeenCalled();
});
```

- [ ] **Step 5: Run test to verify it fails**

Run: `npm test -- --run src/test/billingInvoices.test.jsx`

Expected: FAIL because a canceled invoice has no retry action.

- [ ] **Step 6: Implement retry state and button**

```jsx
const [retrying, setRetrying] = useState(null);
const handleRetry = async (invoice) => {
  try {
    setRetrying(invoice.invoice_id);
    await retryInvoice(invoice.invoice_id);
    toast.success('Nova fatura criada. Clique em Pagar para gerar o checkout.');
    await load();
  } catch {
    toast.error('Não foi possível criar uma nova fatura.');
  } finally {
    setRetrying(null);
  }
};
// render next to the pending branch
{inv.status === 'canceled' && (
  <Button onClick={() => handleRetry(inv)} isLoading={retrying === inv.invoice_id} variant="secondary">
    Tentar novamente
  </Button>
)}
```

- [ ] **Step 7: Run frontend tests to verify they pass**

Run: `npm test -- --run src/test/billingApi.test.js src/test/billingInvoices.test.jsx`

Expected: PASS; retry calls only `/retry` and does not call checkout creation.

- [ ] **Step 8: Build the frontend**

Run: `npm run build`

Expected: exit code 0.

- [ ] **Step 9: Commit the frontend slice**

```bash
git -C C:/Users/reali/Documents/Neectify/marketfy/frontend add src/lib/api.js src/pages/dashboard/BillingInvoices.jsx src/test/billingApi.test.js src/test/billingInvoices.test.jsx
git -C C:/Users/reali/Documents/Neectify/marketfy/frontend commit -m "feat: retry canceled invoices"
```

## Task 4: Final integration verification

**Files:**
- Verify only.

- [ ] **Step 1: Run backend regression suite**

Run: `python -m pytest tests/unit/test_invoice_service.py tests/unit/test_invoice_list_endpoint.py tests/unit/test_billing_invoice_webhook.py tests/unit/test_billing_jobs_reconcile.py tests/unit/test_billing_jobs_generate.py -q`

Expected: PASS.

- [ ] **Step 2: Run frontend regression suite and build**

Run: `npm test -- --run src/test/billingApi.test.js src/test/billingInvoices.test.jsx src/test/settingsFiscalRoute.test.jsx src/test/plans.test.jsx; npm run build`

Expected: all tests PASS and build exits 0.

- [ ] **Step 3: Check staged diff hygiene before integration**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; only source, tests and intentionally created documentation are staged or committed.
