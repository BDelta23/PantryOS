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

PantryOS Core is intended to run as a Docker service on a NAS or another always-on local Docker host. The container listens on port `8765`, stores all persistent state under `/data`, and should be reached from Home Assistant with the NAS LAN URL, for example `http://<NAS-LAN-IP>:8765`. Do not enter `127.0.0.1` in Home Assistant unless Home Assistant and PantryOS Core are running on the same host.

For local development from this checkout:

```powershell
$env:PANTRYOS_API_TOKEN = "replace-with-a-long-local-token"
docker compose up --build
```

Then open:

```text
http://127.0.0.1:8765
```

For a household NAS deployment, use `deploy/docker/`:

```powershell
cd deploy/docker
copy .env.example .env
# edit .env and set PANTRYOS_API_TOKEN to a long random value
docker compose up -d --build
```

The NAS compose example bind-mounts `./data` to `/data`. The root development `compose.yaml` uses the named volume `pantryos-data` at `/data`, which avoids Docker Desktop UNC bind-mount issues. In both cases, the SQLite database is `/data/pantryos.sqlite3`, and receipt uploads, browser sessions, migration backups, and backup archives stay under `/data` unless explicitly configured otherwise. Deleting and recreating the container must not delete the mounted data directory or named volume.

The container includes the local `tesseract-ocr` binary for receipt image extraction. The entrypoint repairs `/data` ownership for mounted volumes, then drops the PantryOS process to dedicated UID/GID `10001`; the Compose service runs with a read-only root filesystem, drops all Linux capabilities by default, adds only `CHOWN`/`SETGID`/`SETUID` for startup ownership repair and privilege drop, sets `pids_limit: 256`, uses a small `/tmp` tmpfs, and enables `no-new-privileges`.

Key Docker environment variables:

- `PANTRYOS_API_TOKEN`: required bearer token for API and browser sign-in.
- `PANTRYOS_LISTEN_HOST`: container listen host; use `0.0.0.0` for Docker/LAN access.
- `PANTRYOS_LISTEN_PORT`: container listen port; default `8765`.
- `PANTRYOS_PORT`: host-side port published by Compose; default `8765`.
- `PANTRYOS_DATA_DIR`: allowed persistent data directory; default `/data` in Docker.
- `PANTRYOS_DATABASE_PATH`: SQLite database path; default `/data/pantryos.sqlite3` in Docker.
- `PANTRYOS_BACKUP_DIR`: backup output directory; default `/data/backups` in Docker.

Confirm readiness:

```powershell
curl http://<NAS-LAN-IP>:8765/api/v1/health/ready
```

Expected response:

```json
{"status":"ready"}
```

Do not expose port `8765` directly to the public internet. PantryOS is designed for a trusted local network with token authentication and no required cloud dependency.

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

The isolated container smoke builds and starts the Compose service in a temporary Compose project with its own host port, container name, and Docker volume, waits for readiness, verifies the non-root hardened runtime, proves bearer-token auth, mutates inventory and receipt state over the live API, restarts against the same generated volume, creates a receipt-inclusive backup archive, restores it into a second database inside `/data`, compares source/restored counts, runs the dependency-free verifier inside the image, and removes its generated Docker resources before exit.

Run the deterministic receipt OCR corpus smoke against the running PantryOS container and a temporary SQLite database:

```powershell
python scripts/receipt_ocr_corpus_smoke.py
```

Run the image/container hardening audit when Docker Desktop is started:

```powershell
$env:PANTRYOS_API_TOKEN = "replace-with-a-long-local-token"
python scripts/image_hardening_audit.py
```

Use `python scripts/image_hardening_audit.py --skip-live` for a static Dockerfile, `.dockerignore`, and rendered Compose audit without inspecting a running container. The live audit additionally verifies the image has no baked API token, the container is healthy and not privileged, the root filesystem is read-only, only `/data` is writable, and the PantryOS process is UID/GID `10001` with no effective Linux capabilities after startup.

Generate or verify the container supply-chain lock and SPDX-style SBOM after rebuilding the release candidate image:

```powershell
python scripts/supply_chain_audit.py --write
python scripts/supply_chain_audit.py
```

## Updates and Backup

Update PantryOS Core independently from Home Assistant. For a published image, set `PANTRYOS_IMAGE` in `deploy/docker/.env` to an explicit tag such as `ghcr.io/<owner>/<repo>:0.1.0`, run `docker compose pull`, then run `docker compose up -d`. Keep the `/data` mount in place so the SQLite database and receipt payloads survive container recreation.

