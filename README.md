# PantryOS

PantryOS is a home food intelligence system with two surfaces:

- A runnable local kitchen dashboard in `app/` for the v1 vertical slice.
- A Home Assistant custom integration in `custom_components/pantryos` for sensors, services, and automation.

Home Assistant is intended to be one interface into the food database, not the database itself. PantryOS Core owns the local SQLite database. The Home Assistant integration uses the authenticated Core API. The browser dashboard uses same-site session cookies and CSRF-protected `/api/*` compatibility routes backed by the same Core.

## V1 Vertical Slice

The local app covers the end-to-end loop that makes the product useful:

- See tonight's planned meal, use-soon food, quick meals, shopping, and inventory on one kitchen dashboard.
- Add food with quantity, unit, location, expiration, minimum stock, cost, and leftover flag.
- Type a barcode to add a known package or create a manual barcode mapping for an unknown package.
- Open, consume, move, or discard inventory lots while preserving product-level settings.
- Add recipes from simple comma-separated ingredient rows.
- Plan a recipe for tonight.
- Compare recipes against current inventory.
- Add missing recipe ingredients to the shopping list.
- Check, uncheck, remove, and complete shopping purchases into inventory lots.
- Upload structured receipt text or supported receipt images, review extracted lines, commit purchases into lots, and inspect per-product price history.
- Start cooking from tonight's planned meal, confirm lot allocations, complete cooking, and create leftovers.
- Keep suggested purchases separate from the shopping list until explicitly promoted.
- Persist state to `data/pantryos.sqlite3`, with one-time import support for legacy `data/pantryos.json`.

Run it with the bundled or system Python:

```powershell
$env:PANTRYOS_API_TOKEN = "replace-with-a-long-local-token"
python app/server.py
```

Then open:

```text
http://127.0.0.1:8765
```

The dashboard imports legacy JSON on first startup when available and can seed demo data when the SQLite database is empty. Sign in with `PANTRYOS_API_TOKEN` to start the local browser session. You can also use **Seed Demo** or **Reset Demo** from the UI after signing in.

The browser shell includes PWA install metadata at `/manifest.webmanifest`, an SVG app icon at `/icon.svg`, and a service worker at `/service-worker.js`. The service worker caches only the app shell and static assets. API requests remain network-only; if PantryOS Core is offline, the service worker returns a `503 offline` problem response stating that the request was not committed. Offline writes are never queued or replayed.

## Development Gates

