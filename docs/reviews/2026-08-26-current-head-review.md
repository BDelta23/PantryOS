# PantryOS Current HEAD Release Review

## Release gate

**FAIL**

This report supersedes `docs/reviews/2026-08-26-full-review.md` for current HEAD. The prior High findings for HA sensor staleness and non-isolated container smoke are resolved on current HEAD. The repository still must not be tagged as v1.0 because release-blocking Medium evidence gaps remain and `docs/release/RELEASE_READINESS.md` is still `NOT READY`.

## Scope and revision reviewed

- Repository: `\\ds620-slim\Code Projects\PantryOS`
- Exact revision: `82d7f3284ea3457139e332c7cafb77a037ce3d52`
- Short revision: `82d7f32 Isolate container release smoke`
- Review date: 2026-08-26
- Worktree at start of review: only `?? docs/reviews/`
- Review mode: read-only for application/source files; this report is the only intended write.

## Commands executed

- `git rev-parse HEAD` -> `82d7f3284ea3457139e332c7cafb77a037ce3d52`
- `git status --short` -> `?? docs/reviews/`
- `Get-Content HANDOFF_README.md`
- `Get-Content docs/handoff/01_VISION_AND_SCOPE.md`
- `Get-Content docs/handoff/02_BASELINE.md`
- `Get-Content docs/handoff/03_KNOWN_ISSUES.md`
- `Get-Content docs/handoff/04_TARGET_ARCHITECTURE.md`
- `Get-Content docs/handoff/05_DOMAIN_MODEL.md`
- `Get-Content docs/handoff/08_ACCEPTANCE_CRITERIA.md`
- `Get-Content docs/handoff/12_REVIEW_CHECKLIST.md`
- `rg -n "state_revision|total_items|core_push|wait|final" scripts/ha_core_live_smoke.py custom_components/pantryos` -> confirmed HA smoke waits for final `state_revision` and `total_items` entity state checks.
- `rg -n "isolated|project|container|volume|ports|cleanup|COMPOSE_PROJECT_NAME" scripts/container_smoke.py` -> confirmed isolated Compose project/container/volume/port and cleanup paths.
- `rg -n "manual_release_evidence|manual-validation|NOT READY|READY|blocked|Release readiness|Status|Decision|manual evidence" docs/release/RELEASE_READINESS.md scripts/release_readiness.py scripts/manual_release_evidence.py README.md docs/handoff/09_TEST_AND_RELEASE_PLAN.md docs/handoff/IMPLEMENTATION_STATUS.md` -> confirmed readiness remains `NOT READY` and manual evidence command is a required release command.
- `.\.venv\Scripts\python.exe scripts\ha_core_live_smoke.py --timeout 240` -> passed with `ok=true`, Home Assistant `2026.8.3`, `config_flow_result_type=create_entry`, `core_mode=isolated`, `revision_before=0`, `revision_after_service=1`, `revision_after_core_push=2`, `state_revision_state=2`, `total_items_state=2`, `sensor_count=16`, `unloaded=true`, `remaining_services=[]`.
- `docker ps -a --filter name=pantryos-ha-core-smoke --format "{{.Names}}"` -> no output.
- `docker network ls --filter name=pantryos-ha-core-smoke --format "{{.Name}}"` -> no output.
- `docker volume ls --filter name=pantryos-ha-core-smoke --format "{{.Name}}"` -> no output.
- `.\.venv\Scripts\python.exe scripts\container_smoke.py --isolated --build-timeout 360 --ready-timeout 120` -> passed with `ok=true`, `isolated=true`, Docker server `29.6.1`, base URL `http://127.0.0.1:51940`, container `pantryos-container-smoke-4ea7ce896faf`, volume `pantryos-container-smoke-data-4ea7ce896faf`, UID `10001`, `revision_after_restart=4`, backup `/app/data/backups/container-smoke-20260826132603.zip`, restored/source counts matching: `events=4`, `lots=2`, `products=2`, `purchases=1`, `recipes=0`.
- `docker ps -a --filter name=pantryos-container-smoke-4ea7ce896faf --format "{{.Names}}"` -> no output.
- `docker network ls --filter name=pantryos-container-smoke-4ea7ce896faf --format "{{.Name}}"` -> no output.
- `docker volume ls --filter name=pantryos-container-smoke-data-4ea7ce896faf --format "{{.Name}}"` -> no output.
- `.\.venv\Scripts\python.exe scripts\manual_release_evidence.py --json` -> failed as expected with `ok=false`, missing `docs/release/manual-validation.json`, required checks `independent-full-review`, `physical-barcode-camera`, `published-image-signature`, and `real-receipt-ocr`.
- `.\.venv\Scripts\python.exe scripts\release_readiness.py --json` -> `decision=NOT READY`, `acceptance_criteria_count=85`, `phase_gate_count=11`, required release commands include `python scripts/container_smoke.py --isolated`, `python scripts/ha_core_live_smoke.py`, `python scripts/manual_release_evidence.py`, and `python scripts/release_readiness.py --check`.
- `.\.venv\Scripts\python.exe scripts\check.py` -> `python compile: 46 files passed`; `100 tests passed`; `api concurrency smoke: 20 mutations passed`; `release readiness: current`; `release artifact audit: current`; `javascript syntax: 2 files passed`.

