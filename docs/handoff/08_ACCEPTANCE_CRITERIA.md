# 8. Verifiable acceptance criteria

These are release gates, not aspirational notes. Codex should add automated coverage wherever practical and record evidence in the implementation ledger.

## A. One source of truth and durability

- [ ] A1. There is one authoritative SQLite database owned by PantryOS Core.
- [ ] A2. The web UI and Home Assistant do not maintain independent inventory state.
- [ ] A3. Adding an item in the web UI changes Home Assistant state promptly without either process restarting.
- [ ] A4. Consuming an item through a Home Assistant action changes the web UI promptly.
- [ ] A5. Restarting PantryOS and Home Assistant preserves and reconciles the same state revision.
- [x] A6. A test with at least 20 concurrent supported mutations produces no lost successful writes and no corrupt database.
- [x] A7. Database transactions roll back all related changes on injected failure.
- [x] A8. Legacy JSON import is validated, backed up, atomic, and idempotent.
- [x] A9. A backup can be restored into a clean instance and passes an integrity/summary comparison.

## B. Products, locations, lots, and events

- [ ] B1. Two lots of the same product can have different locations, expirations, and costs.
- [ ] B2. Product minimum stock remains after all active lots reach zero.
- [ ] B3. Location rename/move does not rewrite every inventory lot or break references.
- [ ] B4. Consumption across lots follows FEFO and returns the exact allocation.
- [ ] B5. Over-consumption is rejected with available quantity unless an explicit administrative adjustment is used.
- [ ] B6. Opening a lot is idempotent and correctly applies configured opened-life policy.
- [ ] B7. Discard and waste create historical events and remove quantity from usable stock.
- [ ] B8. Inventory event history reconstructs the reason and source of every tested mutation.
- [ ] B9. Negative quantities, invalid dates, incompatible units, duplicate constrained names, and cyclic locations are rejected.

## C. Units and recipe intelligence

- [ ] C1. `1 lb` satisfies an `8 oz` requirement and reports the correct remainder.
- [ ] C2. Incompatible dimensions are rejected; mass-to-volume is not guessed.
- [ ] C3. Product aliases resolve recipe text to stable product IDs.
- [ ] C4. Expired/discarded lots do not satisfy recipes by default.
- [ ] C5. Recipe quantities scale correctly with servings.
- [ ] C6. Ready, missing-N, and max-time queries return deterministic explainable results.
- [ ] C7. Unknown prep time follows the documented max-time policy.
- [ ] C8. Use-soon recommendations identify which urgent lots a recipe consumes and why it ranks highly.

## D. Meal planning and shopping

- [ ] D1. A weekly plan stores date, meal type, recipe ID, servings, and status.
- [ ] D2. Ingredient demand aggregates across all planned meals and subtracts usable inventory.
- [ ] D3. Rebuilding shopping demand twice produces the same generated demand, not double quantities.
- [ ] D4. Repeating “add missing” for one recipe/plan source is idempotent.
- [ ] D5. Manual demand and generated demand can coexist with visible source breakdown.
- [ ] D6. Minimum-stock suggestions remain separate until accepted.
- [ ] D7. Shopping lines can be edited, checked, unchecked, suppressed/removed, and assigned notes/store.
- [ ] D8. Completing a purchase creates purchase lines and selected inventory lots transactionally.
- [ ] D9. A user override is preserved or explicitly reconciled when generated demand is rebuilt.

## E. Cooking, leftovers, waste, and value

- [ ] E1. Starting cooking emits an event but does not consume inventory.
- [ ] E2. Completing cooking requires confirmed allocations and is atomic.
- [ ] E3. Completing cooking can create leftover lots with servings, made time, use-by time, location, and recipe/session provenance.
- [ ] E4. Leftovers appear in the next-meal/use-soon experience.
- [ ] E5. Monthly waste is calculated from waste events and survives lot cleanup.
- [ ] E6. Inventory value by freezer/refrigerator/location is available to the API and Home Assistant.

## F. Barcode, receipt, and price workflows