Back up the persistent PantryOS data directory. At minimum, protect `/data/pantryos.sqlite3` plus `/data/receipts/` if receipt uploads are used. The CLI can also produce a receipt-inclusive archive:

```powershell
python scripts/pantryos.py --db /data/pantryos.sqlite3 backup --output /data/backups/pantryos.zip
python scripts/pantryos.py --db /data/pantryos.sqlite3 restore --input /data/backups/pantryos.zip --verify
```

## Home Assistant Integration

Install the PantryOS Home Assistant integration through HACS as a custom integration repository after the repository is published on GitHub:

1. In Home Assistant, open HACS.
2. Open **Custom repositories**.
3. Enter the PantryOS GitHub repository URL.
4. Select repository type **Integration**.
5. Add the repository, install PantryOS, then restart Home Assistant when HACS asks.
6. Go to **Settings > Devices & services > Add integration**.
7. Search for `PantryOS`.
8. Enter the NAS PantryOS Core URL, such as `http://<NAS-LAN-IP>:8765`, and the same API token configured in Docker.

No SSH, SD-card access, Samba copying, or manual copying into `/config/custom_components` is required for the HACS path. The integration validates the URL and token during setup, supports reconfiguration for a moved NAS address, and supports reauthentication for token rotation.

### HACS Release Path

Future integration releases should use semantic version tags such as `0.1.1`, `0.2.0`, or `1.0.0` when maturity supports it. The expected maintainer path is commit, tag/release on GitHub, let HACS detect the update, update from HACS, then restart or reload Home Assistant as required. Core container releases are separate and should use explicit Docker image tags.

### Main Entities

The integration exposes these 16 sensors:

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

### Action Surface

The integration registers 15 API-backed Home Assistant actions: `add_item`, `consume_item`, `delete_item` as a compatibility discard action, `discard_item`, `move_item`, `open_item`, `add_recipe`, `plan_meal`, `add_shopping_item`, `add_missing_to_shopping_list`, `rebuild_shopping`, `promote_suggested_purchases`, `start_cooking`, `complete_cooking`, and `cancel_cooking`.

Example automations for use-soon notifications, grocery arrival counts, cooking mode, and freezer risk/value alerts are in `docs/home_assistant/example_automations.yaml`. PantryOS runs a background Home Assistant event-stream subscription and fires a `pantryos_updated` Home Assistant event with the latest bounded PantryOS event metadata so automations can react to events such as `cooking.started`. Sensors do not poll individually; they read the shared coordinator snapshot. If the stream is interrupted, the coordinator falls back to the authenticated event audit and snapshot refresh path, then reconnects without requiring the integration to be deleted and recreated.

Run the installed Home Assistant smoke when Docker is available:

```powershell
python scripts/ha_installed_smoke.py
```

The smoke uses `ghcr.io/home-assistant/home-assistant:stable` by default, or `PANTRYOS_HA_IMAGE` when set. It creates a temporary Home Assistant config directory, copies `custom_components/pantryos`, imports the integration inside the installed Home Assistant Python environment, and exercises setup, background event-stream updates, service schemas/handlers, unload cancellation, and auth-failure recovery against a fake PantryOS client. It uses a temporary SQLite database and does not mutate your PantryOS database.

Run the live Home Assistant Core smoke after building the PantryOS Docker image:

```powershell
python scripts/ha_core_live_smoke.py
```

The live smoke uses the same Home Assistant image and `PANTRYOS_HA_IMAGE` override. It creates a disposable Docker network, starts an isolated PantryOS Core container from `pantryos-pantryos:latest` or `PANTRYOS_CORE_IMAGE`, uses a fixed non-secret smoke token only on that private network, creates the PantryOS entry through Home Assistant's config-flow manager against the live Core API, registers sensors/actions, calls `pantryos.add_item`, then writes a direct Core API item and waits for the Home Assistant event-stream listener to advance the coordinator revision. The disposable Core container, network, and volume are removed before the command exits, and your running PantryOS database is not mutated.

### Example Action Calls

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
python scripts/pantryos.py --db /data/pantryos.sqlite3 backup --output /data/backups/pantryos.zip
python scripts/pantryos.py --db /data/pantryos.sqlite3 restore --input /data/backups/pantryos.zip --verify
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

The readiness document is written to `docs/release/RELEASE_READINESS.md` and remains `NOT READY` until every acceptance criterion in `docs/handoff/08_ACCEPTANCE_CRITERIA.md` is checked off, all phase gates are closed, and implementation-status blockers are cleared.



