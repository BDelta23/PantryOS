# 1. Vision and release scope

## Product statement

PantryOS is a **local-first Home Food Intelligence system**. It maintains an accurate model of food in the household and turns that model into useful decisions:

- What can be made now?
- What should be used before it is lost?
- What should be purchased, and why?
- What does the planned week require after current inventory is subtracted?
- What leftovers are available for the next meal?
- What food and value are at risk when a refrigerator or freezer has a problem?

The primary user is the household owner. Home Assistant is a first-class interface and automation surface, but it is not the database.

## Product principles

1. **Accuracy before intelligence.** Recommendations are useful only when inventory transactions are trustworthy.
2. **One source of truth.** Every interface observes and changes the same state.
3. **Low-friction input.** Barcode, receipt, quick-add, and voice/action surfaces reduce manual entry.
4. **Suggest, do not nag.** Suggested purchases remain distinct from committed shopping lines until accepted.
5. **Review before irreversible automation.** Receipt extraction and cooking consumption require confirmation.
6. **Local-first and private.** Core operation does not require a cloud account or telemetry.
7. **Explain recommendations.** A meal or purchase suggestion must expose the inventory and demand that produced it.
8. **Home automation at the boundary.** PantryOS reports food state and emits events; Home Assistant decides how lights, tablets, notifications, and appliances respond.

## Required v1.0 user journeys

### Inventory lifecycle

1. Create or identify a product.
2. Add one or more lots with quantity, unit, location, acquisition date, expiration, cost, and barcode/package information.
3. Open, move, adjust, consume, discard, or mark waste.
4. Preserve every mutation as an inventory event.
5. Keep product-level desired stock even after the final lot is consumed.

### “What can I make?”

- Match recipe ingredients to products through IDs and aliases.
- Convert compatible units.
- Exclude unusable inventory.
- Rank ready meals, meals missing at most N ingredients, and meals under a time limit.
- Explain exactly what is available and missing.

### Use-it-before-you-lose-it

- Show expiring lots and leftovers in urgency order.
- Rank recipes that consume the most urgent usable inventory.
- Surface a concrete recommendation rather than only an expiration alert.

### Shopping and meal planning

- Plan recipes by date, meal type, and servings.
- Scale ingredients and aggregate demand across the plan.
- Subtract usable on-hand inventory and existing reservations.
- Keep generated demand idempotent and show source breakdown.
- Keep suggested minimum-stock purchases separate until accepted.
- Check, uncheck, edit, remove, and convert purchased lines into inventory and price history.

### Leftovers and cooking

- Start a cooking session for a recipe.
- Review the lots and quantities that will be consumed.
- Complete the session transactionally.
- Create leftover lots with servings, made time, use-by time, location, and source recipe/session.
- Emit a cooking event that Home Assistant can use for cooking mode.

### Barcode workflow

- Scan through a phone or tablet camera when browser support exists.
- Accept manual code entry when camera scanning is unavailable.
- Resolve known codes to a product and package quantity.
- For an unknown code, open a prefilled product-mapping form rather than failing.
- Persist the mapping so later scans are fast.

### Receipt and price workflow

- Upload a supported receipt image or text-based digital receipt.
- Run a concrete local extraction implementation in the supported container image.
- Present extracted store, date, products, quantities, and prices for review.
- Require user confirmation before inventory or purchases are changed.
- Detect duplicate imports by content hash or source identifier.
- Store purchases and purchase lines so product/store price history and anomaly comparisons can be calculated.

### Home Assistant

- Configure PantryOS Core URL and token through a config flow.
- Expose summary and value sensors, availability, diagnostics, and documented actions.
- Reflect web changes promptly and reflect Home Assistant actions in the web UI without restart.
- Recover when PantryOS is temporarily offline.
- Never create a second inventory store in Home Assistant.

## v1.0 functional scope

| Area | Required result |
|---|---|
| Core persistence | SQLite, transactions, WAL/busy timeout, migrations, legacy JSON import |
| Domain | Products, aliases/barcodes, locations, lots, events, recipes, meal plans, demand, shopping, cooking, leftovers, purchases, receipts |
| API | Authenticated `/api/v1`, structured errors, idempotency, optimistic concurrency where needed, event stream |
| Web | Responsive PWA for every required user journey; no stub controls |
| Intelligence | Unit-aware matching, use-soon ranking, plan aggregation, stock suggestions, waste and value metrics |
| Input | Manual, barcode, reviewed receipt import |
| Home Assistant | Local API client, config flow, coordinator/push updates, entities, actions, diagnostics, tests |
| Operations | Docker, non-root runtime, health/readiness, backups, restore, logs, upgrade docs |
| Quality | Automated tests, lint/type checks, browser tests, migration and concurrency coverage, release report |

## Deferred beyond v1.0

The following are intentionally not completion blockers:

- Multiple households, hosted accounts, or cloud synchronization.
- Live retailer scraping, coupon aggregation, or current-store price feeds.
- Dedicated scanner hardware integrations beyond keyboard-wedge/manual compatibility.
- Autonomous appliance control from PantryOS.
- Nutrition, allergy, or medical decision support.
- Fully autonomous AI meal planning or unreviewed receipt commits.
- Mass-to-volume conversion without explicit product density/package conversion data.
