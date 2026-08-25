# 13. Fixed decisions, allowed alternatives, and non-goals

## Fixed decisions

These decisions define the product boundary. A code-level variation is allowed only when it preserves the decision and is documented.

1. **PantryOS Core owns all authoritative data.**
2. **SQLite is the v1.0 database.** It is appropriate for one household and simplifies local deployment.
3. **The database is accessed only by PantryOS Core.**
4. **Web and Home Assistant use a versioned authenticated API.**
5. **Products and inventory lots are separate concepts.**
6. **Locations are hierarchical records with stable IDs.**
7. **Inventory changes have an event ledger.**
8. **Quantities and money use decimal-safe representations.**
9. **Shopping generation is source-based and idempotent.**
10. **Receipt extraction is reviewed before commit.**
11. **Core workflows function without internet access.**
12. **Home Assistant controls the home; PantryOS emits food state/events.**
13. **Legacy JSON data has a safe import path.**
14. **No visible completion-critical control may be a stub.**

## Preferred but replaceable choices

A documented ADR may replace these choices when tests and operational guarantees remain equal or stronger:

- FastAPI/Pydantic for HTTP contracts.
- SQLAlchemy/Alembic for SQLite mapping and migrations.
- SSE for push updates, with polling recovery.
- Buildless modular JavaScript versus a small TypeScript frontend.
- Tesseract or another concrete local OCR implementation.

An ADR must state context, alternatives, tradeoffs, migration impact, security impact, test plan, and rollback path. “Developer preference” alone is not sufficient.

## Explicit non-goals for v1.0

- SaaS hosting, user registration, social features, or multi-household tenancy.
- Cloud synchronization between homes.
- Native iOS/Android applications; the PWA is the supported mobile surface.
- Live inventory from smart refrigerators or weight sensors.
- Dedicated scanner hardware drivers; keyboard-wedge/manual input may work naturally.
- Real-time retailer catalogs, scraping, coupons, or price predictions from external feeds.
- Automated ordering or checkout.
- Nutrition, calorie, allergen, or medical guidance.
- Autonomous AI recipe generation as a dependency for meal matching.
- Unreviewed OCR commits.
- Direct control of lights, displays, or appliances from PantryOS Core.
- General ERP/accounting features.

## Product policy defaults

- Single household and one administrative user/session model for v1.0.
- Suggested purchases require acceptance.
- Inventory consumption is confirmed and cannot silently go negative.
- Cooking completion proposes allocations and requires confirmation.
- Deleted records that affect history are soft-deleted or administratively corrected with an audit event.
- External data sharing is off by default.
- Unknown or ambiguous extraction/matching is shown to the user rather than guessed silently.
