# PantryOS

PantryOS is a home food intelligence system with two surfaces:

- A runnable local kitchen dashboard in `app/` for the v1 vertical slice.
- A Home Assistant custom integration in `custom_components/pantryos` for sensors, services, and automation.

Home Assistant is intended to be one interface into the food database, not the database itself. The current Core work keeps persistence local in SQLite for the web app and legacy import path. The Home Assistant integration is still scheduled to move from HA Store to the same API in Phase 3.

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
python app/server.py
```

Then open:

```text
http://127.0.0.1:8765
```

The dashboard imports legacy JSON on first startup when available and can seed demo data when the SQLite database is empty. You can also use **Seed Demo** or **Reset Demo** from the UI.


## Docker

Build and run the v1 app with Docker Compose:

```powershell
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
docker compose run --rm pantryos python scripts/run_tests.py
```
## Home Assistant Integration

Copy `custom_components/pantryos` into your Home Assistant config directory:

```text
<config>/custom_components/pantryos
```

Restart Home Assistant, then go to **Settings > Devices & services > Add integration** and search for `PantryOS`.

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

The local app exposes these JSON endpoints:

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

The pure inventory engine lives in `custom_components/pantryos/inventory.py` and is covered by tests in `tests/`.

Run tests when `pytest` is installed:

```powershell
python -m pytest
```

In the Codex bundled Python runtime, `pytest` may not be installed. The test functions can still be smoke-run directly:

```powershell
python scripts/run_tests.py
```



