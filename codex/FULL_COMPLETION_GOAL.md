/goal Complete PantryOS from the supplied v0.1.0 proof of concept to the production-ready, local-first v1.0 release defined by this repository's handoff package.

Read `AGENTS.md`, `HANDOFF_README.md`, and every file under `docs/handoff/` before making architectural changes. Treat `docs/handoff/08_ACCEPTANCE_CRITERIA.md` as the binding completion contract and keep `docs/handoff/IMPLEMENTATION_STATUS.md` current with exact commands and evidence.

## Objective

Deliver a working Home Food Intelligence system in this repository, not a plan or a larger prototype. PantryOS Core must become the only authoritative data owner. The web/PWA and Home Assistant integration must operate against the same authenticated, versioned API and show consistent state across restart, failure, and concurrent use.

The completed release must implement the full v1.0 scope in `docs/handoff/01_VISION_AND_SCOPE.md`: transactional inventory, products/lots/locations/events, recipe intelligence, use-soon recommendations, meal planning, idempotent shopping demand, cooking and leftovers, barcode workflows, reviewed receipt ingestion, purchase/price history, Home Assistant, security, backups, operations, and automated verification.

## Non-negotiable requirements

1. Replace JSON and Home Assistant inventory storage with one SQLite-backed PantryOS Core. Do not leave a compatibility path that can become a second live source of truth.
2. Add versioned migrations, an atomic/idempotent import of the legacy `data/pantryos.json`, pre-migration backup, integrity checking, and tested restore.
3. Separate Product, ProductAlias/Barcode, hierarchical Location, InventoryLot, and InventoryEvent concepts. Product-level stock rules must survive when no lots remain.
4. Use decimal-safe quantity and money handling. Convert only compatible units or explicit product/package conversions. Never guess mass-to-volume conversion.
5. Enforce inventory invariants: no negative stock, no silent over-consumption, expired/discarded lots unavailable by default, deterministic FEFO allocation, and an event in the same transaction as every mutation.
6. Build an authenticated `/api/v1` contract with structured errors, request/upload limits, idempotent retry behavior, optimistic conflict handling where needed, OpenAPI, liveness/readiness, and an authenticated event stream.
7. Migrate the bundled web UI to that API and complete every primary workflow. No toast-only stub, fake success, hard-coded response, or completion-critical TODO may remain.
8. Rebuild the Home Assistant integration as an async API client. Config flow must validate URL/token and use the PantryOS instance ID. Use coordinated/push updates, availability, reconfigure/reauth, diagnostics redaction, translated actions, and current Home Assistant lifecycle patterns. It must never open SQLite or persist inventory.
9. Make shopping demand source-based and idempotent. Repeating rebuild, promotion, or add-missing operations must not multiply quantities. Preserve manual overrides and show source breakdown.
10. Implement date/meal/serving-based planning, scaled recipe demand, alias and unit-aware matching, unresolved-ingredient handling, expiration-aware availability, and explainable use-soon ranking.
11. Implement cooking sessions that propose allocations, consume only on confirmed completion, create traceable leftovers, record waste/value history, and emit events for Home Assistant cooking mode.
12. Implement browser barcode scanning with feature detection, manual fallback, known-code fast add, unknown-code mapping, and persistent package mappings.
13. Implement receipt upload → concrete local extraction → editable review → idempotent transactional commit. At least one extraction path must work in the documented container without a paid cloud credential. Store purchases and compatible unit-price history.
14. Apply the security and operational requirements in `docs/handoff/11_SECURITY_AND_OPERATIONS.md`: auth, sessions/CSRF/CORS, safe uploads, secret redaction, non-root container, structured logs, backup/restore, doctor command, and failure recovery.
15. Add and run the complete test matrix in `docs/handoff/09_TEST_AND_RELEASE_PLAN.md`, including concurrency, migration/import, API, Home Assistant, browser, cross-interface, receipt, and container smoke tests.

## Working method

