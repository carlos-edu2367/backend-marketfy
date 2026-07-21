# Product deletion and duplicate EAN warning

## Goal

Let an inventory manager remove an accidentally duplicated product without
destroying sales history, and warn before a new product reuses an existing
barcode in the same market.

## Scope

- Keep the existing backend `DELETE /inventory/{market_id}/products/{product_id}`
  soft-delete endpoint.
- Add a confirmation action in the Marketfy inventory screen that calls it,
  refreshes the product list, and clears any fiscal selection for that product.
- Before product creation, normalize the entered barcode and compare it against
  the products already loaded for the selected market.
- If a match exists, display the matching product name and require an explicit
  confirmation to continue. Cancelling preserves the completed form.
- The internal product code remains server-enforced as unique. Barcode reuse is
  intentionally permitted after the user confirms.

## Safety and behavior

Deletion is a soft delete. It removes the product from active inventory and
offline sync while retaining past sales, inventory movements, fiscal evidence,
and auditability. No database migration or physical deletion is required.

The duplicate check is a fast client-side warning, not a uniqueness constraint:
it is scoped to the selected market and does not reject barcode-less products.
The server remains the authority for the internal code and normal creation
validation.

## Tests

- Frontend: duplicate EAN opens a confirmation; cancel does not post; confirm
  posts once. The delete confirmation calls DELETE and refreshes the list.
- Backend: preserve/extend the existing service and route behavior for a
  market-scoped soft delete.

## Non-goals

- No automatic merge of duplicate products.
- No rewrite of historical sales or fiscal snapshots.
- No changes to the PIX branch or its files.
