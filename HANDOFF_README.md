# PantryOS Codex Handoff

**Package date:** 2026-08-25  
**Baseline application version:** 0.1.0  
**Original source archive SHA-256:** `06a0979f982d734b6ff1f7cd8aa95cb2525c9bfb6b98397bd9477be9aade6896`

This repository is a runnable proof of concept plus a complete implementation brief for turning PantryOS into a reliable, local-first Home Food Intelligence system.

## Read this first

The prototype demonstrates the product loop, but it is not yet safe to treat as the household's source of truth. The web application and Home Assistant integration currently maintain different data stores, and the JSON repository can lose concurrent writes.

The first engineering milestone is therefore not another feature. It is:

> **PantryOS Core v0.2 — one transactional database, multiple interfaces.**

PantryOS Core owns the data. The web application, Home Assistant, scanners, and future clients consume the same authenticated API and event stream.

## Package layout

- `AGENTS.md` — durable repository instructions that Codex should load automatically.
- `docs/handoff/` — product scope, baseline evidence, architecture, data model, roadmap, acceptance criteria, test plan, and review rules.
- `codex/FULL_COMPLETION_GOAL.md` — paste into Codex Goal mode to implement the release.
- `codex/FULL_REVIEW_GOAL.md` — run in a separate session after implementation for an independent release review.
- `docs/handoff/evidence/` — source checksums, baseline verification output, and a deterministic JSON race reproducer.
- Existing `app/`, `custom_components/`, `tests/`, and container files — the original prototype source.

## Recommended Codex workflow

1. Open this `PantryOS` directory as the project root. Do not open only the `codex/` or `docs/` subdirectory.
2. Confirm that Codex has read `AGENTS.md`.
3. Run the baseline checks in `docs/handoff/02_BASELINE.md`.
4. Paste `codex/FULL_COMPLETION_GOAL.md` into `/goal` and let the implementation proceed through the phase gates.
5. Review the resulting changes and preserve a clean checkpoint.
6. Start a new Codex session or worktree and paste `codex/FULL_REVIEW_GOAL.md`. The reviewer should not be the implementation thread.
7. Resolve every release-blocking review finding and repeat the review until the release gate is `PASS`.

## Full-completion boundary

The v1.0 completion target includes:

- A single SQLite-backed PantryOS Core with migrations and legacy JSON import.
- Products, aliases/barcodes, hierarchical locations, inventory lots, and an immutable inventory event ledger.
- Reliable unit conversion, expiration-aware recipe matching, use-soon recommendations, meal planning, idempotent shopping demand, leftovers, and cooking sessions.
- A responsive web/PWA experience for all core workflows.
- Barcode scanning with a manual fallback and persistent local product mapping.
- Receipt ingestion with at least one concrete local extraction path, a mandatory review screen, and purchase/price history.
- A Home Assistant integration that connects to PantryOS Core rather than storing a second inventory.
- Authentication, request limits, safe uploads, backups, restore validation, structured logging, health checks, and release documentation.
- Automated unit, integration, migration, Home Assistant, browser, concurrency, and container smoke tests.

The explicit non-goals are in `docs/handoff/13_DECISIONS_AND_NON_GOALS.md`. Multi-household cloud sync, live retailer scraping, and autonomous appliance control are not required for v1.0.

## Baseline result

The original dependency-free test runner passes all 11 tests. Python compilation and JavaScript syntax checks also pass. A forced two-writer test demonstrates a silent lost update in the JSON repository: both writes return successfully, but only one item remains.

That baseline is preserved in `docs/handoff/evidence/`.