- Start by mapping the current request/data flows and creating deterministic regression tests for the known P0 defects. Preserve a clean baseline checkpoint when Git is available.
- Work through `docs/handoff/07_IMPLEMENTATION_ROADMAP.md` in dependency order. Do not implement receipts or other advanced features before the Phase 3 one-source-of-truth proof passes.
- Use subagents where available for independent areas such as data/migrations, web accessibility, Home Assistant, and security/testing. Avoid concurrent writes to the same files. Wait for their evidence before declaring a phase complete.
- Make routine implementation decisions independently. When a material design choice conflicts with the handoff, write an ADR under `docs/adr/` with alternatives, tradeoffs, migration, security, tests, and rollback before proceeding.
- Do not ask for an external credential to finish a required workflow. Implement an adapter plus a deterministic local/manual path and synthetic fixtures.
- Do not weaken or delete acceptance criteria to make the build pass. Correct the implementation or document a genuine non-applicable criterion that follows from the explicit non-goals.
- Keep compatibility where it does not preserve unsafe architecture. Existing Home Assistant action names should be retained or migrated with clear release notes.
- After each phase, run its strongest targeted checks, update `IMPLEMENTATION_STATUS.md`, and fix failures before moving on.

## Mandatory proof scenarios

Automate these scenarios where possible and execute them before completion:

1. Import the supplied legacy JSON into an empty database twice; the second run creates no duplicates and all counts/quantities/recipes/plan data remain correct.
2. Execute at least 20 concurrent supported mutations; every successful mutation is present and event history balances with active quantities.
3. Add Eggs from the web UI; Home Assistant updates. Consume two Eggs through a Home Assistant action; the web/API updates. Restart both surfaces; they still agree.
4. Store two lots of one product with different expirations, consume by product, and verify FEFO allocation. Reject over-consumption.
5. Match `8 oz` against `1 lb`, reject an incompatible dimension, and exclude expired inventory from a recipe.
6. Build a multi-day plan with scaled servings, rebuild shopping twice, and prove the second result is identical. Confirm suggested minimum stock stays uncommitted until accepted.
7. Start cooking without consuming anything, then complete with confirmed allocations and create leftovers. Inject a failure and prove the transaction rolls back.
8. Scan a known barcode, handle an unknown barcode, and use manual fallback in a deterministic browser test.
9. Upload a synthetic receipt, extract it locally, edit it, commit it, and prove a duplicate commit creates no duplicate purchase or stock. Show price history in compatible units.
10. Take a backup, restore into a clean instance, and compare products, lots, events, recipes, plans, shopping, purchases, and state revision.
11. Stop PantryOS while Home Assistant is running; entities become unavailable. Restart PantryOS; they recover without reconfiguration and without duplicate actions/entities.
12. Build and run the container as non-root, verify readiness, persistence across restart, receipt extraction, and authenticated mutation.

## Completion standard

Continue until every applicable checkbox in `docs/handoff/08_ACCEPTANCE_CRITERIA.md` is evidenced, all documented release commands pass from a clean checkout, and no known P0 issue remains. Do not stop merely because the architecture is in place or tests for the old vertical slice pass.

Before finishing:

- update README/setup, API, Home Assistant, migration, backup/restore, troubleshooting, and release documentation to match the actual implementation;
- remove placeholder URLs, obsolete duplicate-store code, dead dynamic imports, fake demo behavior, and unexplained skipped tests;
- generate or update `docs/release/RELEASE_READINESS.md` with the exact revision, commands, results, coverage, migration/restore evidence, acceptance matrix, and honest residual risks;
- run a final self-review against `docs/handoff/12_REVIEW_CHECKLIST.md` and correct all Critical/High and release-blocking Medium findings;
- leave the repository runnable with documented one-command local/container startup and no required secret committed.

Your final response must summarize the delivered architecture and user workflows, list material migrations and compatibility notes, provide every verification command with result, identify any non-blocking residual risk precisely, and point to `docs/release/RELEASE_READINESS.md`. Do not claim completion when a required command was not run or a primary feature is only scaffolded.