- [ ] F1. A known barcode scan resolves a product/package and can add a lot.
- [ ] F2. An unknown barcode opens a mapping/product form; manual entry works without camera support.
- [ ] F3. Barcode mappings are unique and persist across restart.
- [ ] F4. Receipt upload enforces supported type and size limits and stores files outside the static web root.
- [ ] F5. At least one local extraction implementation works in the documented container environment.
- [ ] F6. Extracted receipt data is editable and cannot mutate inventory before confirmation.
- [ ] F7. Duplicate receipt commit is detected and does not duplicate purchase lines or inventory.
- [ ] F8. Purchase history shows store, date, package/quantity, total, and comparable unit price.
- [ ] F9. Price anomaly output uses compatible units, a documented baseline, and an explanation.

## G. Web/PWA quality

- [ ] G1. All primary inventory, recipe, plan, shopping, cooking, barcode, receipt, and settings workflows are reachable in the UI.
- [ ] G2. No visible control is a toast-only stub or hard-coded success simulation.
- [ ] G3. Loading, empty, offline, validation, conflict, and server-error states are clear.
- [ ] G4. Destructive actions require appropriate confirmation and remain keyboard accessible.
- [ ] G5. Critical workflows pass automated browser tests at phone and kitchen-tablet viewport sizes.
- [ ] G6. Forms have labels, focus handling, error association, and no critical automated accessibility violations.
- [ ] G7. PWA install metadata and an intentional offline/error strategy exist; offline writes are not falsely reported as committed.

## H. Home Assistant

- [ ] H1. Config flow accepts and validates base URL/token and uses the PantryOS instance ID as unique identity.
- [ ] H2. Reconfigure and authentication failure recovery are supported.
- [ ] H3. The integration uses one coordinated cache/push subscription and performs no I/O in entity properties.
- [ ] H4. Entities become unavailable when Core is unreachable and recover automatically.
- [ ] H5. Actions call the API, return useful errors/responses, and remain registered according to current HA conventions.
- [ ] H6. Existing action names are retained or have a documented compatibility/migration path.
- [ ] H7. Sensors cover total inventory, expiring soon, shopping, possible meals, leftovers, waste, and location values/counts without unbounded attributes.
- [ ] H8. Diagnostics redact token, authorization headers, session data, receipt text/images, and secrets.
- [ ] H9. HA tests cover setup, unload, auth failure, unavailable/recovery, sensor updates, and representative actions.
- [ ] H10. Example automations demonstrate use-soon notification, grocery arrival count, cooking mode, and freezer risk/value.

## I. Security and operations

- [ ] I1. Unauthorized API mutation and event subscription fail.
- [ ] I2. Browser authentication/session, CSRF, CORS, origin, and cookie policy are explicit and tested.
- [ ] I3. Request body, upload size, filename/path, MIME/content, and rate limits are enforced.
- [ ] I4. No secrets appear in source, logs, client errors, diagnostics, fixtures, or generated artifacts.
- [ ] I5. Container runs as non-root and writes only intended volume paths.
- [ ] I6. Liveness and readiness have different semantics and tests.
- [ ] I7. Structured logs include request/correlation IDs without sensitive payloads.
- [ ] I8. Migration failure leaves the prior database/backup recoverable.
- [ ] I9. `pantryos doctor`, backup, restore verification, and legacy dry-run are documented and smoke-tested.
- [ ] I10. Core operation is usable without internet access after dependencies/images are installed.

## J. Engineering and release

- [ ] J1. Unit, integration, API, migration, concurrency, HA, browser, and container smoke suites pass from a clean checkout.
- [ ] J2. Lint, format, and type checks pass.
- [ ] J3. Domain/application code has meaningful coverage, including failure paths; the configured threshold passes without broad exclusions.
- [ ] J4. OpenAPI, user setup, Home Assistant setup, backup/restore, migration, and troubleshooting docs match actual behavior.
- [ ] J5. No completion-critical TODO, placeholder URL, fake response, disabled test, or skipped release check remains without a documented non-blocking reason.
- [ ] J6. A scripted demo proves add → sync → plan → shop → cook → leftover → use-soon across the supported surfaces.
- [ ] J7. Independent review reports no open Critical/High findings and no release-blocking Medium finding.
- [ ] J8. `docs/release/RELEASE_READINESS.md` records exact commands, results, residual risks, and a justified `PASS`.
