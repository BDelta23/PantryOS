/goal Perform an independent, evidence-driven, full release-readiness review of the completed PantryOS repository against the handoff vision, architecture, acceptance criteria, security requirements, and current documented behavior.

Use a new session or clean review worktree. Read `AGENTS.md`, `HANDOFF_README.md`, all files under `docs/handoff/`, the implementation status, ADRs, release readiness report, and the complete source/test/configuration tree. Determine the exact revision and working-tree state before reviewing.

## Review posture

This is a review, not an implementation pass. Keep source code and tests unchanged during the initial review. The only file you may create or update is the review report under `docs/reviews/`. Do not silently repair defects, weaken tests, edit acceptance criteria, or accept the implementer's claims without reproducing them.

Treat every prior statement as a claim to verify. Separate confirmed evidence from inference and from checks the environment cannot run. A passing unit suite alone is not release evidence.

Use parallel subagents where available, with non-overlapping read-only scopes, then wait for all results before assigning the release gate:

- architecture, domain model, migrations, and data integrity;
- API, authentication, sessions, upload safety, and event stream;
- web/PWA behavior, XSS handling, accessibility, and browser workflows;
- recipe, planning, shopping, cooking, leftovers, receipt, and price correctness;
- Home Assistant lifecycle, client/coordinator, entities/actions, diagnostics, and tests;
- operations, container, dependencies, backups, recovery, docs, and CI.

## Required review coverage

### 1. Architecture and source-of-truth audit

Trace every read and mutation path. Confirm there is exactly one authoritative SQLite database owned by PantryOS Core. Search for residual JSON persistence, Home Assistant Store inventory, direct SQLite access from HA, browser-side authoritative queues, or alternate code paths that can diverge. Verify events publish only after commit and clients recover from missed events.

### 2. Data integrity and migration audit

Inspect schema, constraints, migrations, repositories, transaction boundaries, optimistic concurrency, decimal handling, FEFO, expiration, unit conversion, demand idempotency, event balancing, receipt commit, cooking completion, purchase completion, backup, and restore. Reproduce the original lost-update race against the new implementation and run a broader concurrent mutation test.

Run legacy import against the supplied-format fixture twice. Compare products, lots, quantities, locations, recipes, shopping, and plan results. Inject failures into migration/import, cooking, receipt commit, and purchase completion and prove rollback/recoverability.

### 3. API and security audit

Enumerate every route and stream. Verify authentication and authorization, stable errors, malformed JSON/media handling, request and upload limits, idempotency scoping, conflict handling, CORS, CSRF, session/cookie policy, origin/host behavior, path traversal resistance, SQL safety, receipt file isolation, OCR timeouts, rate/concurrency controls, and secret redaction.

Test unauthorized, expired/revoked token, malformed, oversized, duplicate, and adversarial inputs. Inspect browser storage and logs for reusable secrets or sensitive receipt data.

### 4. Functional product audit

Execute the complete user loop:

- product/location/lot lifecycle;
- known and unknown barcode;
- recipe mapping and unit conversion;
- ready/missing/time/use-soon recommendations;
- multi-day serving-scaled plan;
- shopping rebuild twice and manual override behavior;
- purchase completion;
- cooking start/complete/rollback and leftovers;
- waste/value history;
- receipt extraction/review/duplicate commit;
- price history/anomaly explanation.

Flag every fake success, dead control, unimplemented route, hard-coded demo response, hidden manual step, or acceptance criterion that exists only in documentation.

### 5. Web/PWA and accessibility audit

Run supported browser tests at phone and kitchen-tablet sizes. Check loading, empty, offline, conflict, auth-expired, validation, and server-failure states. Verify keyboard operation, focus, labels, error association, destructive confirmation, responsive layout, barcode fallback, receipt review, and XSS escaping in text and attributes. Confirm offline writes are never presented as committed when they are not.

### 6. Home Assistant audit

Verify config flow URL/token validation, instance unique ID, duplicate handling, reconfigure/reauth, setup retry, unload/reload, action registration lifecycle, shared async session, bounded timeouts, coordinator/push updates, recovery polling, availability, native value types/device classes, bounded attributes, compatibility action names, diagnostics redaction, manifest metadata/translations, and representative tests.

Prove web mutation → HA state and HA action → web/API state. Stop/restart PantryOS and confirm unavailability/recovery without duplicate entities/actions or lost state. Confirm HA cannot function as a second inventory store.

### 7. Operations and release audit

Run the documented clean-checkout commands. Build and start the container as non-root, verify liveness/readiness distinction, persistent restart, local receipt extraction, backup, restore to a clean volume, and doctor/integrity output. Compare CI commands with local documentation. Inspect dependency pinning, image contents, filesystem permissions, logging/redaction, migration lock behavior, and failure recovery.

Check that README, OpenAPI, Home Assistant setup, migration, backup/restore, troubleshooting, changelog, and release readiness report match the implementation.

## Mandatory dynamic scenarios

Attempt every dynamic check in `docs/handoff/12_REVIEW_CHECKLIST.md`, plus every “Mandatory proof scenario” in `codex/FULL_COMPLETION_GOAL.md`. Record the exact command, result, and environment limitation. A missing tool is a proof gap, not a pass.

## Findings

Use the finding format and severity definitions in `docs/handoff/12_REVIEW_CHECKLIST.md`. Every finding must include:

- severity and evidence status;
- precise file and line range;
- affected acceptance criteria;
- concrete impact;
- reproduction/evidence;
- expected invariant;
- smallest reliable remediation;
- missing regression test.

Order findings by severity and blast radius. Do not lead with praise or bury defects in a narrative summary. Report low-severity and informational issues when they materially improve release confidence, but distinguish them from blockers.

## Required artifact and release decision

Create `docs/reviews/YYYY-MM-DD-full-review.md` with:

1. `PASS`, `CONDITIONAL`, or `FAIL` release gate;
2. exact revision and scope;
3. commands/environment;
4. prioritized findings;
5. acceptance-criteria coverage matrix for A1 through J8;
6. security/data-integrity conclusion;
7. migration/backup/restore conclusion;
8. web and Home Assistant end-to-end conclusion;
9. test/documentation gaps;
10. residual risks and recommended remediation order.

The gate must be `FAIL` when any Critical/High finding is open, any P0 defect remains, duplicate state ownership exists, successful mutations can be lost, authentication can be bypassed, legacy migration/restore can lose data, the two-interface proof fails, or a primary workflow is a stub.

The gate may be `CONDITIONAL` only for bounded Medium issues with explicit workarounds and no violation of data integrity, security, migration, or the primary user loop. `PASS` requires all applicable acceptance criteria to have direct evidence and no release-blocking Medium finding.

Your final response should state the gate first, list blocker IDs, summarize commands executed and proof gaps, and point to the review report. Do not modify the implementation until the review report is complete and the user starts a separate remediation task.
