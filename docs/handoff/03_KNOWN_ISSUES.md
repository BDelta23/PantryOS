# 3. Known issues and risk register

## P0 — release blockers

### P0-1: Split-brain persistence

- **Evidence:** The web app reads `data/pantryos.json` through `JsonInventoryRepository` (`app/server.py:49-79`). Home Assistant reads its own `Store` (`custom_components/pantryos/store.py:14-26`).
- **Impact:** An item added in the browser does not exist in Home Assistant, and a Home Assistant action does not update the browser.
- **Required correction:** PantryOS Core owns one database. Both interfaces use the same API.

### P0-2: Silent lost updates under concurrency

- **Evidence:** `mutate()` performs load, callback, and save with no transaction or lock (`app/server.py:75-79`). The deterministic reproducer in `evidence/json_race_reproducer.py` loses one of two successful writes.
- **Impact:** Household inventory can silently diverge from reality.
- **Required correction:** Transactional SQLite operations, busy timeout/WAL as appropriate, concurrency regression tests, and no whole-document read-modify-write persistence.

### P0-3: Unauthenticated network mutation

- **Evidence:** Every write endpoint accepts requests without a session or token (`app/server.py:215-307`). Docker binds the service to all container interfaces (`Dockerfile`, `compose.yaml`).
- **Impact:** Any device able to reach the port can add, consume, move, or delete food. Cross-site request delivery is also possible because request content type and origin are not enforced.
- **Required correction:** Authenticated API clients, secure browser session handling, same-origin/CSRF controls, explicit CORS policy, and safe default binding/setup behavior.

### P0-4: API error handling is not reliable

- **Evidence:** `_read_json()` is called before the `try` block (`app/server.py:215-219`), so malformed JSON is not converted into a structured 400 response. Decimal parsing can raise exceptions not covered by the handler. Type errors can escape as connection failures.
- **Impact:** Clients receive dropped connections or inconsistent errors; malformed input can destabilize requests.
- **Required correction:** Framework-level validation, bounded request bodies, stable problem responses, and tests for malformed and adversarial input.

### P0-5: No migration, backup, or recovery path

- **Evidence:** The current file is deserialized directly with no schema version (`app/server.py:56-61`). Home Assistant Store has a version but no migration implementation.
- **Impact:** A model change or corrupt file can make all state unreadable. There is no verified restore path.
- **Required correction:** Versioned database migrations, idempotent legacy import, automatic pre-migration backup, documented backup/restore, and restore tests.

## P1 — major correctness and product gaps

### P1-1: Product definitions and physical stock are conflated

`InventoryItem` stores barcode, minimum stock, quantity, cost, and expiration together (`inventory.py:51-67`). Product-level rules disappear with a lot, so `consume_item()` keeps a zero-quantity item only when it has `minimum_stock` (`inventory.py:259-273`). This is a workaround, not a stable model.

**Required:** Product, barcode/package, inventory lot, and event records.

### P1-2: Recipe matching is exact-unit and text-name based

`inventory_totals()` keys by normalized name and exact unit (`inventory.py:334-349`). `1 lb` does not satisfy `8 oz`; aliases such as “chicken breasts” do not resolve to a product. Expired inventory is included.

**Required:** Product IDs, aliases, compatible unit conversion, package conversions, and usable-lot filtering.

### P1-3: Generated shopping demand is additive rather than idempotent

`add_missing_to_shopping_list()` adds the current shortage to an existing source row (`inventory.py:303-319`). Repeating the operation doubles the quantity. `promote_suggested_purchases()` has the same pattern (`inventory.py:321-325`). The current test explicitly expects this accumulation.

**Required:** Stable demand source keys, upsert/recalculation semantics, aggregate source breakdown, and a regression test proving repeated syncs do not multiply need.

### P1-4: Meal planning cannot represent a real plan