Environment limitation: UNC process spawning required escalated shell execution for Git, ripgrep, Python, and Docker commands. The executed review commands were read-only except for isolated Docker resources intentionally created and cleaned by the smoke tests and this review report write.

## Resolved prior findings

### HA Core smoke false-pass risk — resolved

Evidence status: confirmed

The prior review found that `ha_core_live_smoke.py` could pass after the Core revision advanced while HA entity states remained stale. Current source now derives expected entity states from the final coordinator summary and waits until both the `state_revision` and `total_items` Home Assistant entities match those expected values (`scripts/ha_core_live_smoke.py:181`, `scripts/ha_core_live_smoke.py:182`, `scripts/ha_core_live_smoke.py:192`, `scripts/ha_core_live_smoke.py:194`, `scripts/ha_core_live_smoke.py:200`). The live smoke on current HEAD passed with `revision_after_core_push=2`, `state_revision_state=2`, and `total_items_state=2`. Generated HA smoke Docker containers, networks, and volumes were absent after cleanup.

### Independent isolated container smoke — resolved

Evidence status: confirmed

The prior review could not run the full container smoke independently because the old path mutated the existing Compose service/database. Current source adds an isolated mode that writes a temporary Compose file, generated container name, generated volume, optional/generated port, generated project name, and cleanup routine (`scripts/container_smoke.py:108`, `scripts/container_smoke.py:151`, `scripts/container_smoke.py:156`, `scripts/container_smoke.py:162`, `scripts/container_smoke.py:165`, `scripts/container_smoke.py:310`, `scripts/container_smoke.py:314`, `scripts/container_smoke.py:315`, `scripts/container_smoke.py:318`, `scripts/container_smoke.py:483`). The isolated smoke passed on current HEAD and Docker cleanup checks for its exact generated container, network, and volume returned no resources.

## Findings

[MEDIUM] R-001 — Physical camera and representative real-receipt evidence are still absent
Evidence status: confirmed
Location: `scripts/manual_release_evidence.py:16`, `scripts/manual_release_evidence.py:17`, `scripts/manual_release_evidence.py:20`, `scripts/manual_release_evidence.py:21`; `docs/release/RELEASE_READINESS.md:40`, `docs/release/RELEASE_READINESS.md:44`, `docs/release/RELEASE_READINESS.md:74`
Acceptance criteria: F2, F5, F6, G5, G6
Impact: The implemented barcode and receipt workflows have deterministic browser/OCR automation, but the release still lacks evidence from a physical camera/manual barcode path and a representative real receipt. That leaves the user-facing input workflows unproven in the actual kitchen-device conditions required by the handoff package.
Evidence/reproduction: `.\.venv\Scripts\python.exe scripts\manual_release_evidence.py --json` returned `ok=false` because `docs/release/manual-validation.json` is missing; required checks include `physical-barcode-camera` and `real-receipt-ocr`. Release readiness explicitly lists physical-device camera and real-receipt validation as pending.
Expected invariant: v1.0 release evidence includes concrete PASS records for the physical barcode camera/manual fallback and a representative real receipt OCR/review workflow against current HEAD.
Recommended remediation: Capture the required device/browser/receipt evidence, store local artifacts under the documented evidence path, and record `physical-barcode-camera` and `real-receipt-ocr` checks in `docs/release/manual-validation.json` for commit `82d7f3284ea3457139e332c7cafb77a037ce3d52` or the final release commit.
Regression test: Keep deterministic browser/OCR automation in `scripts/check.py`; add the real-device evidence record to the manual release gate rather than faking the hardware path in automated tests.

[MEDIUM] R-002 — Published image signature proof is not available yet
Evidence status: confirmed
Location: `scripts/manual_release_evidence.py:24`, `scripts/manual_release_evidence.py:25`; `docs/handoff/IMPLEMENTATION_STATUS.md:113`; `docs/release/RELEASE_READINESS.md:74`
Acceptance criteria: I4, I5, J8
Impact: Supply-chain lock/SBOM/signing policy exists, but the final published image signature verification is a release-tag artifact. Without it, the release cannot prove the container image users install is the reviewed, hardened build.
Evidence/reproduction: `.\.venv\Scripts\python.exe scripts\manual_release_evidence.py --json` reports missing `docs/release/manual-validation.json`; required checks include `published-image-signature`. Release readiness/status also state that published-image signature verification remains a release-tag step.
Expected invariant: The final release records image reference, digest, tag, verification command/result, and local evidence artifact proving the published image signature.
Recommended remediation: After final tag/image publication, run the documented signature verification and record a PASS `published-image-signature` entry in `docs/release/manual-validation.json`.
Regression test: Keep `scripts/supply_chain_audit.py` for pre-publish static evidence and require the manual signature check before readiness can become PASS.

