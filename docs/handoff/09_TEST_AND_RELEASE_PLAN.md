# 9. Test, CI, and release plan

## Test layers

### Domain unit tests

Cover conversion, invariants, FEFO allocation, expiration, recipe scaling/matching, use-soon scoring, demand idempotency, waste/value, price normalization, and event creation. These tests should not require HTTP or a real database unless repository behavior is the subject.

### Repository and migration tests

Use temporary SQLite databases to cover:

- migration from empty through latest;
- upgrade from each committed schema revision;
- legacy JSON dry-run/import and duplicate prevention;
- foreign keys and constraints;
- transaction rollback on injected failures;
- concurrent writes and busy handling;
- backup and restore equivalence.

### API integration tests

Cover authentication, authorization, validation, stable errors, pagination, idempotency keys, optimistic conflicts, request limits, receipt upload safety, event publication after commit, and the complete workflow endpoints.

### Home Assistant tests

Use the supported Home Assistant custom-component test harness. Mock the PantryOS HTTP service at the transport boundary. Cover config flow, duplicate instance, reconfigure/reauth, setup/unload, unavailable/recovery, coordinator/push updates, entity state/attributes, action calls, action errors, and diagnostics redaction.

### Browser tests

Use Playwright or an equivalent maintained browser runner. Critical flows:

1. initial setup/login;
2. add/edit/move/open/consume/discard item;
3. product and location management;
4. recipe match and meal planning;
5. idempotent shopping rebuild and purchase completion;
6. cooking completion and leftover creation;
7. known/unknown/manual barcode flow;
8. receipt upload, review, and commit;
9. offline/server-error/conflict handling;
10. phone and tablet viewport accessibility smoke.

Camera APIs can be abstracted behind an injectable scanner adapter for deterministic tests.

### End-to-end cross-interface test

Run PantryOS Core, the bundled web client, and a Home Assistant integration test client/fixture. Prove web mutation → HA update and HA action → web/API update against one database and revision.

### Container smoke tests

- Build from a clean context.
- Run as the configured non-root user.
- Wait for readiness.
- Exercise authenticated state and mutation.
- Restart with the same volume.
- Run local receipt extraction fixture inside the image.
- Create backup, restore into a second clean volume, and compare state.

## Expected commands

Codex should make the final commands authoritative in `README.md` and CI. A reasonable target is:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy src custom_components/pantryos
python -m pytest -q
python -m pytest tests/migrations -q
python -m pytest tests/home_assistant -q
python scripts/check.py
PANTRYOS_API_TOKEN=browser-smoke-token node scripts/browser_smoke.cjs

# Only when a frontend toolchain is introduced
npm ci
npm run lint
npm run test
npm run build
npm run test:e2e

docker compose build
python scripts/container_smoke.py
python scripts/smoke_e2e.py
python -m pantryos.cli doctor
```

Do not retain `scripts/run_tests.py` as the only release runner. It may remain as a minimal smoke tool during migration.

## Coverage policy

- Enforce a meaningful threshold on `src/pantryos/domain` and `src/pantryos/application`, initially at least 85% line coverage unless branch coverage provides a stronger gate.
- Do not inflate coverage by omitting error paths, marking large files excluded, or testing only serialization.
- Report untestable hardware/browser behavior as a specific manual check, not as covered.

## CI jobs

1. Python formatting/lint/type/unit/integration.
2. Migration and legacy import matrix.
3. Home Assistant custom-component tests and metadata validation.
4. Frontend lint/unit/build/browser tests when applicable.
5. Container build and smoke.
6. Dependency/security scanning with reviewed severity policy.
7. Documentation/link/schema checks.

Cache dependencies but never cache or upload tokens, real databases, receipt images, or household data.

## Test data

Use synthetic fixtures only:

- multiple lots and expirations;
- aliases and unit conversions;
- a weekly meal plan;
- a known barcode and an unknown barcode;
- a generated receipt image/text with deterministic expected extraction;
- price history across stores;
- expired/waste events;
- legacy JSON representative of the supplied file.

## Release artifacts

Required before v1.0:

- Tagged version and changelog.
- Reproducible container image instructions.
- Database schema/migration version.
- OpenAPI document generated from the running application.
- Home Assistant installation and configuration guide.
- Operations guide for backup, restore, upgrades, logs, and recovery.
- `docs/release/RELEASE_READINESS.md` with exact command outputs and residual risks.
- `docs/release/manual-validation.json` validated by `python scripts/manual_release_evidence.py` for physical barcode camera, real receipt OCR, published image signature, and independent full review evidence. Operators can generate the current-HEAD field scaffold with `python scripts/manual_release_evidence.py --write-template`, or target an immutable candidate with `python scripts/manual_release_evidence.py --write-template --commit <release-candidate-sha>`; the generated template is intentionally incomplete and must become concrete `PASS` evidence before release. The final `docs/release/manual-validation.json` file itself must be git-tracked and clean in the validating checkout, may contain only the generated required check IDs, must keep each check's generated acceptance IDs exact, must stay on the generated schema with no ignored extra fields, and local evidence artifacts must be git-tracked and clean under `docs/release/evidence/`. Physical barcode evidence must include separate valid 8-14 digit GTIN known-barcode and unknown/manual-fallback outcomes; real receipt evidence must identify a representative real receipt source, describe OCR extraction from a receipt image/photo/scan, and include committed `receipt_` and `purchase_` identifiers, committed lot count, and price-history confirmation covering store, date, package/quantity, total, and unit price. Published-image evidence must include a SemVer `vX.Y.Z` release tag, the final `sha256:<digest>`, a published image reference containing that digest, a signature identity, a transparency-log URL or explicit `not available: ...` reason, and a passing `cosign verify` command against that same published image reference, digest, and identity; independent review evidence must reference an existing git-tracked and clean `docs/reviews/` artifact that mentions the target release commit and includes machine-readable `decision=PASS`, `open_critical_high=0`, and `release_blocking_medium=0` markers.
- Independent review report under `docs/reviews/`.

## Release gate

`PASS` requires:

- every P0 issue closed with a regression test;
- every acceptance criterion either evidenced or explicitly marked non-applicable with a justified scope reference;
- no open Critical/High finding;
- no release-blocking Medium finding;
- clean migration/import and backup/restore tests;
- two-interface consistency proof;
- no stubbed primary workflow.