Create a local development environment and run the v1 quality gates with Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src custom_components\pantryos
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m coverage run -m pytest -q
.\.venv\Scripts\python.exe -m coverage report
```

Coverage is configured to include subprocesses so CLI smoke tests executed from pytest count toward the measured gate. The current release threshold is 80% total coverage.
## Docker

Build and run the v1 app with Docker Compose. Set `PANTRYOS_API_TOKEN` in your shell or copy `.env.example` to `.env` and replace the placeholder first:

```powershell
$env:PANTRYOS_API_TOKEN = "replace-with-a-long-local-token"
docker compose up --build
```

Then open:

```text
http://127.0.0.1:8765
```

The container includes the local `tesseract-ocr` binary for receipt image extraction and writes the SQLite database, private receipt uploads, migration backups, and backup archives to the named Docker volume `pantryos-data`. A named volume is used because Docker Desktop does not reliably bind-mount this UNC checkout as a Windows host path. The entrypoint repairs `/app/data` ownership for named volumes, then drops the PantryOS process to dedicated UID/GID `10001`; the Compose service runs with a read-only root filesystem, drops all Linux capabilities by default, adds only `CHOWN`/`SETGID`/`SETUID` for startup ownership repair and privilege drop, sets `pids_limit: 256`, uses a small `/tmp` tmpfs, `no-new-privileges`, `PANTRYOS_DATA_DIR=/app/data`, and `PANTRYOS_BACKUP_DIR=/app/data/backups`. In the container, CLI database paths must stay under `/app/data`, and backup/restore archive paths must stay under `/app/data/backups`. Change the host port with `PANTRYOS_PORT`:

```powershell
$env:PANTRYOS_PORT = "8770"
docker compose up --build
```

Run the dependency-free verifier in Docker:

```powershell
$env:PANTRYOS_API_TOKEN = "replace-with-a-long-local-token"
docker compose run --rm pantryos python scripts/check.py
```

Run the Docker release smoke after Docker Desktop is started:

```powershell
$env:PANTRYOS_API_TOKEN = "replace-with-a-long-local-token"
python scripts/container_smoke.py --isolated
```

The isolated container smoke builds and starts the Compose service in a temporary Compose project with its own host port, container name, and Docker volume, waits for readiness, verifies the non-root hardened runtime, proves bearer-token auth, mutates inventory and receipt state over the live API, restarts against the same generated volume, creates a receipt-inclusive backup archive, restores it into a second database inside `/app/data`, compares source/restored counts, runs the dependency-free verifier inside the image, and removes its generated Docker resources before exit.

Run the deterministic receipt OCR corpus smoke against the running PantryOS container:

```powershell
python scripts/receipt_ocr_corpus_smoke.py
```

The OCR corpus smoke generates multiple receipt images inside the container with `text2image`, extracts them through the same local `tesseract` boundary PantryOS uses for image receipts, parses the OCR text through PantryOS Core, and commits the result into a temporary SQLite database. It does not mutate your PantryOS database.

Run the image/container hardening audit when Docker Desktop is started:

```powershell
$env:PANTRYOS_API_TOKEN = "replace-with-a-long-local-token"
python scripts/image_hardening_audit.py
```

Use `python scripts/image_hardening_audit.py --skip-live` for a static Dockerfile, `.dockerignore`, and rendered Compose audit without inspecting a running container. The live audit additionally verifies the image has no baked API token, the container is healthy and not privileged, the root filesystem is read-only, only `/app/data` is writable, and the PantryOS process is UID/GID `10001` with no effective Linux capabilities after startup.

Generate or verify the container supply-chain lock and SPDX-style SBOM after rebuilding the release candidate image:

```powershell
python scripts/supply_chain_audit.py --write
python scripts/supply_chain_audit.py
```

The supply-chain audit enforces the digest-pinned base image, `docs/release/container-image.lock.json`, `docs/release/pantryos-image-sbom.spdx.json`, required `tesseract-ocr` package inventory, and the release signing policy in `docs/release/SUPPLY_CHAIN.md`.

Audit release-critical artifacts for J5 release debt markers:

```powershell
python scripts/release_artifact_audit.py
```

Every intentional match is path-aware and must carry a non-blocking reason; unexpected matches fail `python scripts/check.py`.

Generate the manual evidence scaffold, then validate final manual release evidence before tagging v1.0:

```powershell
python scripts/manual_release_evidence.py --write-template --commit <release-candidate-sha>
python scripts/manual_release_evidence.py --commit <release-candidate-sha>
```

`--write-template` writes `docs/release/manual-validation.template.json` for the current Git commit, or for an explicit `--commit <release-candidate-sha>`, with every required check and field present. It is intentionally incomplete and guarded against being written over the real evidence file. After physical-device checks, image signing, and final review are complete, save concrete `PASS` records as `docs/release/manual-validation.json`, track that JSON file in git, ensure the tracked file is clean in the validating checkout, and run the validator. The validator requires `PASS` records for exactly `physical-barcode-camera`, `real-receipt-ocr`, `published-image-signature`, and `independent-full-review`; no extra check IDs are accepted. The file must stay on the generated schema with no ignored extra root, check, detail, or evidence fields. Each record must include an operator, UTC timestamp, the exact acceptance IDs generated for that check, concrete environment or release details, a summary, and git-tracked local evidence artifact paths under `docs/release/evidence/` that are clean in the validating checkout. Physical barcode evidence must include an `http` or `https` app URL, a known barcode result that resolved a product or lot, a different unknown barcode, and the manual fallback result. Real receipt evidence must include `receipt_` and `purchase_` identifiers, a positive committed lot count, and a price-history result. Signature evidence must include a `sha256:<digest>` image digest, a published image reference containing that digest, a signature identity, and the exact `cosign verify` command that passed against that same published image reference, digest, and identity. Independent review evidence must point at an existing git-tracked review artifact under `docs/reviews/` that is clean in the validating checkout for the same target release commit, and that artifact must mention the reviewed commit and include machine-readable `decision=PASS`, `open_critical_high=0`, and `release_blocking_medium=0` markers. The command exits nonzero until those external checks are recorded.

## Home Assistant Integration

Copy `custom_components/pantryos` into your Home Assistant config directory:

```text
<config>/custom_components/pantryos
```

Restart Home Assistant, then go to **Settings > Devices & services > Add integration** and search for `PantryOS`. Enter the PantryOS Core URL, for example `http://127.0.0.1:8765`, and the same `PANTRYOS_API_TOKEN` used by the Core server. The integration supports Home Assistant reconfigure and reauth flows for URL/token changes; authentication failures during setup are reported as credential failures rather than generic connection retries.

