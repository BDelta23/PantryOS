# 7. Dependency-ordered implementation roadmap

Each phase has an exit gate. Do not begin advanced features while a prior gate is failing. Keep `IMPLEMENTATION_STATUS.md` current with commands and evidence.

## Phase 0 — freeze and instrument the baseline

**Work**

- Initialize/confirm Git history and create a baseline checkpoint.
- Preserve the original source archive checksum and test output.
- Add regression tests that demonstrate the JSON race and non-idempotent shopping behavior before replacing them.
- Establish formatter, lint, type-check, test, and CI commands.

**Exit gate**

- Baseline tests still pass.
- Known defects have deterministic reproductions.
- No product behavior has been silently removed.

## Phase 1 — extract PantryOS Core and add SQLite

**Work**

- Create the package structure and settings.
- Add SQLAlchemy models, repositories, Alembic, foreign keys, indexes, and transaction helpers.
- Implement instance metadata and state revision.
- Implement legacy JSON dry-run/import, backup, and idempotency marker.
- Extract domain rules from the Home Assistant component.

**Exit gate**

- Fresh database creation and all migrations pass.
- Upgrade from a fixture representing `data/pantryos.json` preserves counts and values.
- Re-running import creates no duplicates.
- Parallel write test shows no lost updates.

## Phase 2 — complete core inventory model and API foundation

**Work**

- Products, aliases, barcodes, locations, lots, inventory events, and unit registry.
- Auth, sessions/tokens, stable problem responses, request limits, OpenAPI, health/readiness.
- Add/open/move/adjust/consume/discard/waste operations with event emission.
- Dashboard snapshot and SSE/WebSocket event stream.

**Exit gate**

- All inventory invariants have unit and API tests.
- Mutations and events commit atomically.
- Unauthorized writes fail.
- Browser and a simple API client observe the same revision.

## Phase 3 — reconnect both interfaces and prove one source of truth

**Work**

- Migrate the web dashboard to `/api/v1`.
- Replace `PantryStore` with an async PantryOS client.
- Add HA config flow for URL/token, coordinator/push, availability, and initial sensors/actions.
- Preserve action compatibility through translation to the new API.

**Mandatory cross-interface proof**

1. Add Eggs from the web UI.
2. Home Assistant count/state changes without restart.
3. Consume two Eggs through a Home Assistant action.
4. The web UI reflects the new quantity.
5. Restart PantryOS and Home Assistant test fixtures.
6. Both still agree.

**Exit gate**

- No HA inventory Store remains.
- The integration never opens SQLite.
- Cross-interface automated contract test passes.
- Offline/reconnect behavior is tested.

## Phase 4 — recipes, use-soon, meal plan, and idempotent shopping

**Work**

- Recipe CRUD, ingredient-product resolution, aliases, unit conversion, servings, and match explanations.
- Expiration-aware availability and use-soon scoring.
- Date/meal/serving-based plan.
- Stable shopping demand sources, aggregate list, suggestions, checks, edits, suppression, and purchase completion.

**Exit gate**

- `lb`/`oz` and other compatible conversions work.
- Expired stock does not satisfy a recipe.
- Weekly plan demand scales and consolidates correctly.
- Rebuilding or repeating “add missing” is idempotent.
- Suggested purchases are not committed without user acceptance.

## Phase 5 — cooking sessions, leftovers, waste, and value

**Work**

- Start/complete/cancel cooking session.
- Confirm FEFO lot allocations before consumption.
- Create leftover lots and recommendations.
- Record waste events and monthly history.
- Calculate inventory value by location and expose freezer/refrigerator values.
- Emit cooking events for Home Assistant automations.

**Exit gate**

- Cooking completion is atomic.
- Partial failure cannot consume ingredients without creating the intended completion records.
- Leftovers appear in both UI and HA summaries.
- Waste history survives lot deletion/closure.

## Phase 6 — barcode, receipts, purchases, and prices

**Work**

- Camera barcode scan with manual fallback and local mapping.
- Unknown-code product creation flow.
- Receipt upload limits, safe storage, local extraction provider, parser, review UI, duplicate detection, and commit.
- Purchase and price history, comparable unit price, and anomaly explanation.

**Exit gate**

- Known and unknown barcode browser tests pass.
- At least one committed receipt fixture works through upload → extraction → review → commit.
- Re-committing the same receipt does not duplicate purchases or lots.
- Price history compares only compatible units and identifies its baseline window.

## Phase 7 — complete Home Assistant product surface

**Work**

- Final sensor set, action responses, translations, diagnostics redaction, reconfigure/reauth, repairs where useful, and manifest metadata.
- Push updates with recovery poll.
- Example automations for use-soon, grocery arrival, cooking mode, and freezer risk/value.
- HA integration tests and hassfest-compatible structure where applicable.

**Exit gate**

- Config flow validates URL/token and prevents duplicate instance entries.
- Actions are registered correctly even when an entry is temporarily unavailable.
- Entities report unavailable during outages and recover.
- Diagnostics contain no token or sensitive receipt content.

## Phase 8 — security, operations, UX, and release hardening

**Work**

- Non-root container, dependency pinning/lock, upload and request hardening, CORS/CSRF, logging, backup/restore, doctor command, upgrade docs.
- PWA, responsive/tablet QA, accessibility checks, destructive confirmations, empty/error/loading states.
- Complete test matrix, CI, release notes, API docs, operations guide, and final demo script.

**Exit gate**

- Every acceptance criterion in `08_ACCEPTANCE_CRITERIA.md` is evidenced.
- Full release commands pass from a clean checkout.
- Independent review reports no open Critical/High findings and no release-blocking Medium finding.
- `docs/release/RELEASE_READINESS.md` gives an honest `PASS` with command output and any non-blocking residual risks.
