# 5. Domain model and invariants

The table names below are conceptual. Internal names may differ, but the boundaries and invariants are required.

## Product catalog

### Product

| Field | Notes |
|---|---|
| `id` | Stable UUID or equivalent |
| `name` | Display name |
| `normalized_name` | Indexed canonical matching key |
| `category` | Optional household category |
| `default_unit` | Preferred display/stock unit |
| `minimum_stock_quantity` / `minimum_stock_unit` | Product-level desired stock |
| `preferred_location_id` | Optional default location |
| `default_shelf_life_days` | Optional unopened default |
| `opened_shelf_life_days` | Optional opened default |
| `density_or_conversion_metadata` | Optional explicit product-specific conversion factors |
| `active` | Soft retirement without destroying history |
| timestamps / version | Auditing and optimistic updates |

### ProductAlias

- `product_id`
- `alias`
- `normalized_alias`
- source and confidence where imported
- unique normalized alias within the household

Aliases resolve recipe text such as “chicken breasts” to the same product without relying on fuzzy matching for every calculation.

### ProductBarcode

- unique barcode value and format
- `product_id`
- package quantity and package unit
- optional brand/size metadata
- source: manual, receipt, local catalog, external adapter

A scan identifies a product package; it is not itself an inventory lot.

## Locations

### Location

| Field | Notes |
|---|---|
| `id` | Stable ID |
| `parent_id` | Nullable self-reference |
| `name` | Name within parent |
| `type` | house, room, refrigerator, freezer, pantry, shelf, bin, cabinet, other |
| `temperature_entity_id` | Optional Home Assistant entity reference used only as metadata |
| `active` | Allows retirement/merge |

Rules:

- Sibling names are unique under one parent.
- Cycles are rejected.
- Moving/renaming a location does not rewrite inventory lots.
- Descendant queries use IDs/tree traversal, not string prefixes.

## Inventory

### InventoryLot

A lot represents a physical batch with common acquisition, expiration, location, and cost properties.

| Field | Notes |
|---|---|
| `id` | Stable ID |
| `product_id` | Required |
| `quantity` / `unit` | Current quantity in a supported unit |
| `location_id` | Required unless explicitly unassigned |
| `acquired_at` | Timestamp/date |
| `expires_at` | Nullable timestamp/date |
| `opened_at` | Nullable timestamp |
| `lot_type` | grocery, leftover, prepared, bulk, other |
| `purchase_line_id` | Optional provenance |
| `cooking_session_id` | Optional leftover provenance |
| `total_cost` / `currency` | Cost attributable to the current or original lot, with documented policy |
| `notes` | User notes |
| `version` | Optimistic concurrency |

### InventoryEvent

Required types:

- `ADD`
- `CONSUME`
- `MOVE`
- `OPEN`
- `ADJUST`
- `DISCARD`
- `WASTE`
- `PURCHASE`
- `COOK`
- `LEFTOVER_CREATE`
- `IMPORT`

Each event records lot/product IDs, quantity/unit where applicable, from/to location, reason/source, actor/client, timestamp, and safe metadata. An inventory mutation and its event commit together.

### Inventory invariants

1. Quantities are decimal-safe and never negative.
2. Zero-quantity lots are closed/retained for history or removed from active views; product restock rules remain on the product.
3. A normal consume request cannot exceed available quantity. Multi-lot product consumption allocates FEFO and returns the allocation.
4. Expired, discarded, or closed lots are unavailable by default.
5. Unit conversion occurs only within the same dimension or through an explicit product/package conversion.
6. Mass-to-volume conversion is rejected without explicit density metadata.
7. Opening a lot is idempotent and may derive a new use-by date from product policy without silently extending an earlier expiration.
8. Waste records quantity and attributed value at the time of the event.

## Units

Provide a central registry with dimensions and exact conversion factors.

Minimum built-in support:

- **Mass:** mg, g, kg, oz, lb
- **Volume:** ml, l, tsp, tbsp, fl oz, cup, pint, quart, gallon
- **Count:** count/each and dozen
- **Product-specific/nonconvertible:** serving, bag, can, bottle, package, bunch, slice, other

Store canonical codes separately from display labels. Preserve user-facing units while calculations normalize compatible values.

## Recipes

### Recipe

- stable ID and unique normalized name
- yield servings
- prep, cook, and total minutes where known
- instructions and tags
- active/version fields

### RecipeIngredient

- recipe ID
- product ID after resolution
- retained display text
- quantity/unit for recipe yield
- optional flag and notes
- ordering

Unresolved imported ingredients must be visible for mapping. A production recipe cannot be reported as fully matchable while required ingredients are unresolved.

### Recipe matching rules

- Scale ingredients by requested servings.
- Convert to product-compatible units.
- Use only usable, unreserved inventory.
- Return available, required, missing, and lot allocation details.
- Distinguish “ready,” “missing N products,” “unresolved ingredient,” and “unknown time.”
- A max-time query must define whether unknown time is excluded; default to exclude and expose an override.
- Use-soon scoring considers quantity of urgent inventory consumed, urgency, missing items, and time. Return score components for explainability.

## Meal planning and demand

### MealPlanEntry

- date
- meal type
- recipe ID
- servings
- status: planned, cooking, completed, skipped
- notes and version

### ShoppingDemand

Generated and manual needs are source records, not blind increments.

- stable source key, such as `meal:<entry-id>:ingredient:<ingredient-id>` or `minimum:<product-id>`
- product, quantity, unit
- source kind and source ID
- active/recalculated timestamp

Rebuilding demand upserts or removes source rows so repeated operations are idempotent.

### Shopping view/state

The user-facing list aggregates compatible demand by product and unit while retaining source breakdown. It supports:

- accept/reject suggested minimum-stock demand
- manual lines
- quantity override with an explicit reason
- check/uncheck
- remove/suppress
- store assignment or note
- completion into a purchase

## Cooking and leftovers

### CookingSession

- recipe ID, planned servings, actual servings
- status and timestamps
- proposed and confirmed inventory allocations
- Home Assistant correlation/event ID

Starting a session does not consume inventory. Completing a session atomically:

1. validates selected allocations;
2. consumes lots and records events;
3. marks the meal-plan entry completed when linked;
4. creates confirmed leftover lots;
5. publishes `cooking.completed` after commit.

A leftover lot includes made time, use-by time, servings/unit, location, recipe/session source, and cost attribution where available.

## Purchases, receipts, and prices

### ReceiptImport

- content hash/source ID for deduplication
- upload metadata and safe storage path
- extraction provider/version
- raw extracted text
- status: uploaded, extracted, review, committed, rejected, failed
- parser warnings and timestamps

### Purchase

- store, purchase date/time, currency, subtotal/tax/total where available
- source receipt ID
- notes

### PurchaseLine

- product ID or unresolved text
- quantity/unit/package data
- line total and derived comparable unit price
- matched barcode where available
- link to created inventory lot(s)

Price comparisons must compare compatible package or normalized units and clearly mark estimates. The anomaly feature should use a documented robust baseline, such as recent median, and show the evidence window.

## Identity, time, and money rules

- IDs are stable and never derived from mutable names.
- Persist timestamps in UTC; use the configured household timezone for calendar dates and “days left.”
- Persist currency code with money.
- Never use binary floating-point for authoritative quantities or money.
- Every mutable aggregate has `created_at`, `updated_at`, and either a version or another safe concurrency mechanism.