[MEDIUM] R-003 — Release readiness remains NOT READY
Evidence status: confirmed
Location: `docs/release/RELEASE_READINESS.md:7`, `docs/release/RELEASE_READINESS.md:28`, `docs/release/RELEASE_READINESS.md:37`, `docs/release/RELEASE_READINESS.md:38`, `scripts/release_readiness.py:121`, `scripts/release_readiness.py:144`
Acceptance criteria: J7, J8
Impact: The release gate cannot pass while the readiness artifact itself records open phase gates, pending manual evidence, and missing PASS status. This is intentionally release-blocking, not a stale false negative.
Evidence/reproduction: `.\.venv\Scripts\python.exe scripts\release_readiness.py --json` returned `decision=NOT READY`, with open phase gates including `Independent full review completed` and `Release gate PASS`. The required release command list includes `python scripts/manual_release_evidence.py`, which currently fails because the manual evidence file is missing.
Expected invariant: `docs/release/RELEASE_READINESS.md` records exact commands, results, residual risks, and a justified `PASS` only after every release-blocking finding is closed.
Recommended remediation: Complete and validate the manual evidence JSON, rerun all required release commands, regenerate readiness with `python scripts/release_readiness.py --write`, and perform one final independent review that reports no Critical/High or release-blocking Medium findings.
Regression test: Keep `scripts/check.py` enforcing readiness freshness and `scripts/release_readiness.py --check` in the required release command set.

## Acceptance-criteria coverage matrix

| Area | Refresh result |
|---|---|
| A. One source of truth and durability | No new blocker found in this focused refresh. `scripts/check.py` and API concurrency smoke passed; HA live smoke proved current Core-to-HA state propagation in isolated live Core/HA. |
| B. Products, locations, lots, and events | No new blocker found in this focused refresh; not exhaustively re-audited beyond current `scripts/check.py` and readiness/status evidence. |
| C. Units and recipe intelligence | No new blocker found in this focused refresh; not exhaustively re-audited beyond current `scripts/check.py` and readiness/status evidence. |
| D. Meal planning and shopping | No new blocker found in this focused refresh; not exhaustively re-audited beyond current `scripts/check.py` and readiness/status evidence. |
| E. Cooking, leftovers, waste, and value | No new blocker found in this focused refresh; not exhaustively re-audited beyond current `scripts/check.py` and readiness/status evidence. |
| F. Barcode, receipt, and price workflows | Release-blocking Medium remains for physical barcode camera and representative real-receipt manual evidence. |
| G. Web/PWA quality | Release-blocking Medium remains where physical-device camera evidence intersects browser workflow acceptance. |
| H. Home Assistant | Prior High stale-sensor smoke finding is resolved on current HEAD. Live smoke passed with HA entity states matching final Core revision/count and cleaned generated resources. |
| I. Security and operations | Prior independent container-smoke limitation is resolved by `--isolated`. Published-image signature proof remains a release-blocking Medium. |
| J. Engineering and release | `scripts/check.py` passed and readiness is current, but J7/J8 cannot pass while this review reports release-blocking Medium findings and readiness remains `NOT READY`. |

## Security and data-integrity conclusion

No open Critical or High finding was identified in this focused current-head refresh. The HA cross-interface false-pass issue is resolved by current source and live output, and the isolated container smoke now independently verifies container hardening, restart persistence, backup/restore counts, and in-image checks without mutating the existing Compose service/database.

The release remains blocked by missing external evidence and final publication evidence, not by a newly observed application data-integrity regression in this pass.

## Test and documentation gaps

- `docs/release/manual-validation.json` is absent and therefore cannot prove physical camera, representative real-receipt, published image signature, or final independent full-review evidence.
- `docs/release/RELEASE_READINESS.md` is current but intentionally `NOT READY`.
- A final readiness PASS and final no-blocker independent review must be produced after the manual evidence and image-signature records exist.

## Residual risks and recommended next action

1. Produce the manual evidence artifacts and `docs/release/manual-validation.json` for the final release commit.
2. Publish/tag the final image and record signature verification evidence.
3. Rerun the full required release command list from `docs/release/RELEASE_READINESS.md`.
4. Regenerate `docs/release/RELEASE_READINESS.md` and repeat the independent review.

## J7 assessment

J7 cannot pass for current HEAD. This review has no open Critical/High findings, but it does report release-blocking Medium findings, and J7 requires an independent review with no open Critical/High findings and no release-blocking Medium finding.
