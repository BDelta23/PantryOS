# PantryOS

PantryOS is a home food intelligence system with two surfaces:

- A runnable local kitchen dashboard in `app/` for the v1 vertical slice.
- A Home Assistant custom integration in `custom_components/pantryos` for sensors, services, and automation.

Home Assistant is intended to be one interface into the food database, not the database itself. PantryOS Core owns the local SQLite database, and both the web app and Home Assistant integration use the authenticated Core API for current supported v1 workflows.

## V1 Vertical Slice

The local app covers the end-to-end loop that makes the product useful:

- See tonight's planned meal, use-soon food, quick meals, shopping, and inventory on one kitchen dashboard.
- Add food with quantity, unit, location, expiration, minimum stock, cost, and leftover flag.
- Consume or delete inventory items.
- Add recipes from simple comma-separated ingredient rows.
- Plan a recipe for tonight.
- Compare recipes against current inventory.
- Add missing recipe ingredients to the shopping list.
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

The dashboard imports legacy JSON on first startup when available and can seed demo data when the SQLite database is empty. You can also use **Seed Demo** or **Reset Demo** from the UI.


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

The container writes the SQLite database to the named Docker volume `pantryos-data`. A named volume is used because Docker Desktop does not reliably bind-mount this UNC checkout as a Windows host path. Change the host port with `PANTRYOS_PORT`:

```powershell
$env:PANTRYOS_PORT = "8770"
docker compose up --build
```

Run the dependency-free test runner in Docker:

```powershell
$env:PANTRYOS_API_TOKEN = "replace-with-a-long-local-token"
docker compose run --rm pantryos python scripts/run_tests.py
```
## Home Assistant Integration

Copy `custom_components/pantryos` into your Home Assistant config directory:

```text
<config>/custom_components/pantryos
```

Restart Home Assistant, then go to **Settings > Devices & services > Add integration** and search for `PantryOS`. Enter the PantryOS Core URL, for example `http://127.0.0.1:8765`, and the same `PANTRYOS_API_TOKEN` used by the Core server.

### Main Entities

The integration exposes these sensors:

- `sensor.pantryos_total_items`
- `sensor.pantryos_expiring_soon`
- `sensor.pantryos_shopping_list_count`
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

The versioned API requires `Authorization: Bearer <PANTRYOS_API_TOKEN>` except for health checks. Errors use a stable problem shape with `type`, `title`, `status`, `code`, `detail`, `errors`, and `request_id`.

Current versioned endpoints:

- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`
- `GET /api/v1/instance`
- `GET /api/v1/dashboard`
- `GET /api/v1/locations/summary`
- `GET /api/v1/waste/monthly`
- `POST /api/v1/inventory/lots`
- `POST /api/v1/inventory/lots/{id}/consume`
- `POST /api/v1/inventory/lots/{id}/move`
- `POST /api/v1/inventory/lots/{id}/discard`
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

The browser still uses these temporary compatibility routes while its session/auth flow is built:

- `GET /api/state`
- `POST /api/seed?reset=true`
- `POST /api/items`
- `POST /api/items/{id}/consume`
- `POST /api/items/{id}/move`
- `DELETE /api/items/{id}`
- `POST /api/recipes`
- `POST /api/recipes/{recipe_name}/shopping`
- `POST /api/shopping`
- `POST /api/shopping/promote-suggestions`
- `POST /api/meal-plan`

## Development

The authoritative inventory engine lives in `src/pantryos` and is exposed through the local Core API. The older pure inventory engine in `custom_components/pantryos/inventory.py` is retained temporarily as baseline coverage for the original proof of concept.

Run tests when `pytest` is installed:

```powershell
python -m pytest
```

In the Codex bundled Python runtime, `pytest` may not be installed. The test functions can still be smoke-run directly:

```powershell
python scripts/run_tests.py
```