The state is `dict[str, str]` (`inventory.py:215`) and stores recipe names (`inventory.py:298-301`). It has no date type, meal type, servings, status, reservations, or stable recipe ID. A recipe rename breaks the reference.

**Required:** Date-based meal-plan entries, recipe IDs, servings, status, scaling, and consolidated demand.

### P1-5: Leftovers are only a tag

Leftovers reuse `purchased` as a made date and have no source cooking session, dedicated use-by semantics, or completion flow.

**Required:** Cooking sessions and leftover lots with traceable source and lifecycle.

### P1-6: Food-waste history is not historical

`food_waste_estimate()` sums currently present expired rows in the current month (`inventory.py:415-427`). Deleting or correcting an expired row erases the “history”; quantity and partial consumption are not represented.

**Required:** `DISCARD`/`WASTE` events with quantities and attributed cost.

### P1-7: Locations are unstructured strings

Locations are normalized paths (`inventory.py:28-33`) and counted with `startswith()` (`inventory.py:408-410`). Renaming a location rewrites item strings, and sensors rely on hard-coded path names (`inventory.py:453-457`).

**Required:** Hierarchical location records, type metadata, stable IDs, and optional Home Assistant temperature entity mapping.

### P1-8: Consumption and validation permit surprising states

- Consuming more than available silently removes or zeroes the item (`inventory.py:259-273`).
- Recipe ingredient and shopping quantities are not required to be positive.
- Negative minimum stock or cost can be accepted.
- Duplicate recipe names are allowed; lookup returns the first match.
- Unknown prep time can pass a max-time filter (`inventory.py:351-356`).

**Required:** Explicit invariants and validation tests.

### P1-9: The web interface omits core lifecycle actions

- Move exists in the backend but has no UI.
- Item and recipe editing are absent.
- Shopping lines cannot be checked, edited, or deleted.
- The “Start Cooking” button only displays `Cooking mode queued` (`app/static/app.js:313`).
- There is no barcode camera flow, receipt flow, price history, weekly plan, or backup/settings screen.

**Required:** Complete workflows with no inert or deceptive controls.

### P1-10: Home Assistant is not an external-service integration

- Config flow stores no URL/token (`config_flow.py:18-29`).
- `iot_class` says `local_polling`, but there is no API poll (`manifest.json`).
- Actions are registered from `async_setup_entry` and capture one local store (`__init__.py:18-118`) rather than being registered at integration setup and resolving the active config entry.
- Sensors recompute the full summary independently and do not expose availability (`sensor.py:86-98`).
- No reconfigure/reauth, diagnostics, repair, or Home Assistant harness tests exist.

**Required:** Async client, config validation, runtime data/coordinator, push or responsible polling, service/action setup aligned with current HA patterns, availability, diagnostics, and tests.

## P2 — quality, maintainability, and release gaps

- Domain code lives inside the Home Assistant component and is dynamically loaded by the web server (`app/server.py:23-39`).
- The server uses `http.server` and hand-written routing/validation.
- Baseline had a placeholder manifest documentation URL; the current manifest no longer ships that placeholder.
- No dependency lock file, CI workflow, formatter/linter/type checker configuration, changelog, license, or release process is present.
- Browser tests, accessibility tests, Home Assistant tests, migration tests, security tests, and container smoke tests are absent.
- Health checks do not distinguish liveness from database readiness.
- Fixed demo data is automatically inserted into an empty production data file (`app/static/app.js:316-320`).
- Static path containment uses a string prefix comparison (`app/server.py:337-340`) rather than a path-aware containment check.
- Request size, upload size, and rate limits do not exist.
- Logs are suppressed by overriding `log_message()` (`app/server.py:309-310`).

## Risk handling order

1. Replace persistence and prove one source of truth.
2. Add migrations/import and transactional invariants.
3. Add authenticated API and error contracts.
4. Reconnect the web UI and Home Assistant.
5. Complete intelligence and lifecycle workflows.
6. Add barcode, receipt, and price features.
7. Harden, document, and independently review.
