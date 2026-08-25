# ADR 0001: Standard-library Core foundation for the Phase 1 extraction

## Status

Accepted for Phase 1 implementation.

## Context

The handoff recommends FastAPI, Pydantic v2, SQLAlchemy, and Alembic. The current proof of concept is dependency-free and already runnable in Docker. The immediate release-blocking defects are split-brain state and JSON lost updates. Closing those defects requires a transactional Core before expanding the feature surface.

## Decision

Implement the first PantryOS Core extraction with Python 3.12 standard-library components:

- `sqlite3` for the authoritative database;
- explicit versioned SQL migrations under `src/pantryos/migrations`;
- repository/application services in `src/pantryos`;
- the existing HTTP server as the temporary API host while `/api/v1` is introduced.

This does not remove the final requirement for a documented `/api/v1` contract, OpenAPI, authentication, Home Assistant API-client integration, and the full test matrix. It is a dependency-light stepping stone to close the data-loss architecture first.

## Alternatives Considered

1. Add FastAPI, SQLAlchemy, Alembic, and Pydantic immediately.
   - Stronger long-term API ergonomics.
   - Higher setup and migration cost before proving the one-source-of-truth database.

2. Keep the JSON repository until the full API stack is introduced.
   - Minimal short-term code churn.
   - Leaves the known lost-update defect live and violates the roadmap order.

## Consequences

- The SQLite schema and application invariants become the first stable Core boundary.
- Migrations are explicit SQL files rather than Alembic revisions in this phase.
- OpenAPI generation must be added or the API layer must migrate later before v1.0 release readiness can pass.
- Tests focus first on migrations, legacy import idempotency, and concurrent mutations.

## Security Impact

No external service or credential is introduced. SQLite access remains inside PantryOS Core. API authentication is still a Phase 2 blocker and must not be marked complete by this ADR.

## Test Plan

- Fresh database migration test.
- Legacy JSON dry-run/import test.
- Double legacy import idempotency test.
- Concurrent mutation test with at least 20 successful writes.
- Transaction rollback test for compound mutation plus event write.

## Rollback

The baseline checkpoint `98078e3` preserves the v0.1.0 proof of concept and handoff package. The old JSON app can be recovered from that commit if this extraction blocks progress.
