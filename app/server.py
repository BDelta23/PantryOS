"""Local PantryOS web application server.

Run with:
    python app/server.py
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from collections.abc import Callable
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "app" / "static"
DEFAULT_DATA_PATH = ROOT / "data" / "pantryos.json"
INVENTORY_PATH = ROOT / "custom_components" / "pantryos" / "inventory.py"

_SPEC = spec_from_file_location("pantryos_inventory", INVENTORY_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Unable to load inventory engine from {INVENTORY_PATH}")
inventory = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = inventory
_SPEC.loader.exec_module(inventory)

InventoryManager = inventory.InventoryManager
InventoryState = inventory.InventoryState
InventoryItem = inventory.InventoryItem
Recipe = inventory.Recipe
ShoppingListItem = inventory.ShoppingListItem

DEFAULT_STATE = {
    "items": [],
    "recipes": [],
    "shopping_list": [],
    "meal_plan": {},
}


class JsonInventoryRepository:
    """File-backed inventory repository for the local app."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> InventoryManager:
        if not self.path.exists():
            return InventoryManager(InventoryState.from_dict(DEFAULT_STATE))
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return InventoryManager.from_dict(data)

    def save(self, manager: InventoryManager) -> None:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            delete=False,
        ) as handle:
            json.dump(manager.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
            temp_path = Path(handle.name)
        temp_path.replace(self.path)

    def mutate(self, callback: Callable[[Any], Any]) -> Any:
        manager = self.load()
        result = callback(manager)
        self.save(manager)
        return result


def public_state(manager: Any) -> dict[str, Any]:
    state = manager.to_dict()
    summary = manager.summary()
    leftovers = [
        item
        for item in state["items"]
        if "leftover" in [tag.casefold() for tag in item.get("tags", [])]
    ]
    return {
        "summary": summary,
        "items": sorted(state["items"], key=lambda item: (item["location"], item["name"])),
        "recipes": sorted(state["recipes"], key=lambda recipe: recipe["name"]),
        "shopping_list": state["shopping_list"],
        "meal_plan": state["meal_plan"],
        "leftovers": leftovers,
        "meals_with_two_or_fewer_missing": manager.possible_meals(max_missing=2),
    }


def seed_manager() -> Any:
    manager = InventoryManager()
    items = [
        {
            "name": "Chicken Breast",
            "quantity": "2",
            "unit": "lb",
            "location": "Garage/Chest Freezer",
            "purchased": "2026-08-21",
            "expires": "2026-08-27",
            "minimum_stock": "1",
            "estimated_cost": "8.50",
        },
        {
            "name": "Pasta",
            "quantity": "16",
            "unit": "oz",
            "location": "Kitchen/Pantry/Shelf 1",
            "minimum_stock": "16",
            "estimated_cost": "1.79",
        },
        {
            "name": "Parmesan",
            "quantity": "6",
            "unit": "oz",
            "location": "Kitchen/Refrigerator/Door",
            "expires": "2026-09-08",
            "estimated_cost": "4.50",
        },
        {
            "name": "Spinach",
            "quantity": "1",
            "unit": "bag",
            "location": "Kitchen/Refrigerator/Crisper",
            "expires": "2026-08-28",
            "estimated_cost": "3.49",
        },
        {
            "name": "Milk",
            "quantity": "0.25",
            "unit": "gallon",
            "location": "Kitchen/Refrigerator/Top Shelf",
            "expires": "2026-08-29",
            "minimum_stock": "1",
            "estimated_cost": "3.69",
        },
        {
            "name": "Eggs",
            "quantity": "4",
            "unit": "count",
            "location": "Kitchen/Refrigerator/Door",
            "minimum_stock": "12",
            "estimated_cost": "2.99",
        },
        {
            "name": "Taco Meat",
            "quantity": "3",
            "unit": "serving",
            "location": "Kitchen/Refrigerator/Bottom Shelf",
            "purchased": "2026-08-24",
            "expires": "2026-08-28",
            "tags": ["leftover"],
            "notes": "Dinner leftovers",
        },
    ]
    for item in items:
        manager.add_item(item)

    recipes = [
        {
            "name": "Chicken Alfredo",
            "prep_minutes": 25,
            "ingredients": [
                {"name": "Chicken Breast", "quantity": "1", "unit": "lb"},
                {"name": "Pasta", "quantity": "16", "unit": "oz"},
                {"name": "Parmesan", "quantity": "4", "unit": "oz"},
                {"name": "Heavy Cream", "quantity": "1", "unit": "cup"},
            ],
        },
        {
            "name": "Spinach Omelette",
            "prep_minutes": 12,
            "ingredients": [
                {"name": "Eggs", "quantity": "3", "unit": "count"},
                {"name": "Spinach", "quantity": "0.5", "unit": "bag"},
                {"name": "Parmesan", "quantity": "1", "unit": "oz"},
            ],
        },
        {
            "name": "Taco Leftover Bowls",
            "prep_minutes": 10,
            "ingredients": [
                {"name": "Taco Meat", "quantity": "2", "unit": "serving"},
                {"name": "Rice", "quantity": "1", "unit": "cup"},
            ],
        },
    ]
    for recipe in recipes:
        manager.add_recipe(recipe)

    manager.plan_meal("Tonight", "Spinach Omelette")
    return manager


class PantryRequestHandler(BaseHTTPRequestHandler):
    repository: JsonInventoryRepository

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._send_json(public_state(self.repository.load()))
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        body = self._read_json()

        try:
            if parsed.path == "/api/items":
                item = self.repository.mutate(lambda manager: manager.add_item(body))
                self._send_json({"item": item.to_dict()}, HTTPStatus.CREATED)
                return

            if parsed.path.startswith("/api/items/") and parsed.path.endswith("/consume"):
                item_id = parsed.path.removeprefix("/api/items/").removesuffix("/consume")
                quantity = Decimal(str(body.get("quantity", "1")))
                result = self.repository.mutate(
                    lambda manager: manager.consume_item(item_id, quantity)
                )
                self._send_json({"item": result.to_dict() if result is not None else None})
                return

            if parsed.path.startswith("/api/items/") and parsed.path.endswith("/move"):
                item_id = parsed.path.removeprefix("/api/items/").removesuffix("/move")
                result = self.repository.mutate(
                    lambda manager: manager.move_item(item_id, body["location"])
                )
                self._send_json({"item": result.to_dict()})
                return

            if parsed.path == "/api/recipes":
                recipe = self.repository.mutate(lambda manager: manager.add_recipe(body))
                self._send_json({"recipe": recipe.to_dict()}, HTTPStatus.CREATED)
                return

            if parsed.path.startswith("/api/recipes/") and parsed.path.endswith("/shopping"):
                recipe_name = unquote(
                    parsed.path.removeprefix("/api/recipes/").removesuffix("/shopping")
                )
                rows = self.repository.mutate(
                    lambda manager: manager.add_missing_to_shopping_list(recipe_name)
                )
                self._send_json({"items": [row.to_dict() for row in rows]})
                return

            if parsed.path == "/api/shopping":
                row = self.repository.mutate(
                    lambda manager: manager.add_shopping_item(
                        body["name"],
                        Decimal(str(body.get("quantity", "1"))),
                        body.get("unit") or "count",
                        body.get("source") or "manual",
                    )
                )
                self._send_json({"item": row.to_dict()}, HTTPStatus.CREATED)
                return

            if parsed.path == "/api/shopping/promote-suggestions":
                rows = self.repository.mutate(
                    lambda manager: manager.promote_suggested_purchases()
                )
                self._send_json({"items": [row.to_dict() for row in rows]})
                return

            if parsed.path == "/api/meal-plan":
                self.repository.mutate(
                    lambda manager: manager.plan_meal(body["day"], body["recipe_name"])
                )
                self._send_json({"ok": True})
                return

            if parsed.path == "/api/seed":
                query = parse_qs(parsed.query)
                reset = query.get("reset", ["false"])[0].casefold() == "true"
                if reset or not self.repository.path.exists():
                    self.repository.save(seed_manager())
                self._send_json(public_state(self.repository.load()))
                return

        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/items/"):
            item_id = parsed.path.removeprefix("/api/items/")
            try:
                self.repository.mutate(lambda manager: manager.delete_item(item_id))
                self._send_json({"ok": True})
            except KeyError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        if length == 0:
            return {}
        payload = self.rfile.read(length).decode("utf-8")
        return json.loads(payload)

    def _send_json(
        self,
        data: dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_static(self, path: str) -> None:
        if path in ("/", ""):
            target = STATIC_DIR / "index.html"
        else:
            target = STATIC_DIR / path.lstrip("/")
        try:
            resolved = target.resolve()
            static_root = STATIC_DIR.resolve()
            if not str(resolved).startswith(str(static_root)) or not resolved.is_file():
                raise FileNotFoundError
            content = resolved.read_bytes()
        except FileNotFoundError:
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def make_server(host: str, port: int, data_path: Path) -> ThreadingHTTPServer:
    handler = type("ConfiguredPantryRequestHandler", (PantryRequestHandler,), {})
    handler.repository = JsonInventoryRepository(data_path)
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PantryOS local app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH)
    args = parser.parse_args()

    server = make_server(args.host, args.port, args.data)
    print(f"PantryOS running at http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