### Main Entities

The integration exposes these sensors:

- `sensor.pantryos_total_items`
- `sensor.pantryos_expiring_soon`
- `sensor.pantryos_shopping_list_count`
- `sensor.pantryos_leftover_count`
- `sensor.pantryos_state_revision`
- `sensor.pantryos_suggested_purchases`
- `sensor.pantryos_possible_meals`
- `sensor.pantryos_food_waste_this_month`
- `sensor.pantryos_kitchen_items`
- `sensor.pantryos_refrigerator_items`
- `sensor.pantryos_freezer_items`
- `sensor.pantryos_pantry_items`
- `sensor.pantryos_kitchen_value`
- `sensor.pantryos_refrigerator_value`
- `sensor.pantryos_freezer_value`
- `sensor.pantryos_pantry_value`

### Service Surface

The integration registers API-backed actions for `add_item`, `consume_item`, `delete_item` as a compatibility discard action, `discard_item`, `move_item`, `open_item`, `add_recipe`, `plan_meal`, `add_shopping_item`, `add_missing_to_shopping_list`, `rebuild_shopping`, `promote_suggested_purchases`, `start_cooking`, `complete_cooking`, and `cancel_cooking`.

Example automations for use-soon notifications, grocery arrival counts, cooking mode, and freezer risk/value alerts are in `docs/home_assistant/example_automations.yaml`. PantryOS runs a background Home Assistant event-stream subscription and fires a `pantryos_updated` Home Assistant event with the latest bounded PantryOS event metadata so automations can react to events such as `cooking.started`. If the stream is interrupted, the coordinator falls back to the authenticated event audit and snapshot refresh path.

Run the installed Home Assistant smoke when Docker is available:

```powershell
python scripts/ha_installed_smoke.py
```

The smoke uses `ghcr.io/home-assistant/home-assistant:stable` by default, or `PANTRYOS_HA_IMAGE` when set. It creates a temporary Home Assistant config directory, copies `custom_components/pantryos`, imports the integration inside the installed Home Assistant Python environment, and exercises setup, background event-stream polling, service schemas/handlers, unload cancellation, and auth-failure recovery against a fake PantryOS client. It does not mutate your PantryOS database.

Run the live Home Assistant Core smoke after building the PantryOS Docker image:

```powershell
python scripts/ha_core_live_smoke.py
```

The live smoke uses the same Home Assistant image and `PANTRYOS_HA_IMAGE` override. It creates a disposable Docker network, starts an isolated PantryOS Core container from `pantryos-pantryos:latest` or `PANTRYOS_CORE_IMAGE`, uses a fixed non-secret smoke token only on that private network, creates the PantryOS entry through Home Assistant's config-flow manager against the live Core API, registers sensors/services, calls `pantryos.add_item`, then writes a direct Core API item and waits for the Home Assistant event-stream listener to advance the coordinator revision. The disposable Core container, network, and volume are removed before the command exits, and your running PantryOS database is not mutated.

