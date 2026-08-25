# PantryOS repository instructions

## Read before editing

Read these files in order:

1. `HANDOFF_README.md`
2. `docs/handoff/01_VISION_AND_SCOPE.md`
3. `docs/handoff/02_BASELINE.md`
4. `docs/handoff/03_KNOWN_ISSUES.md`
5. `docs/handoff/04_TARGET_ARCHITECTURE.md`
6. `docs/handoff/05_DOMAIN_MODEL.md`
7. `docs/handoff/08_ACCEPTANCE_CRITERIA.md`
8. `docs/handoff/12_REVIEW_CHECKLIST.md`

Use `docs/handoff/IMPLEMENTATION_STATUS.md` as the execution ledger and keep it current.

## Non-negotiable architecture

- PantryOS Core is the only authoritative data owner.
- Use a transactional SQLite database with schema migrations; do not retain JSON or Home Assistant storage as a second live source of truth.
- The web UI and Home Assistant integration must use the same versioned PantryOS API.
- Model products separately from inventory lots. Model locations as records, not path strings. Record inventory mutations in an event ledger.
- Preserve existing user data through an idempotent import from the legacy `data/pantryos.json` format.
- Generated shopping demand must be idempotent. Repeating a sync or “add missing” action must not multiply demand.
- Quantities use decimal-safe arithmetic. Convert only compatible units; never infer mass-to-volume conversions without a product-specific factor.
- Expired or discarded lots are unavailable by default. Consumption should use a deterministic FEFO policy unless a lot is explicitly selected.
- Home Assistant is an API client and automation surface. It must not access SQLite directly or persist a separate inventory.
- No core workflow may depend on a paid cloud service or an unavailable API key. External enrichment must be adapter-based and have a local/manual fallback.

## Engineering rules

- Work in dependency-ordered milestones. Prove cross-interface synchronization before implementing advanced input features.
- Do not stop at plans, placeholders, fake buttons, hard-coded demo responses, or TODO-only scaffolding.
- Preserve useful behavior while replacing unsafe internals. Document intentional breaking changes and migration behavior.
- Add a regression test for each corrected data-integrity or security defect.
- Keep secrets out of source, fixtures, logs, diagnostics, and generated reports.
- Use structured errors and stable API contracts. Avoid silent coercion or silent data loss.
- Record material architectural deviations as ADRs under `docs/adr/` before implementing them.
- Keep the frontend accessible, keyboard-usable, responsive on a kitchen tablet and phone, and explicit about destructive actions.

## Verification

Run the strongest available checks after each milestone. The final release must run the complete commands documented in `docs/handoff/09_TEST_AND_RELEASE_PLAN.md`, including concurrency, migration, API, Home Assistant, browser, and Docker smoke coverage.

Never claim a check passed unless its command was executed. Record unavailable checks and the exact proof gap, then continue with all checks the environment supports.

## Code review rules

Follow `docs/handoff/12_REVIEW_CHECKLIST.md`.

Prioritize correctness and data integrity over style. Report findings with severity, confidence, file and line evidence, impact, reproduction steps, remediation, and the missing regression test. Treat duplicate state ownership, lost writes, unauthenticated mutation, non-idempotent shopping demand, migration data loss, and stubbed user actions as release blockers.
