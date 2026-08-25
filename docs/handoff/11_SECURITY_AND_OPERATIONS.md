# 11. Security, privacy, and operations

## Threat model

PantryOS is local-first but should not assume every browser, IoT device, or process on the LAN is trusted. The system accepts state-changing requests, camera-derived barcodes, receipt uploads, and Home Assistant tokens. Primary risks are unauthorized mutation, data loss, malicious uploads, secret leakage, and unsafe upgrades.

## Authentication and sessions

- Require a long-lived scoped API token for Home Assistant and non-browser clients.
- Store only token hashes where the server owns token issuance.
- Provide token create/revoke/rotate behavior or a documented environment-secret model.
- Browser access should use an authenticated same-site session or another explicit secure mechanism; do not expose a reusable HA token to JavaScript local storage.
- Bind to loopback by default for direct runs. A non-loopback/container deployment must require completed setup/authentication.
- Use constant-time token comparison and bounded authentication failures.

## Browser request controls

- Default CORS to same origin; add no wildcard credential policy.
- Protect cookie-authenticated mutations against CSRF.
- Validate `Origin`/`Host` where appropriate for local deployment.
- Use `HttpOnly`, `SameSite`, and `Secure` cookie attributes according to deployment/TLS mode.
- Apply a Content Security Policy compatible with the bundled frontend.
- Avoid inline unescaped user content and add browser XSS tests for names, IDs, notes, and receipt text.

## API and input controls

- Set global and route-specific body limits.
- Validate decimals, date ranges, units, UUIDs, and enum values before application logic.
- Return structured 4xx errors for malformed JSON and invalid media types.
- Add rate limits or bounded concurrency for expensive extraction/upload endpoints.
- Parameterize all SQL through the data layer.
- Treat idempotency keys as scoped to an authenticated principal and request fingerprint.

## Receipt upload safety

- Allow only documented image/text-based receipt formats needed by the implementation.
- Validate content, not only filename extension or client MIME.
- Generate server-side storage names; never concatenate user filenames into paths.
- Store uploads outside the static asset tree.
- Set size, dimensions/page count, and processing timeout limits.
- Run OCR/parsing in a constrained subprocess or worker boundary where practical.
- Do not execute embedded scripts, macros, or active document content.
- Delete rejected/expired uploads according to documented retention.
- Never include raw receipt content in routine logs or HA diagnostics.

## Data privacy

- No telemetry by default.
- Core operation must remain usable offline.
- External barcode/receipt enrichment is opt-in and documents what data leaves the network.
- Provide export and deletion behavior for household data.
- Logs should contain IDs and categories, not full notes, receipt text, or tokens.

## Database and migrations

- Enable foreign keys on every connection.
- Configure busy timeout and test concurrency behavior.
- Use WAL only after verifying the deployment filesystem supports it safely.
- Back up before automatic schema migration.
- Apply migrations under an exclusive startup/upgrade lock so two instances cannot migrate simultaneously.
- On migration failure, refuse readiness and preserve the prior database/backup.
- Provide a doctor/integrity command and document corruption recovery.

## Backup and restore

A backup contains:

- SQLite database captured with a consistent mechanism;
- schema/application version metadata;
- optional receipt/upload files when requested;
- checksum manifest.

Restore behavior:

1. validate format, checksums, and compatible version;
2. restore into a temporary location;
3. run database integrity and migration checks;
4. atomically swap only after validation;
5. preserve the previous data as a rollback backup.

Automated tests must compare key counts, quantities, events, purchases, and revision before and after restore.

## Container hardening

- Run as a dedicated non-root UID/GID.
- Use a minimal pinned base image and a lock file.
- Write only to configured data/upload/backup paths.
- Do not bake secrets or real data into image layers.
- Provide liveness/readiness health checks.
- Use a multi-stage build when frontend or native OCR build dependencies are needed.
- Document required system packages for local receipt extraction.

## Logging and observability

- Structured logs with timestamp, level, request/correlation ID, route, status, duration, and safe error code.
- Redact `Authorization`, cookies, API tokens, session IDs, and upload contents.
- Log migration start/result, backup/restore result, event stream reconnects, and failed extraction category.
- Do not suppress all request logging as the prototype does.
- Expose operational metrics only when they do not leak household content.

## Health semantics

- **Liveness:** process/event loop can respond.
- **Readiness:** migrations complete, database transaction works, required directories are writable, and core initialization succeeded.
- OCR/provider degradation should be a capability/status warning, not necessarily total unready state when core inventory remains usable.

## Recovery cases to document and test

- PantryOS restarts while HA remains running.
- HA restarts while PantryOS remains running.
- Event stream disconnects and misses events.
- Database is locked temporarily.
- Migration fails.
- Legacy JSON is malformed.
- Receipt extraction times out.
- Disk becomes full during upload or backup.
- Token is revoked or rotated.
- Restore targets a newer incompatible backup.