### Example Service Calls

Add chicken to the garage freezer:

```yaml
service: pantryos.add_item
data:
  name: Chicken Breast
  quantity: 3
  unit: lb
  location: Garage/Chest Freezer
  purchased: "2026-08-21"
  expires: "2026-09-02"
  minimum_stock: 1
  estimated_cost: 12.50
```

Add missing ingredients to the shopping list:

```yaml
service: pantryos.add_missing_to_shopping_list
data:
  recipe_name: Chicken Alfredo
```

## API

The versioned API requires `Authorization: Bearer <PANTRYOS_API_TOKEN>` except for health checks. Errors use a stable problem shape with `type`, `title`, `status`, `code`, `detail`, `errors`, and `request_id`. The current OpenAPI 3.1 document is served at authenticated endpoint `GET /api/v1/openapi.json`; its success responses use concrete per-resource schemas for dashboard, inventory lots, events, locations, purchases, prices, shopping, receipts, cooking sessions, leftovers, and mutation envelopes rather than generic object placeholders.

The browser signs in through `POST /api/session/login` with the same local setup token. The sign-in panel reads `GET /api/session` first, reports whether `PANTRYOS_API_TOKEN` is configured, keeps token entry disabled when the server is not configured, and shows the active cookie policy. Successful login creates a file-backed `pantryos_session` cookie with `HttpOnly`, `SameSite=Lax`, `Path=/`, a default `Max-Age` of `43,200` seconds, and returns a per-session CSRF token. Sessions persist across PantryOS restarts in `browser_sessions.json` beside the SQLite database by default; set `PANTRYOS_BROWSER_SESSION_STORE=memory` to force in-memory sessions, or set it to a file path for a custom store. Browser compatibility routes under `/api/*` require that cookie; unsafe methods also require `X-CSRF-Token`. CORS does not use wildcards: preflight and browser-origin checks only echo the current same-origin `Origin`. The browser session TTL can be overridden with `PANTRYOS_BROWSER_SESSION_SECONDS`. For HTTPS or reverse-proxy deployments, set `PANTRYOS_BROWSER_SECURE_COOKIES=true` to add the `Secure` cookie attribute; PantryOS also reports secure-cookie mode when `X-Forwarded-Proto: https` is present.

Non-empty JSON request bodies must use `Content-Type: application/json` or a `+json` media type and are limited to `1,000,000` bytes. Receipt uploads support reviewed local text and image ingestion: `text/plain` `.txt` and `text/csv` `.csv` up to `64,000` UTF-8 bytes, plus `image/png` `.png` and `image/jpeg` `.jpg`/`.jpeg` through JSON `content_base64` up to `750,000` decoded bytes, `6,000px` per edge, and `16,000,000` pixels. Receipt filenames must be basenames, not paths; stored receipt payloads live under the data directory's private `receipts/` folder and are not served from `app/static` or returned by API responses. Image receipt extraction runs the local `tesseract` CLI with a 15-second timeout and returns a clear validation error when OCR is unavailable or unreadable; extracted data still requires review before inventory mutation. Receipt upload and extraction endpoints have a fixed-window local rate limit of `20` requests per `60` seconds per client by default; override with `PANTRYOS_RATE_LIMIT_REQUESTS` and `PANTRYOS_RATE_LIMIT_WINDOW_SECONDS`. Receipt retention is explicit: `purge-receipts` deletes private payload files for old `uploaded`, `review`, or `rejected` receipt uploads, marks their metadata `purged`, and keeps committed receipt payloads unless a future household-data deletion operation removes the purchase evidence.

Current versioned endpoints:

- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`
- `GET /api/v1/openapi.json`
- `GET /api/v1/instance`
- `GET /api/v1/dashboard`
- `GET /api/v1/events`
- `GET /api/v1/events/{id}`
- `GET /api/v1/inventory/events`
- `GET /api/v1/barcodes/{barcode}`
- `GET /api/v1/locations/summary`
- `GET /api/v1/waste/monthly`
- `GET /api/v1/purchases`
- `GET /api/v1/purchases/{id}`
- `GET /api/v1/products/{id}/prices`
- `POST /api/v1/inventory/lots`
- `POST /api/v1/inventory/lots/{id}/consume`
- `POST /api/v1/inventory/lots/{id}/move`
- `POST /api/v1/inventory/lots/{id}/open`
- `POST /api/v1/inventory/lots/{id}/discard`
- `POST /api/v1/barcodes/mappings`
- `POST /api/v1/barcodes/{barcode}/add-lot`
- `POST /api/v1/receipts`
- `POST /api/v1/receipts/{id}/extract`
- `GET /api/v1/receipts/{id}/review`
- `PATCH /api/v1/receipts/{id}/review`
- `POST /api/v1/receipts/{id}/commit`
- `POST /api/v1/receipts/{id}/reject`
- `POST /api/v1/recipes`
- `POST /api/v1/recipes/{recipe_name}/shopping`
- `POST /api/v1/meal-plan`
- `POST /api/v1/shopping/rebuild`
- `POST /api/v1/shopping/manual`
- `PATCH /api/v1/shopping/{id}`
- `DELETE /api/v1/shopping/{id}`
- `POST /api/v1/shopping/{id}/check`
- `POST /api/v1/shopping/{id}/uncheck`
- `POST /api/v1/shopping/complete-purchase`
- `POST /api/v1/shopping/promote-suggestions`
- `POST /api/v1/cooking/sessions`
- `GET /api/v1/cooking/sessions/{id}`
- `POST /api/v1/cooking/sessions/{id}/complete`
- `POST /api/v1/cooking/sessions/{id}/cancel`
- `GET /api/v1/leftovers`

The browser uses these session-protected compatibility routes:

- `GET /api/session`
- `POST /api/session/login`
- `POST /api/session/logout`
- `GET /api/state`
- `POST /api/seed?reset=true`
- `POST /api/items`
- `POST /api/items/{id}/consume`
- `POST /api/items/{id}/move`
- `POST /api/items/{id}/open`
- `DELETE /api/items/{id}`
- `GET /api/barcodes/{barcode}`
- `POST /api/barcodes/mappings`
- `POST /api/barcodes/{barcode}/add-lot`
- `POST /api/receipts`
- `POST /api/receipts/{id}/extract`
- `GET /api/receipts/{id}/review`
- `PATCH /api/receipts/{id}/review`
- `POST /api/receipts/{id}/commit`
- `POST /api/receipts/{id}/reject`
- `GET /api/purchases`
- `GET /api/purchases/{id}`
- `POST /api/recipes`
- `POST /api/recipes/{recipe_name}/shopping`
- `POST /api/shopping`
- `POST /api/shopping/{id}/check`
- `POST /api/shopping/{id}/uncheck`
- `DELETE /api/shopping/{id}`
- `POST /api/shopping/complete-purchase`
- `POST /api/shopping/promote-suggestions`
- `POST /api/shopping/rebuild`
- `POST /api/meal-plan`
- `POST /api/cooking/sessions`
- `POST /api/cooking/sessions/{id}/complete`
- `POST /api/cooking/sessions/{id}/cancel`

## Operations

The local operations CLI runs from a checkout or from the installed `pantryos` console entry point:

```powershell
python scripts/pantryos.py --db data/pantryos.sqlite3 doctor
python scripts/pantryos.py --db data/pantryos.sqlite3 backup --output backups/pantryos.sqlite3
python scripts/pantryos.py --db data/pantryos.sqlite3 backup --output backups/pantryos.zip
python scripts/pantryos.py --db data/pantryos.sqlite3 restore --input backups/pantryos.sqlite3 --verify
python scripts/pantryos.py --db data/pantryos.sqlite3 restore --input backups/pantryos.zip --verify

