# 6. API and event contract

## Contract principles

- All new endpoints live under `/api/v1`.
- JSON requests and responses are validated and documented by OpenAPI.
- API tokens use `Authorization: Bearer <token>`; browser login/session behavior is documented separately.
- Mutations return the resulting resource, state revision, and emitted event IDs where useful.
- Validation and domain failures use one stable problem shape.
- Mutations that can be retried support an `Idempotency-Key` header or a stable source key.
- Updates use resource versions/ETags or explicit version fields to prevent blind overwrite.
- Lists support deterministic ordering and pagination where data can grow.

## Problem response

```json
{
  "type": "https://pantryos.local/problems/insufficient-inventory",
  "title": "Insufficient inventory",
  "status": 409,
  "code": "insufficient_inventory",
  "detail": "Requested 3 lb; 2 lb is usable.",
  "errors": [],
  "request_id": "..."
}
```

Do not leak stack traces, filesystem paths, SQL, tokens, or receipt contents in client errors.

## Minimum endpoints

### System and authentication

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health/live` | Process liveness |
| GET | `/api/v1/health/ready` | Database/migration readiness |
| GET | `/api/v1/instance` | Instance ID, API version, capabilities, state revision |
| POST | `/api/v1/auth/login` | Browser session where local login is implemented |
| POST | `/api/v1/auth/logout` | End browser session |

### Dashboard and events

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/dashboard` | Aggregated, bounded kitchen dashboard snapshot |
| GET | `/api/v1/events` | SSE or WebSocket upgrade endpoint |
| GET | `/api/v1/events/{id}` | Optional event detail/audit lookup |

The dashboard should contain revision, tonight, use-soon, quick meals, shopping summary, leftovers, values, and counts without returning unbounded full tables.

### Products and barcodes

- `GET/POST /api/v1/products`
- `GET/PATCH/DELETE /api/v1/products/{id}`
- `POST /api/v1/products/{id}/aliases`
- `DELETE /api/v1/products/{id}/aliases/{alias_id}`
- `GET /api/v1/barcodes/{code}`
- `POST /api/v1/barcodes/{code}/map`

Unknown barcode lookup returns a typed `not_mapped` result suitable for a creation form rather than a generic 404.

### Locations

- `GET/POST /api/v1/locations`
- `GET/PATCH/DELETE /api/v1/locations/{id}`
- `POST /api/v1/locations/{id}/move`
- `GET /api/v1/locations/{id}/inventory`
- `GET /api/v1/locations/{id}/value`

Deletion must reject or explicitly reassign active inventory and descendants.

### Inventory

- `GET/POST /api/v1/inventory/lots`
- `GET/PATCH /api/v1/inventory/lots/{id}`
- `POST /api/v1/inventory/lots/{id}/consume`
- `POST /api/v1/inventory/lots/{id}/move`
- `POST /api/v1/inventory/lots/{id}/open`
- `POST /api/v1/inventory/lots/{id}/adjust`
- `POST /api/v1/inventory/lots/{id}/discard`
- `POST /api/v1/inventory/consume-product`
- `GET /api/v1/inventory/events`

`consume-product` returns the FEFO allocation. Destructive operations require explicit quantities and reasons; delete is reserved for administrative correction and must not replace discard/waste history.

### Recipes and intelligence

- `GET/POST /api/v1/recipes`
- `GET/PATCH/DELETE /api/v1/recipes/{id}`
- `POST /api/v1/recipes/{id}/match`
- `GET /api/v1/recommendations/meals?max_missing=&max_minutes=&servings=`
- `GET /api/v1/recommendations/use-soon`

Match responses include scaled requirements, available quantities, missing quantities, unresolved ingredients, and suggested lot allocations.

### Meal plan and shopping

- `GET/POST /api/v1/meal-plan`
- `PATCH/DELETE /api/v1/meal-plan/{id}`
- `POST /api/v1/shopping/rebuild`
- `GET /api/v1/shopping`
- `POST /api/v1/shopping/manual`
- `PATCH/DELETE /api/v1/shopping/{id}`
- `POST /api/v1/shopping/{id}/check`
- `POST /api/v1/shopping/{id}/uncheck`
- `POST /api/v1/shopping/complete-purchase`

Rebuild is idempotent. It reports source changes and does not silently accept suggested minimum-stock demand that the user previously rejected.

### Cooking and leftovers

- `POST /api/v1/cooking/sessions`
- `GET/PATCH /api/v1/cooking/sessions/{id}`
- `POST /api/v1/cooking/sessions/{id}/complete`
- `POST /api/v1/cooking/sessions/{id}/cancel`
- `GET /api/v1/leftovers`

### Receipts, purchases, and prices

- `POST /api/v1/receipts` — bounded text upload or image upload with JSON `content_base64`
- `POST /api/v1/receipts/{id}/extract`
- `GET/PATCH /api/v1/receipts/{id}/review`
- `POST /api/v1/receipts/{id}/commit`
- `POST /api/v1/receipts/{id}/reject`
- `GET /api/v1/purchases`
- `GET /api/v1/purchases/{id}`
- `GET /api/v1/products/{id}/prices`

Receipt upload accepts `text/plain`, `text/csv`, `image/png`, and `image/jpeg`; image extraction is local OCR and must degrade with a validation error when the runtime lacks OCR support. Receipt commit is idempotent and transactional. It may create products, barcode mappings, purchase records, and inventory lots only after review.

### Operations

Backups may be CLI-only to reduce attack surface. At minimum provide:

- `pantryos backup --output ...`
- `pantryos restore --input ... --verify`
- `pantryos import-legacy --path ... --dry-run`
- `pantryos doctor`

## Event stream behavior

- Authenticate before stream upgrade.
- Send a current revision/hello message immediately.
- Include monotonic state revision and event ID.
- Support heartbeat and reconnect with `Last-Event-ID` where feasible.
- Bound retained events; clients must recover by fetching a snapshot if history is unavailable.
- Publish only after the database transaction commits.
- Home Assistant may use push as the primary update path and a periodic snapshot as recovery.

Example:

```json
{
  "id": "evt_...",
  "revision": 142,
  "type": "inventory.consumed",
  "occurred_at": "2026-08-25T22:31:00Z",
  "data": {
    "product_id": "...",
    "lot_ids": ["..."],
    "quantity": "3",
    "unit": "count"
  }
}
```

## Compatibility

Preserve Home Assistant action names where practical so existing automations can migrate. The legacy unversioned browser API may be removed after the bundled UI is migrated, but the release notes must document the change. Do not run two persistence implementations to preserve compatibility.
