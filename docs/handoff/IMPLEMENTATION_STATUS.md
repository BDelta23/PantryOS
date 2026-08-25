# PantryOS implementation status

Codex should update this file throughout the completion goal. Record exact commands and concise evidence; do not mark a gate complete based on code inspection alone when it is runnable.

## Baseline

- [x] Original source preserved in handoff package.
- [x] `python scripts/run_tests.py` — 11 tests passed on 2026-08-25.
- [x] Python compile and JavaScript syntax checks passed.
- [x] JSON lost-update defect reproduced.
- [x] `docker compose config` — passed on 2026-08-25 in local Docker Desktop environment.

## Phase gates

- [x] Phase 0 — baseline regression tests and engineering tooling
- [x] Phase 1 — SQLite, migrations, legacy import, package extraction
- [ ] Phase 2 — inventory domain and authenticated API foundation (in progress; current HA-used routes are authenticated under `/api/v1`)
- [ ] Phase 3 — web + Home Assistant one-source-of-truth proof (in progress; automated web/API-to-HA-client sync proof added)
- [ ] Phase 4 — recipes, use-soon, meal plan, idempotent shopping
- [ ] Phase 5 — cooking, leftovers, waste, and location value
- [ ] Phase 6 — barcode, receipts, purchases, and price history
- [ ] Phase 7 — complete Home Assistant surface
- [ ] Phase 8 — security, operations, UX, and release hardening
- [ ] Independent full review completed
- [ ] Release gate PASS

## Evidence log

| Date/time | Phase | Change or decision | Commands and result | Remaining proof gap |
|---|---|---|---|---|
| 2026-08-25 | Baseline | Handoff created from v0.1.0 prototype | 11 tests pass; compileall pass; node syntax pass | Docker unavailable in inspection environment |

| 2026-08-25 | Phase 0 | Handoff controls installed into repo root; Git repository initialized for baseline checkpoint | `python scripts/run_tests.py` -> 11 tests passed; `python -m compileall -q app custom_components/pantryos scripts tests` -> passed; `node --check app/static/app.js` -> passed; `docker compose config` -> passed; baseline commit `98078e3` created | Formatter/lint/type/CI commands still need final v1 tooling; SQLite Core not started |
| 2026-08-25 | Phase 0 | Deterministic baseline defect reproducer added for JSON lost update and additive shopping demand | `python scripts/reproduce_baseline_defects.py` -> P0/P1 baseline defects reproduced; surviving JSON item list was `['Eggs']`; repeated recipe demand quantity was `6` | Final invariant tests will replace this reproducer after SQLite/idempotent shopping implementation |
| 2026-08-25 | Phase 1 | Added `src/pantryos` Core with SQLite migrations, unit registry, transaction wrapper, product/location/lot/event records, FEFO product consumption, legacy JSON import with backup/import marker, and backup/restore helpers | `python scripts/check.py` -> python compile: 18 files passed; 18 tests passed; javascript syntax passed | Web server still uses JSON repository; Home Assistant still uses HA Store until later phase |
| 2026-08-25 | Phase 1 | Replaced web app JSON repository with SQLite-backed PantryOS Core; app startup imports legacy `data/pantryos.json` only when the Core database is empty; Docker image now copies `src/` and uses `/app/data/pantryos.sqlite3` | `python scripts/check.py` -> python compile: 18 files passed; 18 tests passed; javascript syntax passed; `docker compose build` -> built; `docker compose run --rm pantryos python scripts/run_tests.py` -> 18 tests passed; `GET /api/v1/health/ready` -> `{"status":"ready"}`; `GET /api/v1/instance` -> schema_version 1, state_revision 12 | Home Assistant still uses HA Store and must be replaced in Phase 3 |
| 2026-08-25 | Phase 2 | Added bearer-token enforcement for protected `/api/v1` routes, request IDs, stable problem responses, contract-compatible v1 inventory lot create/consume/move/discard routes, Docker token configuration, and API documentation | `python scripts/check.py` -> python compile: 18 files passed; 19 tests passed; javascript syntax passed; `docker compose config` with `PANTRYOS_API_TOKEN=local-dev-token` -> passed; `docker compose build` -> built; `docker compose run --rm pantryos python scripts/run_tests.py` -> 19 tests passed; Docker smoke: container healthy, unauthenticated `GET /api/v1/dashboard` -> `401 unauthorized`, authenticated `POST /api/v1/inventory/lots` + `POST /api/v1/inventory/lots/{id}/consume` -> revision 14 | Browser compatibility routes remain unauthenticated until browser session/CSRF work; OpenAPI, CORS/origin policy, rate limits, and event subscription auth are still pending |
| 2026-08-25 | Phase 3 | Added Home Assistant `PantryAPIClient`, config flow URL/token validation against `/api/v1/instance`, API-backed polling sensor cache, and API-backed service calls for item add/consume/discard/move plus current recipe/shopping compatibility actions; removed HA Store from setup path | `python scripts/check.py` -> python compile: 20 files passed; 21 tests passed; javascript syntax passed; `docker compose build` -> built; `docker compose run --rm pantryos python scripts/run_tests.py` -> 21 tests passed | Full HA runtime test harness is not installed; event push/coordinator, auth recovery diagnostics, and live HA-to-web/browser sync proof remain pending |
| 2026-08-25 | Phase 3 | Moved HA client recipe/meal/shopping calls onto authenticated `/api/v1` routes and added automated cross-surface proof: browser compatibility route add is visible through HA API client refresh; HA API client consume is visible through browser state without restarting either surface | `python scripts/check.py` -> python compile: 21 files passed; 22 tests passed; javascript syntax passed; `docker compose build` -> built; `docker compose run --rm pantryos python scripts/run_tests.py` -> 22 tests passed; Docker readiness `GET /api/v1/health/ready` -> `{"status":"ready"}` | Full HA runtime test harness, Home Assistant entity lifecycle proof, and event push/coordinator remain pending |
## Architectural decisions

Record ADR paths here as they are created.

| ADR | Status | Summary |
|---|---|---|
| docs/adr/0001-stdlib-core-foundation.md | Accepted | Use Python stdlib sqlite3 and explicit SQL migrations for the Phase 1 Core extraction; final API/auth/OpenAPI gates remain required |

## Open blockers

- Home Assistant uses the PantryOS Core API setup path now, but full HA runtime tests, coordinator/push subscription, diagnostics, and recovery behavior are still pending.
- Browser compatibility routes remain unauthenticated until browser session, CSRF, CORS/origin, and UI token/session work are implemented; current HA client no longer depends on those routes.
- OpenAPI generation/validation is not implemented yet.
- Events are persisted but no authenticated SSE/WebSocket subscription exists yet.
- Advanced product/location/recipe/shopping/cooking/barcode/receipt workflows are still incomplete.
- Completion acceptance criteria are not fully evidenced.