# inside the Compose container, use the mounted data volume paths
python scripts/pantryos.py --db /app/data/pantryos.sqlite3 backup --output /app/data/backups/pantryos.zip
python scripts/pantryos.py --db /app/data/pantryos.sqlite3 restore --input /app/data/backups/pantryos.zip --verify
python scripts/pantryos.py --db data/pantryos.sqlite3 import-legacy --path data/pantryos.json --dry-run
```

`backup --output *.sqlite3` writes a SQLite backup plus a `.sha256.json` manifest. `backup --output *.zip` writes an upload-inclusive archive with `pantryos.sqlite3`, non-purged private `receipts/*` payloads, and `manifest.json` checksums. `restore` verifies the backup before replacing the target database; `.zip` restores also rewrite receipt storage paths for the destination data directory and restore receipt payload files. `purge-receipts` defaults to uncommitted receipt uploads older than 30 days; use `--dry-run` first to inspect eligible rows, and repeat `--status uploaded|review|rejected` to limit cleanup. Purged receipts cannot be extracted or committed until the same payload is uploaded again, which reactivates the existing metadata row. When an existing database has pending migrations, PantryOS writes a pre-migration SQLite backup under `data/backups/migrations/`; if a migration fails, it restores that backup after saving a `.failed` copy for inspection. `import-legacy --dry-run` validates and summarizes legacy JSON without creating or mutating the target database.

The server writes structured JSON request logs to stdout. Each line includes `event`, `request_id`, `method`, `path`, `status`, `duration_ms`, and `client`. Request bodies, authorization headers, cookies, CSRF tokens, receipt contents, and browser session values are not logged.

## Development

The authoritative inventory engine lives in `src/pantryos` and is exposed through the local Core API. The older pure inventory engine in `custom_components/pantryos/inventory.py` is retained temporarily as baseline coverage for the original proof of concept.

Run tests when `pytest` is installed:

```powershell
python -m pytest
```

In the Codex bundled Python runtime, `pytest` may not be installed. The dependency-light verifier still compiles Python, runs the smoke test module directly, and checks browser JavaScript syntax when Node is available:

```powershell
python scripts/check.py
```

Run the browser viewport/accessibility smoke with Playwright available to Node and a Chromium browser installed for that Playwright runtime. The smoke starts a temporary PantryOS server, blocks service workers for online workflow checks, runs phone and kitchen-tablet viewports, exercises the primary browser workflows, and audits login/app/completed states for landmarks, labels, ARIA references, media labels, live regions, focusable target sizing, focus styling, duplicate IDs, and text contrast:

```powershell
$env:PYTHON = "C:\Users\Kronus\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$env:PANTRYOS_API_TOKEN = "browser-smoke-token"
& "C:\Users\Kronus\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" scripts\browser_smoke.cjs
```

For a normal Node toolchain, install Playwright and Chromium first, then run `node scripts/browser_smoke.cjs` with `PYTHON` pointing at the Python runtime to use for `app/server.py`.

Run the scripted add-to-use-soon cross-surface release smoke:

```powershell
$env:PANTRYOS_API_TOKEN = "e2e-smoke-token"
python scripts/smoke_e2e.py
```

Run the authenticated API concurrency smoke for the A6 durability gate. It starts a temporary local server, launches 20 concurrent add/open/consume/discard mutations, checks the final revision/event counts, and runs SQLite integrity verification:

```powershell
python scripts/concurrency_smoke.py
```

Generate or check the release-readiness ledger:

```powershell
python scripts/release_readiness.py --write
python scripts/release_readiness.py --check
```

The readiness document is written to `docs/release/RELEASE_READINESS.md` and remains `NOT READY` until all phase gates and implementation-status blockers are cleared.
