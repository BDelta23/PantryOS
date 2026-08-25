"""Local PantryOS web application server backed by PantryOS Core."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import sys
import tempfile
import uuid
from contextlib import closing
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pantryos.core import PantryCore, normalize_name  # noqa: E402
from pantryos.errors import PantryOSError  # noqa: E402
from pantryos.units import convert, decimal_text, require_non_negative, unit_code  # noqa: E402

STATIC_DIR = ROOT / "app" / "static"
DEFAULT_DB_PATH = ROOT / "data" / "pantryos.sqlite3"
LEGACY_JSON_PATH = ROOT / "data" / "pantryos.json"
MAX_REQUEST_BODY_BYTES = 1_000_000
PROBLEM_BASE_URL = "https://pantryos.local/problems"
PUBLIC_V1_ENDPOINTS = {"/api/v1/health/live", "/api/v1/health/ready"}


def demo_seed_document() -> dict[str, Any]:
    return {
        "items": [
            {
                "id": "seed-chicken",
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
                "id": "seed-pasta",
                "name": "Pasta",
                "quantity": "16",
                "unit": "oz",
                "location": "Kitchen/Pantry/Shelf 1",
                "minimum_stock": "16",
                "estimated_cost": "1.79",
            },
            {
                "id": "seed-parmesan",
                "name": "Parmesan",
                "quantity": "6",
                "unit": "oz",
                "location": "Kitchen/Refrigerator/Door",
                "expires": "2026-09-08",
                "estimated_cost": "4.50",
            },
            {
                "id": "seed-spinach",
                "name": "Spinach",
                "quantity": "1",
                "unit": "bag",
                "location": "Kitchen/Refrigerator/Crisper",
                "expires": "2026-08-28",
                "estimated_cost": "3.49",
            },
            {
                "id": "seed-milk",
                "name": "Milk",
                "quantity": "0.25",
                "unit": "gallon",
                "location": "Kitchen/Refrigerator/Top Shelf",
                "expires": "2026-08-29",
                "minimum_stock": "1",
                "estimated_cost": "3.69",
            },
            {
                "id": "seed-eggs",
                "name": "Eggs",
                "quantity": "4",
                "unit": "count",
                "location": "Kitchen/Refrigerator/Door",
                "minimum_stock": "12",
                "estimated_cost": "2.99",
            },
            {
                "id": "seed-taco-meat",
                "name": "Taco Meat",
                "quantity": "3",
                "unit": "serving",
                "location": "Kitchen/Refrigerator/Bottom Shelf",
                "purchased": "2026-08-24",
                "expires": "2026-08-28",
                "tags": ["leftover"],
                "notes": "Dinner leftovers",
            },
        ],
        "recipes": [
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
        ],
        "shopping_list": [],
        "meal_plan": {"Tonight": "Spinach Omelette"},
    }


def remove_database_files(db_path: Path) -> None:
    for path in (db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")):
        if path.exists():
            path.unlink()


def seed_core(core: PantryCore, reset: bool = False) -> dict[str, Any]:
    if reset:
        remove_database_files(core.db_path)
    core.migrate()
    if core.dashboard()["summary"]["product_count"] > 0 and not reset:
        return public_state(core)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        json.dump(demo_seed_document(), handle)
        seed_path = Path(handle.name)
    try:
        core.import_legacy_json(seed_path)
    finally:
        seed_path.unlink(missing_ok=True)
    return public_state(core)


def public_state(core: PantryCore) -> dict[str, Any]:
    dashboard = core.dashboard()
    lots = dashboard["lots"]
    products = dashboard["products"]
    recipes = dashboard["recipes"]
    shopping = dashboard["shopping"]
    summary = legacy_summary(core, dashboard)
    return {
        "revision": dashboard["revision"],
        "instance_id": dashboard["instance_id"],
        "summary": summary,
        "items": [lot_to_item(lot) for lot in lots if lot["status"] == "active"],
        "recipes": [recipe_to_legacy(recipe) for recipe in recipes],
        "shopping_list": [shopping_to_legacy(row) for row in shopping if row["status"] == "active"],
        "meal_plan": meal_plan_legacy(core),
        "leftovers": [lot_to_item(lot) for lot in lots if lot["status"] == "active" and lot["lot_type"] == "leftover"],
        "meals_with_two_or_fewer_missing": recipe_matches(core, recipes, max_missing=2),
        "core": {"products": products, "lots": lots, "events": dashboard["events"]},
    }


def lot_to_item(lot: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": lot["id"],
        "name": lot["product_name"],
        "quantity": lot["quantity"],
        "unit": lot["unit"],
        "location": lot["location_name"],
        "purchased": lot["acquired_at"],
        "expires": lot["expires_at"],
        "opened": bool(lot["opened_at"]),
        "minimum_stock": lot["minimum_stock_quantity"],
        "barcode": None,
        "estimated_cost": lot["total_cost"],
        "tags": ["leftover"] if lot["lot_type"] == "leftover" else [],
        "notes": lot["notes"],
    }


def recipe_to_legacy(recipe: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": recipe["id"],
        "name": recipe["name"],
        "prep_minutes": recipe["prep_minutes"],
        "instructions": recipe["instructions"],
        "tags": json.loads(recipe["tags_json"]),
        "ingredients": [
            {
                "id": ingredient["id"],
                "product_id": ingredient["product_id"],
                "name": ingredient["display_text"],
                "quantity": ingredient["quantity"],
                "unit": ingredient["unit"],
            }
            for ingredient in recipe["ingredients"]
        ],
    }


def shopping_to_legacy(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["display_name"],
        "quantity": row["quantity"],
        "unit": row["unit"],
        "source": row["source_key"],
        "source_kind": row["source_kind"],
        "source_id": row["source_id"],
        "status": row["status"],
        "accepted": bool(row["accepted"]),
        "checked": bool(row["checked"]),
        "note": row["note"],
        "store": row["store"],
    }


def meal_plan_legacy(core: PantryCore) -> dict[str, str]:
    with closing(core.connect()) as connection:
        rows = connection.execute(
            """
            SELECT m.meal_type, r.name
            FROM meal_plan_entries m
            JOIN recipes r ON r.id = m.recipe_id
            ORDER BY m.plan_date, m.meal_type
            """
        ).fetchall()
    return {row["meal_type"]: row["name"] for row in rows}


def legacy_summary(core: PantryCore, dashboard: dict[str, Any]) -> dict[str, Any]:
    expiring = expiring_soon(dashboard["lots"])
    suggestions = minimum_stock_suggestions(core)
    possible = recipe_matches(core, dashboard["recipes"], max_missing=0)
    return {
        "total_items": dashboard["summary"]["active_lot_count"],
        "expiring_soon": expiring,
        "expiring_soon_count": len(expiring),
        "shopping_list_count": dashboard["summary"]["shopping_count"],
        "suggested_purchases": suggestions,
        "suggested_purchase_count": len(suggestions),
        "possible_meals": possible,
        "possible_meal_count": len(possible),
        "food_waste_this_month": "0",
        "location_counts": location_counts(dashboard["lots"]),
    }


def expiring_soon(lots: list[dict[str, Any]], days: int = 4) -> list[dict[str, Any]]:
    today = datetime_date()
    rows = []
    for lot in lots:
        if lot["status"] != "active" or not lot["expires_at"]:
            continue
        days_left = (datetime_date(lot["expires_at"]) - today).days
        if 0 <= days_left <= days:
            rows.append(
                {
                    "id": lot["id"],
                    "name": lot["product_name"],
                    "quantity": lot["quantity"],
                    "unit": lot["unit"],
                    "location": lot["location_name"],
                    "days_left": days_left,
                    "expires": lot["expires_at"],
                }
            )
    return sorted(rows, key=lambda row: (row["days_left"], row["name"]))


def datetime_date(value: str | None = None):
    from datetime import date

    if value is None:
        return date.today()
    return date.fromisoformat(value[:10])


def location_counts(lots: list[dict[str, Any]]) -> dict[str, int]:
    active = [lot for lot in lots if lot["status"] == "active"]
    return {
        "Kitchen": sum(1 for lot in active if lot["location_name"] != "Chest Freezer"),
        "Refrigerator": sum(1 for lot in active if "Refrigerator" in lot["location_name"] or lot["location_name"] in {"Door", "Crisper", "Top Shelf", "Bottom Shelf"}),
        "Freezer": sum(1 for lot in active if "Freezer" in lot["location_name"]),
        "Pantry": sum(1 for lot in active if "Pantry" in lot["location_name"] or "Shelf" in lot["location_name"]),
    }


def recipe_matches(core: PantryCore, recipes: list[dict[str, Any]], max_missing: int) -> list[dict[str, Any]]:
    with closing(core.connect()) as connection:
        lot_rows = connection.execute(
            "SELECT * FROM inventory_lots WHERE status = 'active' AND CAST(quantity AS REAL) > 0"
        ).fetchall()
    available: dict[str, list[dict[str, Any]]] = {}
    today = datetime_date()
    for row in lot_rows:
        lot = dict(row)
        if lot["expires_at"] and datetime_date(lot["expires_at"]) < today:
            continue
        available.setdefault(lot["product_id"], []).append(lot)

    meals = []
    for recipe in recipes:
        missing = []
        unresolved = []
        for ingredient in recipe["ingredients"]:
            product_id = ingredient["product_id"]
            if product_id is None:
                unresolved.append(ingredient["display_text"])
                missing.append(
                    {
                        "name": ingredient["display_text"],
                        "quantity": ingredient["quantity"],
                        "unit": ingredient["unit"],
                    }
                )
                continue
            required = require_non_negative(ingredient["quantity"])
            unit = unit_code(ingredient["unit"])
            on_hand = Decimal("0")
            try:
                for lot in available.get(product_id, []):
                    on_hand += convert(require_non_negative(lot["quantity"]), lot["unit"], unit)
            except PantryOSError:
                on_hand = Decimal("0")
            if on_hand < required:
                missing.append(
                    {
                        "name": ingredient["display_text"],
                        "quantity": decimal_text(required - on_hand),
                        "unit": unit,
                    }
                )
        if len(missing) <= max_missing:
            meals.append(
                {
                    "name": recipe["name"],
                    "prep_minutes": recipe["prep_minutes"],
                    "missing_count": len(missing),
                    "missing": missing,
                    "unresolved": unresolved,
                }
            )
    return sorted(meals, key=lambda meal: (meal["missing_count"], meal["prep_minutes"] or 9999, meal["name"]))


def minimum_stock_suggestions(core: PantryCore) -> list[dict[str, Any]]:
    with closing(core.connect()) as connection:
        products = connection.execute(
            "SELECT * FROM products WHERE active = 1 AND minimum_stock_quantity IS NOT NULL ORDER BY name"
        ).fetchall()
        lots = connection.execute(
            "SELECT * FROM inventory_lots WHERE status = 'active' AND CAST(quantity AS REAL) > 0"
        ).fetchall()
    by_product: dict[str, list[dict[str, Any]]] = {}
    for row in lots:
        by_product.setdefault(row["product_id"], []).append(dict(row))
    suggestions = []
    for product in products:
        target = require_non_negative(product["minimum_stock_quantity"])
        unit = unit_code(product["minimum_stock_unit"])
        current = Decimal("0")
        try:
            for lot in by_product.get(product["id"], []):
                current += convert(require_non_negative(lot["quantity"]), lot["unit"], unit)
        except PantryOSError:
            continue
        if current < target:
            suggestions.append(
                {
                    "name": product["name"],
                    "quantity": decimal_text(target - current),
                    "unit": unit,
                    "current": decimal_text(current),
                    "minimum_stock": decimal_text(target),
                }
            )
    return suggestions


class PantryRequestHandler(BaseHTTPRequestHandler):
    core: PantryCore
    api_token: str | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if not self._authorize(parsed.path):
            return
        if parsed.path in ("/api/state", "/api/v1/dashboard"):
            self._send_json(public_state(self.core))
            return
        cooking_session_id = cooking_session_path(parsed.path)
        if cooking_session_id is not None:
            self._send_json(self.core.cooking_session(cooking_session_id))
            return
        if parsed.path == "/api/v1/leftovers":
            self._send_json({"items": [lot_to_item(row) for row in public_state(self.core)["core"]["lots"] if row["status"] == "active" and row["lot_type"] == "leftover"]})
            return
        if parsed.path == "/api/v1/shopping":
            self._send_json({"items": [shopping_to_legacy(row) for row in self.core.shopping_items()]})
            return
        if parsed.path == "/api/v1/instance":
            self._send_json(self.core.instance())
            return
        if parsed.path == "/api/v1/health/live":
            self._send_json({"status": "live"})
            return
        if parsed.path == "/api/v1/health/ready":
            self.core.integrity_check()
            self._send_json({"status": "ready"})
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self._authorize(parsed.path):
            return
        try:
            body = self._read_json()
            if parsed.path in ("/api/items", "/api/v1/inventory/lots"):
                result = self.core.add_inventory_lot(body)
                self._send_json({"item": lot_to_item(result["lot"]), "revision": result["revision"]}, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/v1/cooking/sessions":
                result = self.core.start_cooking_session(body)
                self._send_json(result, HTTPStatus.CREATED)
                return
            cooking_action = cooking_action_path(parsed.path)
            if cooking_action is not None:
                session_id, action = cooking_action
                if action == "complete":
                    result = self.core.complete_cooking_session(session_id, body)
                    self._send_json({**result, "leftovers": [lot_to_item(row) for row in result["leftovers"]]})
                    return
                if action == "cancel":
                    self._send_json(self.core.cancel_cooking_session(session_id, body))
                    return
            versioned_lot_action = versioned_lot_action_path(parsed.path)
            if versioned_lot_action is not None:
                lot_id, action = versioned_lot_action
                if action == "consume":
                    self._send_json(consume_lot_product(self.core, lot_id, str(body["quantity"]), body.get("reason")))
                    return
                if action == "move":
                    self._send_json(move_lot(self.core, lot_id, body["location"]))
                    return
                if action == "discard":
                    self._send_json(discard_lot(self.core, lot_id, str(body["reason"])))
                    return
            if parsed.path.startswith("/api/items/") and parsed.path.endswith("/consume"):
                lot_id = parsed.path.removeprefix("/api/items/").removesuffix("/consume")
                result = consume_lot_product(self.core, lot_id, str(body.get("quantity", "1")))
                self._send_json(result)
                return
            if parsed.path.startswith("/api/items/") and parsed.path.endswith("/move"):
                lot_id = parsed.path.removeprefix("/api/items/").removesuffix("/move")
                self._send_json(move_lot(self.core, lot_id, body["location"]))
                return
            if parsed.path == "/api/seed":
                reset = parse_qs(parsed.query).get("reset", ["false"])[0].casefold() == "true"
                self._send_json(seed_core(self.core, reset=reset))
                return
            if parsed.path in ("/api/meal-plan", "/api/v1/meal-plan"):
                self._send_json(plan_meal(self.core, body["day"], body["recipe_name"]))
                return
            if parsed.path in ("/api/recipes", "/api/v1/recipes"):
                self._send_json(add_recipe(self.core, body), HTTPStatus.CREATED)
                return
            recipe_shopping_name = recipe_shopping_path(parsed.path)
            if recipe_shopping_name is not None:
                self._send_json(add_missing_to_shopping(self.core, recipe_shopping_name))
                return
            if parsed.path in ("/api/shopping", "/api/v1/shopping/manual"):
                self._send_json(add_manual_shopping(self.core, body), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/v1/shopping/rebuild":
                result = self.core.rebuild_shopping_demand()
                self._send_json({**result, "items": [shopping_to_legacy(row) for row in result["items"]]})
                return
            if parsed.path == "/api/v1/shopping/complete-purchase":
                result = self.core.complete_purchase(body)
                self._send_json({**result, "lots": [lot_to_item(row) for row in result["lots"]]}, HTTPStatus.CREATED)
                return
            shopping_action = shopping_action_path(parsed.path)
            if shopping_action is not None:
                item_id, action = shopping_action
                if action == "check":
                    result = self.core.set_shopping_checked(item_id, True)
                    self._send_json({**result, "item": shopping_to_legacy(result["item"])})
                    return
                if action == "uncheck":
                    result = self.core.set_shopping_checked(item_id, False)
                    self._send_json({**result, "item": shopping_to_legacy(result["item"])})
                    return
            if parsed.path in ("/api/shopping/promote-suggestions", "/api/v1/shopping/promote-suggestions"):
                self._send_json(promote_suggestions(self.core))
                return
        except json.JSONDecodeError:
            self._send_problem(
                HTTPStatus.BAD_REQUEST,
                "Request body must be valid JSON.",
                code="invalid_json",
                title="Invalid JSON",
            )
            return
        except KeyError as exc:
            self._send_problem(
                HTTPStatus.BAD_REQUEST,
                f"Missing required field: {exc.args[0]}",
                code="missing_field",
                title="Missing field",
            )
            return
        except PantryOSError as exc:
            self._send_problem(domain_status(exc), str(exc), code=problem_code(exc), title=problem_title(exc))
            return
        except ValueError as exc:
            self._send_problem(HTTPStatus.BAD_REQUEST, str(exc), code="invalid_request", title="Invalid request")
            return
        self._send_problem(HTTPStatus.NOT_FOUND, "Not found", code="not_found", title="Not found")

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        if not self._authorize(parsed.path):
            return
        try:
            body = self._read_json()
            shopping_id = shopping_item_path(parsed.path)
            if shopping_id is not None:
                result = self.core.update_shopping_item(shopping_id, body)
                self._send_json({**result, "item": shopping_to_legacy(result["item"])})
                return
        except json.JSONDecodeError:
            self._send_problem(HTTPStatus.BAD_REQUEST, "Request body must be valid JSON.", code="invalid_json", title="Invalid JSON")
            return
        except KeyError as exc:
            self._send_problem(HTTPStatus.BAD_REQUEST, f"Missing required field: {exc.args[0]}", code="missing_field", title="Missing field")
            return
        except PantryOSError as exc:
            self._send_problem(domain_status(exc), str(exc), code=problem_code(exc), title=problem_title(exc))
            return
        except ValueError as exc:
            self._send_problem(HTTPStatus.BAD_REQUEST, str(exc), code="invalid_request", title="Invalid request")
            return
        self._send_problem(HTTPStatus.NOT_FOUND, "Not found", code="not_found", title="Not found")

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if not self._authorize(parsed.path):
            return
        try:
            shopping_id = shopping_item_path(parsed.path)
            if shopping_id is not None:
                self._send_json(self.core.remove_shopping_item(shopping_id))
                return
            if parsed.path.startswith("/api/items/"):
                lot_id = parsed.path.removeprefix("/api/items/")
                self._send_json(discard_lot(self.core, lot_id))
                return
        except PantryOSError as exc:
            self._send_problem(domain_status(exc), str(exc), code=problem_code(exc), title=problem_title(exc))
            return
        self._send_problem(HTTPStatus.NOT_FOUND, "Not found", code="not_found", title="Not found")
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        if length == 0:
            return {}
        if length > MAX_REQUEST_BODY_BYTES:
            raise ValueError("Request body too large")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def _authorize(self, path: str) -> bool:
        if not is_versioned_api(path) or path in PUBLIC_V1_ENDPOINTS:
            return True
        token = self.api_token
        if not token:
            self._send_problem(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "PANTRYOS_API_TOKEN is required before using /api/v1.",
                code="auth_not_configured",
                title="Authentication not configured",
            )
            return False
        auth_header = self.headers.get("Authorization", "")
        scheme, _, supplied_token = auth_header.partition(" ")
        if scheme.casefold() != "bearer" or not secrets.compare_digest(supplied_token.strip(), token):
            self._send_problem(
                HTTPStatus.UNAUTHORIZED,
                "A valid bearer token is required.",
                code="unauthorized",
                title="Unauthorized",
                headers={"WWW-Authenticate": 'Bearer realm="PantryOS"'},
            )
            return False
        return True

    def _request_id(self) -> str:
        request_id = getattr(self, "_pantryos_request_id", None)
        if request_id is None:
            supplied = self.headers.get("X-Request-ID", "").strip()
            if supplied and len(supplied) <= 128 and all(char.isprintable() for char in supplied):
                request_id = supplied
            else:
                request_id = uuid.uuid4().hex
            self._pantryos_request_id = request_id
        return request_id

    def _send_problem(
        self,
        status: HTTPStatus,
        detail: str,
        *,
        code: str,
        title: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._send_json(
            problem(detail, status=status, code=code, title=title, request_id=self._request_id()),
            status,
            headers=headers,
        )

    def _send_json(
        self,
        data: dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Request-ID", self._request_id())
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _serve_static(self, path: str) -> None:
        target = STATIC_DIR / "index.html" if path in ("/", "") else STATIC_DIR / path.lstrip("/")
        try:
            resolved = target.resolve()
            resolved.relative_to(STATIC_DIR.resolve())
            if not resolved.is_file():
                raise FileNotFoundError
            content = resolved.read_bytes()
        except (FileNotFoundError, ValueError):
            self._send_problem(HTTPStatus.NOT_FOUND, "Not found", code="not_found", title="Not found")
            return
        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def is_versioned_api(path: str) -> bool:
    return path == "/api/v1" or path.startswith("/api/v1/")


def problem(
    detail: str,
    *,
    status: HTTPStatus = HTTPStatus.BAD_REQUEST,
    code: str = "request_failed",
    title: str = "Request failed",
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "type": f"{PROBLEM_BASE_URL}/{code.replace('_', '-')}",
        "title": title,
        "status": status.value,
        "code": code,
        "detail": detail,
        "errors": [],
        "request_id": request_id or uuid.uuid4().hex,
    }


def problem_code(exc: PantryOSError) -> str:
    return getattr(exc, "code", None) or "domain_error"


def problem_title(exc: PantryOSError) -> str:
    words = problem_code(exc).replace("_", " ")
    return words.capitalize()


def domain_status(exc: PantryOSError) -> HTTPStatus:
    name = exc.__class__.__name__
    if name == "NotFoundError":
        return HTTPStatus.NOT_FOUND
    if name in {"ConflictError", "InsufficientInventoryError"}:
        return HTTPStatus.CONFLICT
    return HTTPStatus.BAD_REQUEST


def versioned_lot_action_path(path: str) -> tuple[str, str] | None:
    prefix = "/api/v1/inventory/lots/"
    if not path.startswith(prefix):
        return None
    parts = path.removeprefix(prefix).split("/")
    if len(parts) != 2 or not parts[0] or parts[1] not in {"consume", "move", "discard"}:
        return None
    return unquote(parts[0]), parts[1]


def recipe_shopping_path(path: str) -> str | None:
    for prefix in ("/api/recipes/", "/api/v1/recipes/"):
        if path.startswith(prefix) and path.endswith("/shopping"):
            return unquote(path.removeprefix(prefix).removesuffix("/shopping"))
    return None


def cooking_session_path(path: str) -> str | None:
    prefix = "/api/v1/cooking/sessions/"
    if not path.startswith(prefix):
        return None
    suffix = path.removeprefix(prefix)
    if not suffix or "/" in suffix:
        return None
    return unquote(suffix)


def cooking_action_path(path: str) -> tuple[str, str] | None:
    prefix = "/api/v1/cooking/sessions/"
    if not path.startswith(prefix):
        return None
    parts = path.removeprefix(prefix).split("/")
    if len(parts) != 2 or parts[1] not in {"complete", "cancel"}:
        return None
    return unquote(parts[0]), parts[1]


def shopping_item_path(path: str) -> str | None:
    prefix = "/api/v1/shopping/"
    if not path.startswith(prefix):
        return None
    suffix = path.removeprefix(prefix)
    if not suffix or "/" in suffix or suffix in {"manual", "rebuild", "complete-purchase", "promote-suggestions"}:
        return None
    return unquote(suffix)


def shopping_action_path(path: str) -> tuple[str, str] | None:
    prefix = "/api/v1/shopping/"
    if not path.startswith(prefix):
        return None
    parts = path.removeprefix(prefix).split("/")
    if len(parts) != 2 or parts[1] not in {"check", "uncheck"}:
        return None
    return unquote(parts[0]), parts[1]


def consume_lot_product(core: PantryCore, lot_id: str, quantity: str, reason: str | None = None) -> dict[str, Any]:
    with closing(core.connect()) as connection:
        lot = connection.execute("SELECT product_id, unit FROM inventory_lots WHERE id = ?", (lot_id,)).fetchone()
    if lot is None:
        raise PantryOSError(f"Unknown inventory lot: {lot_id}")
    return core.consume_product(product_id=lot["product_id"], quantity=quantity, unit=lot["unit"], reason=reason)


def move_lot(core: PantryCore, lot_id: str, location: str) -> dict[str, Any]:
    core.migrate()
    with core.transaction() as connection:
        lot = connection.execute("SELECT * FROM inventory_lots WHERE id = ?", (lot_id,)).fetchone()
        if lot is None:
            raise PantryOSError(f"Unknown inventory lot: {lot_id}")
        to_location_id = core.ensure_location_path(connection, location)
        from_location_id = lot["location_id"]
        connection.execute(
            "UPDATE inventory_lots SET location_id = ?, updated_at = ?, version = version + 1 WHERE id = ?",
            (to_location_id, datetime_now(), lot_id),
        )
        revision = core._append_event(
            connection,
            "MOVE",
            product_id=lot["product_id"],
            lot_id=lot_id,
            from_location_id=from_location_id,
            to_location_id=to_location_id,
            source="api",
        )
        updated = core.get_lot(connection, lot_id)
    return {"item": lot_to_item(updated), "revision": revision}


def discard_lot(core: PantryCore, lot_id: str, reason: str = "web delete action") -> dict[str, Any]:
    core.migrate()
    with core.transaction() as connection:
        lot = connection.execute("SELECT * FROM inventory_lots WHERE id = ?", (lot_id,)).fetchone()
        if lot is None:
            raise PantryOSError(f"Unknown inventory lot: {lot_id}")
        connection.execute(
            "UPDATE inventory_lots SET quantity = '0', status = 'discarded', updated_at = ?, version = version + 1 WHERE id = ?",
            (datetime_now(), lot_id),
        )
        revision = core._append_event(
            connection,
            "DISCARD",
            product_id=lot["product_id"],
            lot_id=lot_id,
            quantity=lot["quantity"],
            unit=lot["unit"],
            reason=reason,
            source="api",
        )
    return {"ok": True, "revision": revision}


def add_recipe(core: PantryCore, body: dict[str, Any]) -> dict[str, Any]:
    core.migrate()
    with core.transaction() as connection:
        recipe_id = core._upsert_recipe(connection, body)
        connection.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
        for position, ingredient in enumerate(body.get("ingredients", [])):
            product = connection.execute(
                "SELECT id FROM products WHERE normalized_name = ?",
                (normalize_name(ingredient["name"]),),
            ).fetchone()
            core._insert_recipe_ingredient(
                connection,
                recipe_id,
                ingredient,
                product["id"] if product else None,
                position,
            )
        snapshot = core._recipe_snapshot(connection, recipe_id)
        revision = int(connection.execute("SELECT value FROM app_metadata WHERE key = 'state_revision'").fetchone()[0])
    return {"recipe": recipe_to_legacy(snapshot), "revision": revision}


def plan_meal(core: PantryCore, day: str, recipe_name: str) -> dict[str, Any]:
    core.migrate()
    with core.transaction() as connection:
        recipe = connection.execute(
            "SELECT id FROM recipes WHERE normalized_name = ?",
            (normalize_name(recipe_name),),
        ).fetchone()
        if recipe is None:
            raise PantryOSError(f"Unknown recipe: {recipe_name}")
        core._upsert_meal_plan(
            connection,
            plan_date=datetime_date().isoformat(),
            meal_type=day,
            recipe_id=recipe["id"],
            servings="1",
        )
        revision = int(connection.execute("SELECT value FROM app_metadata WHERE key = 'state_revision'").fetchone()[0])
    return {"ok": True, "revision": revision}


def add_missing_to_shopping(core: PantryCore, recipe_name: str) -> dict[str, Any]:
    core.migrate()
    with core.transaction() as connection:
        recipe_row = connection.execute(
            "SELECT id FROM recipes WHERE normalized_name = ?",
            (normalize_name(recipe_name),),
        ).fetchone()
        if recipe_row is None:
            raise PantryOSError(f"Unknown recipe: {recipe_name}")
        recipe = core._recipe_snapshot(connection, recipe_row["id"])
        missing = recipe_matches(core, [recipe], max_missing=999)[0]["missing"]
        for row in missing:
            product = connection.execute(
                "SELECT id FROM products WHERE normalized_name = ?",
                (normalize_name(row["name"]),),
            ).fetchone()
            source_key = f"recipe:{recipe['id']}:{normalize_name(row['name'])}:{unit_code(row['unit'])}"
            core._upsert_shopping_demand(
                connection,
                source_key=source_key,
                product_id=product["id"] if product else None,
                display_name=row["name"],
                quantity=row["quantity"],
                unit=row["unit"],
                source_kind="recipe",
                source_id=recipe["id"],
                accepted=True,
            )
        rows = [dict(item) for item in connection.execute("SELECT * FROM shopping_demands WHERE source_id = ?", (recipe["id"],))]
    return {"items": [shopping_to_legacy(row) for row in rows]}


def add_manual_shopping(core: PantryCore, body: dict[str, Any]) -> dict[str, Any]:
    core.migrate()
    source_key = str(body.get("source_key") or f"manual:{uuid.uuid4().hex}")
    with core.transaction() as connection:
        product = connection.execute(
            "SELECT id FROM products WHERE normalized_name = ?",
            (normalize_name(body["name"]),),
        ).fetchone()
        core._upsert_shopping_demand(
            connection,
            source_key=source_key,
            product_id=product["id"] if product else None,
            display_name=body["name"],
            quantity=str(body["quantity"]),
            unit=str(body.get("unit") or "count"),
            source_kind=str(body.get("source") or "manual"),
            source_id=None,
            accepted=True,
        )
        revision = core._append_event(
            connection,
            "SHOPPING_MANUAL",
            product_id=product["id"] if product else None,
            quantity=str(body["quantity"]),
            unit=str(body.get("unit") or "count"),
            reason="manual shopping demand",
            source="api",
        )
        row = connection.execute("SELECT * FROM shopping_demands WHERE source_key = ?", (source_key,)).fetchone()
    return {"item": shopping_to_legacy(dict(row)), "revision": revision}


def promote_suggestions(core: PantryCore) -> dict[str, Any]:
    core.migrate()
    suggestions = minimum_stock_suggestions(core)
    with core.transaction() as connection:
        for row in suggestions:
            product = connection.execute(
                "SELECT id FROM products WHERE normalized_name = ?",
                (normalize_name(row["name"]),),
            ).fetchone()
            if product is None:
                continue
            core._upsert_shopping_demand(
                connection,
                source_key=f"minimum:{product['id']}",
                product_id=product["id"],
                display_name=row["name"],
                quantity=row["quantity"],
                unit=row["unit"],
                source_kind="minimum_stock",
                source_id=product["id"],
                accepted=True,
            )
        rows = [dict(item) for item in connection.execute("SELECT * FROM shopping_demands WHERE source_kind = 'minimum_stock'")]
    return {"items": [shopping_to_legacy(row) for row in rows]}


def datetime_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def make_server(host: str, port: int, db_path: Path) -> ThreadingHTTPServer:
    handler = type("ConfiguredPantryRequestHandler", (PantryRequestHandler,), {})
    handler.core = PantryCore(db_path)
    handler.api_token = os.environ.get("PANTRYOS_API_TOKEN")
    handler.core.migrate()
    if LEGACY_JSON_PATH.exists() and handler.core.dashboard()["summary"]["product_count"] == 0:
        handler.core.import_legacy_json(LEGACY_JSON_PATH)
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PantryOS local app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    server = make_server(args.host, args.port, args.data)
    print(f"PantryOS running at http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

