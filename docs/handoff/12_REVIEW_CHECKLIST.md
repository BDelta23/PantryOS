# 12. Independent review checklist

The final review is evidence-driven and initially read-only. The reviewer may create the review report but should not repair source code in the same pass.

## Finding severity

- **Critical:** likely catastrophic data loss/corruption, authentication bypass with broad impact, secret compromise, or unsafe destructive behavior with no practical guard.
- **High:** release-blocking correctness/security/reliability defect that can materially corrupt household state, bypass authorization, break migration/restore, or invalidate the core user loop.
- **Medium:** important defect or missing requirement with a bounded workaround; release-blocking when it violates an explicit acceptance criterion.
- **Low:** localized quality, accessibility, maintainability, or documentation issue with limited immediate impact.
- **Informational:** observation or improvement with no demonstrated defect.

Every finding includes confidence/evidence status: `confirmed`, `strongly supported`, or `needs verification`.

## Review areas

### Architecture and ownership

- Exactly one authoritative data store.
- No Home Assistant Store, browser cache, JSON file, or second service can silently become authoritative.
- Domain/application boundaries do not depend on FastAPI, SQLAlchemy sessions, or Home Assistant internals.
- Event publication occurs only after commit.
- External providers are isolated and optional.

### Data integrity and concurrency

- Transactions cover compound operations.
- Parallel writes do not lose successful mutations.
- Constraints and validation prevent negative/invalid state.
- FEFO, expiration, unit conversion, reservations, and idempotency are correct.
- Migration/import/backup/restore are atomic and repeatable.
- Event history and balances remain consistent.

### API and security

- Auth on every protected route and stream.
- No token leakage.
- Stable errors for malformed/adversarial input.
- CORS/CSRF/session controls.
- Upload path/content/size/time limits.
- Idempotency and concurrency headers cannot be abused across users/requests.
- OpenAPI matches actual request/response behavior.

### Web/PWA

- No stub or fake-success control.
- Critical flows are complete and recover from server conflict/offline/error.
- Untrusted content is escaped in text and attributes.
- Forms, focus, keyboard, responsive layout, and accessibility.
- Barcode fallback and receipt review are usable.
- No secret persisted in unsafe browser storage.

### Recipe, planning, shopping, and cooking logic

- Unit-aware product matching and unresolved ingredient handling.
- Expired inventory exclusion.
- Plan scaling and demand aggregation.
- Repeated rebuild/add-missing is idempotent.
- Suggestions stay separate until accepted.
- Cooking completion and leftovers are atomic and explainable.
- Waste and value calculations use historical events and defensible cost attribution.

### Home Assistant

- Correct config flow, unique ID, reauth/reconfigure, setup/retry/unload.
- Client uses shared async session and bounded timeouts.
- Coordinator/push behavior and availability.
- Actions registered and resolved correctly.
- Entity native values/device classes/attributes are appropriate and bounded.
- Diagnostics redact sensitive data.
- No direct DB or duplicate inventory persistence.
- Manifest, translations, and tests align with current HA requirements.

### Operations and release

- Non-root container and persistent paths.
- Reproducible dependency installation.
- Liveness/readiness correctness.
- Backup/restore and migration failure recovery.
- Structured/redacted logs.
- Clean-checkout commands and CI parity.
- Documentation matches behavior.

### Test quality

- Tests assert behavior and failure paths rather than implementation trivia.
- Data-loss/security defects have regression tests.
- No unexplained skips, broad mocks, or disabled suites hide release risk.
- Browser and HA tests exercise actual boundaries.
- Container smoke includes restart and persistence.
- Coverage configuration does not exclude critical code.

## Mandatory dynamic checks

At minimum, attempt:

1. clean install and all documented checks;
2. fresh DB and migration to latest;
3. legacy JSON import twice;
4. concurrent mutations;
5. unauthorized/malformed/oversized requests;
6. web add → HA update and HA consume → web update;
7. shopping rebuild twice;
8. cooking completion with injected failure;
9. known/unknown barcode flows;
10. receipt duplicate commit;
11. backup/restore comparison;
12. container restart with persistent volume;
13. HA outage/recovery and diagnostics redaction.

## Finding format

```text
[SEVERITY] R-### — concise title
Evidence status: confirmed | strongly supported | needs verification
Location: path/to/file.py:line-line
Acceptance criteria: A3, H4
Impact: concrete user/system effect
Evidence/reproduction: exact reasoning or commands
Expected invariant: what should be true
Recommended remediation: smallest reliable correction
Regression test: exact test that is missing or should be added
```

Do not bury findings under a general summary. Order by severity and then blast radius.

## Required review report

Create `docs/reviews/YYYY-MM-DD-full-review.md` containing:

1. Release gate: `PASS`, `CONDITIONAL`, or `FAIL`.
2. Scope and exact revision reviewed.
3. Commands executed and environment limitations.
4. Findings in required format.
5. Acceptance-criteria coverage matrix.
6. Security and data-integrity conclusion.
7. Test and documentation gaps.
8. Residual risks and recommended next action.

`PASS` is prohibited when any Critical/High finding is open, a release-blocking Medium exists, a P0 issue is unresolved, the two-interface proof is absent, migrations/restore are unverified, or a primary workflow remains a stub.
