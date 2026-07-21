# Product deletion and duplicate EAN warning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let managers safely remove accidental duplicate products and explicitly confirm duplicate EAN creation.

**Architecture:** Reuse the Marketfy backend's market-scoped soft-delete endpoint so historical records remain intact. Extend the Inventory frontend with two confirmation dialogs: one before DELETE and one before POST when its locally loaded product list has the same normalized barcode.

**Tech Stack:** FastAPI, Python/pytest, React, react-hook-form, Vitest, Axios, react-hot-toast.

## Global Constraints

- Work only on `main`; do not read, modify, stage, or commit PIX-branch files.
- Do not physically delete products, sales, stock movements, or fiscal history.
- Keep duplicate EAN creation allowed only after an explicit confirmation.
- Keep internal product-code uniqueness enforced by the backend.

---

### Task 1: Characterize the existing soft-delete API

**Files:**
- Test: `marketfy/backend/tests/unit/test_inventory_service.py`
- Modify only if test exposes a gap: `marketfy/backend/app/application/services/inventory_service.py`

**Consumes:** `InventoryService.delete_product(market_id, product_id)`.

**Produces:** A regression test proving deletion is market-scoped and calls `Product.mark_deleted()` through repository save.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_delete_product_soft_deletes_only_product_from_requested_market():
    product = Product(...)
    repo.get_by_id.return_value = product
    await InventoryService(repo, AsyncMock()).delete_product(product.market_id, product.id)
    assert product.active is False
    repo.save.assert_awaited_once_with(product)
```

- [ ] **Step 2: Run test to verify it fails or identifies missing coverage**

Run: `py -3.12 -m pytest tests/unit/test_inventory_service.py::test_delete_product_soft_deletes_only_product_from_requested_market -q`

- [ ] **Step 3: Keep or minimally repair service behavior**

```python
product.mark_deleted()
await self.product_repo.save(product)
return {"message": "Produto removido com sucesso."}
```

- [ ] **Step 4: Run the service test**

Run: `py -3.12 -m pytest tests/unit/test_inventory_service.py -q`

### Task 2: Add Inventory confirmation UI and duplicate-EAN warning

**Files:**
- Modify: `marketfy/frontend/src/pages/dashboard/Inventory.jsx`
- Test: `marketfy/frontend/src/test/inventoryProductManagement.test.jsx`

**Consumes:** `DELETE /inventory/{marketId}/products/{productId}` and the loaded `products` array.

**Produces:** `handleDeleteProduct(product)` and `handleCreateProduct(data)` confirmation flow.

- [ ] **Step 1: Write failing Vitest cases**

```jsx
it('asks before deleting, calls DELETE after confirmation, then reloads products', async () => {
  // select the delete control, confirm, expect api.delete called with product path
})

it('requires explicit confirmation before posting a normalized duplicate barcode', async () => {
  // existing 789123; submitted 789.123; no POST before confirm, one POST after
})
```

- [ ] **Step 2: Run the focused frontend test and verify RED**

Run: `npm test -- --run src/test/inventoryProductManagement.test.jsx`

- [ ] **Step 3: Implement minimal UI behavior**

```jsx
const normalizedBarcode = (value) => String(value || '').replace(/\D/g, '');
const duplicate = products.find((product) =>
  normalizedBarcode(product.barcode) === normalizedBarcode(data.barcode)
);
```

Use a confirmation modal for deletion and a separate continuation modal for duplicate EAN. Cancel closes the modal and retains the create form. Confirming calls `api.delete(...)` or `api.post(...)`, shows toast feedback, and reloads products.

- [ ] **Step 4: Run focused frontend test and verify GREEN**

Run: `npm test -- --run src/test/inventoryProductManagement.test.jsx`

### Task 3: Regression verification and commits

**Files:**
- Verify: backend inventory tests and frontend Inventory tests

- [ ] **Step 1: Run backend tests**

Run: `py -3.12 -m pytest tests/unit/test_inventory_service.py -q`

- [ ] **Step 2: Run frontend checks**

Run: `npm test -- --run src/test/inventoryProductManagement.test.jsx && npm run lint && npm run build`

- [ ] **Step 3: Check staging scope and commit separately per repository**

```powershell
git diff --check
git status --short
git add <only inventory feature files>
git commit -m "feat: manage duplicate inventory products"
```

Never stage `__pycache__` or any PIX-branch file.
