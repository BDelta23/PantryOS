"""Local PantryOS web application server backed by PantryOS Core."""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import secrets
import sys
import tempfile
import threading
import time
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
for candidate in (ROOT / "scripts", SRC):
    candidate_text = str(candidate)
    while candidate_text in sys.path:
        sys.path.remove(candidate_text)
sys.path.insert(0, str(SRC))

from pantryos.core import PantryCore, normalize_name  # noqa: E402
from pantryos.errors import PantryOSError  # noqa: E402
from pantryos.openapi import openapi_document  # noqa: E402
from pantryos.paths import path_within  # noqa: E402
from pantryos.units import convert, decimal_text, require_non_negative, unit_code  # noqa: E402

STATIC_DIR = ROOT / "app" / "static"
DEFAULT_DB_PATH = ROOT / "data" / "pantryos.sqlite3"
LEGACY_JSON_PATH = ROOT / "data" / "pantryos.json"
MAX_REQUEST_BODY_BYTES = 1_000_000
DEFAULT_RATE_LIMIT_REQUESTS = 20
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60
PROBLEM_BASE_URL = "https://pantryos.local/problems"
PUBLIC_V1_ENDPOINTS = {"/api/v1/health/live", "/api/v1/health/ready"}
BROWSER_SESSION_COOKIE = "pantryos_session"
DEFAULT_BROWSER_SESSION_SECONDS = 12 * 60 * 60
DEFAULT_EVENT_STREAM_SECONDS = 30.0
MAX_EVENT_STREAM_SECONDS = 300.0
DEFAULT_EVENT_HEARTBEAT_SECONDS = 15.0
MIN_EVENT_HEARTBEAT_SECONDS = 0.1
DEFAULT_HTTP_REQUEST_QUEUE_SIZE = 64
SESSION_ENDPOINTS = {"/api/session", "/api/session/login", "/api/session/logout"}
LOGGER = logging.getLogger("pantryos.http")
LOGGER.addHandler(logging.NullHandler())


class PantryHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server sized for local bursty UI and API clients."""

    request_queue_size = DEFAULT_HTTP_REQUEST_QUEUE_SIZE


class RequestBodyTooLarge(ValueError):
    """Request body exceeds the configured API limit."""


class UnsupportedMediaType(ValueError):
    """Request declares a body type PantryOS does not accept."""


class RateLimitExceeded(ValueError):
    """Request exceeded a bounded in-process route limit."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Rate limit exceeded; retry after {retry_after_seconds} seconds")


class RateLimiter:
    """Small fixed-window limiter for local expensive endpoints."""

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self.limit = max(1, limit)
        self.window_seconds = max(1, window_seconds)
        self._lock = threading.Lock()
        self._windows: dict[tuple[str, str], tuple[float, int]] = {}

    def check(self, *, client: str, bucket: str, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        key = (client, bucket)
        with self._lock:
            window_start, count = self._windows.get(key, (current, 0))
            elapsed = current - window_start
            if elapsed >= self.window_seconds:
                self._windows[key] = (current, 1)
                return
            if count >= self.limit:
                retry_after = max(1, int(self.window_seconds - elapsed))
                raise RateLimitExceeded(retry_after)
            self._windows[key] = (window_start, count + 1)


class BrowserSessionStore:
    """File-backed session store for the local browser UI."""

    def __init__(self, *, ttl_seconds: int, storage_path: Path | None = None) -> None:
        self.ttl_seconds = max(60, ttl_seconds)
        self.storage_path = storage_path
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, str | float]] = {}
        with self._lock:
            self._load_locked()

    def create(self) -> dict[str, str | float]:
        session_id = secrets.token_urlsafe(32)
        session = {
            "id": session_id,
            "csrf_token": secrets.token_urlsafe(32),
            "expires_at": time.time() + self.ttl_seconds,
        }
        with self._lock:
            self._sessions[session_id] = session
            self._save_locked()
        return session

    def get(self, session_id: str | None) -> dict[str, str | float] | None:
        if not session_id:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if float(session["expires_at"]) <= time.time():
                self._sessions.pop(session_id, None)
                self._save_locked()
                return None
            return session

    def delete(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)
            self._save_locked()

    def _load_locked(self) -> None:
        if self.storage_path is None or not self.storage_path.exists():
            return
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._sessions = {}
            return
        sessions = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(sessions, dict):
            self._sessions = {}
            return
        now = time.time()
        loaded: dict[str, dict[str, str | float]] = {}
        for session_id, session in sessions.items():
            if not isinstance(session_id, str) or not isinstance(session, dict):
                continue
            csrf_token = session.get("csrf_token")
            expires_at = session.get("expires_at")
            if not isinstance(csrf_token, str) or not isinstance(expires_at, int | float):
                continue
            if expires_at <= now:
                continue
            loaded[session_id] = {"id": session_id, "csrf_token": csrf_token, "expires_at": float(expires_at)}
        self._sessions = loaded
        if len(loaded) != len(sessions):
            self._save_locked()

    def _save_locked(self) -> None:
        if self.storage_path is None:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"sessions": self._sessions}
        temporary = self.storage_path.with_name(f".{self.storage_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        temporary.replace(self.storage_path)


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
        "core": {
            "products": products,
            "lots": lots,
            "events": dashboard["events"],
            "locations": dashboard["locations"],
            "summary": dashboard["summary"],
        },
    }


def lot_to_item(lot: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": lot["id"],
        "name": lot["product_name"],
        "quantity": lot["quantity"],
        "unit": lot["unit"],
        "location": lot.get("location_path") or lot["location_name"],
        "location_name": lot["location_name"],
        "purchased": lot["acquired_at"],
        "expires": lot["expires_at"],
        "opened": bool(lot["opened_at"]),
        "opened_at": lot["opened_at"],
        "minimum_stock": lot["minimum_stock_quantity"],
        "product_id": lot["product_id"],
        "barcode": None,
        "estimated_cost": lot["total_cost"],
        "estimated_value": lot.get("estimated_value", "0.00"),
        "tags": ["leftover"] if lot["lot_type"] == "leftover" else [],
        "notes": lot["notes"],
    }


def recipe_to_legacy(recipe: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": recipe["id"],
        "name": recipe["name"],
        "prep_minutes": recipe["prep_minutes"],
        "yield_servings": recipe["yield_servings"],
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
    leftovers = [lot for lot in dashboard["lots"] if lot["status"] == "active" and lot["lot_type"] == "leftover"]
    return {
        "total_items": dashboard["summary"]["active_lot_count"],
        "state_revision": dashboard["revision"],
        "leftover_count": len(leftovers),
        "expiring_soon": expiring,
        "expiring_soon_count": len(expiring),
        "shopping_list_count": dashboard["summary"]["shopping_count"],
        "suggested_purchases": suggestions,
        "suggested_purchase_count": len(suggestions),
        "possible_meals": possible,
        "possible_meal_count": len(possible),
        "food_waste_this_month": dashboard["summary"]["food_waste_this_month"],
        "location_counts": dashboard["summary"]["location_counts"],
        "location_values": dashboard["summary"]["location_values"],
        "locations": dashboard["summary"]["locations"],
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
                    "location": lot.get("location_path") or lot["location_name"],
                    "location_name": lot["location_name"],
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
        "Refrigerator": sum(
            1
            for lot in active
            if "Refrigerator" in lot["location_name"] or lot["location_name"] in {"Door", "Crisper", "Top Shelf", "Bottom Shelf"}
        ),
        "Freezer": sum(1 for lot in active if "Freezer" in lot["location_name"]),
        "Pantry": sum(1 for lot in active if "Pantry" in lot["location_name"] or "Shelf" in lot["location_name"]),
    }


def recipe_matches(core: PantryCore, recipes: list[dict[str, Any]], max_missing: int) -> list[dict[str, Any]]:
    with closing(core.connect()) as connection:
        lot_rows = connection.execute("SELECT * FROM inventory_lots WHERE status = 'active' AND CAST(quantity AS REAL) > 0").fetchall()
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
        lots = connection.execute("SELECT * FROM inventory_lots WHERE status = 'active' AND CAST(quantity AS REAL) > 0").fetchall()
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
    rate_limiter: RateLimiter
    session_store: BrowserSessionStore
    secure_browser_cookies: bool = False

    def handle_one_request(self) -> None:
        self._pantryos_started_at = time.monotonic()
        super().handle_one_request()

    def do_OPTIONS(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self._origin_allowed():
                self._send_problem(
                    HTTPStatus.FORBIDDEN,
                    "Cross-origin browser requests are not allowed.",
                    code="origin_forbidden",
                    title="Forbidden",
                )
                return
            self._send_empty_response(
                HTTPStatus.NO_CONTENT,
                headers={
                    **self._cors_headers(),
                    "Allow": "GET, POST, PATCH, DELETE, OPTIONS",
                    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, X-CSRF-Token, X-Request-ID, Authorization",
                    "Access-Control-Allow-Credentials": "true",
                    "Access-Control-Max-Age": "600",
                },
            )
            return
        self._send_empty_response(HTTPStatus.NO_CONTENT, headers={"Allow": "GET, POST, PATCH, DELETE, OPTIONS"})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if not self._authorize(parsed.path):
            return
        if parsed.path == "/api/session":
            self._send_session_status()
            return
        if parsed.path == "/api/v1/openapi.json":
            self._send_json(openapi_document())
            return
        if parsed.path == "/api/v1/events":
            self._send_event_stream(parsed)
            return
        if parsed.path == "/api/v1/inventory/events":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["25"])[0])
            after = query.get("after_revision", [None])[0]
            self._send_json(self.core.events(limit=limit, after_revision=int(after) if after else None))
            return
        event_id = event_detail_path(parsed.path)
        if event_id is not None:
            self._send_json(self.core.event(event_id))
            return
        if parsed.path in ("/api/state", "/api/v1/dashboard"):
            self._send_json(public_state(self.core))
            return
        if parsed.path in ("/api/locations", "/api/v1/locations"):
            self._send_json(self.core.list_locations())
            return
        if parsed.path == "/api/v1/locations/summary":
            summary = self.core.dashboard()["summary"]
            self._send_json(
                {
                    "counts": summary["location_counts"],
                    "values": summary["location_values"],
                    "locations": summary["locations"],
                    "currency": "USD",
                }
            )
            return
        if parsed.path == "/api/v1/waste/monthly":
            summary = self.core.dashboard()["summary"]
            self._send_json({"food_waste_this_month": summary["food_waste_this_month"], "currency": "USD"})
            return
        cooking_session_id = cooking_session_path(parsed.path)
        if cooking_session_id is not None:
            self._send_json(self.core.cooking_session(cooking_session_id))
            return
        if parsed.path == "/api/v1/leftovers":
            self._send_json(
                {
                    "items": [
                        lot_to_item(row)
                        for row in public_state(self.core)["core"]["lots"]
                        if row["status"] == "active" and row["lot_type"] == "leftover"
                    ]
                }
            )
            return
        receipt_review_id = receipt_review_path(parsed.path)
        if receipt_review_id is not None:
            self._send_json(self.core.receipt_review(receipt_review_id))
            return
        purchase_id = purchase_path(parsed.path)
        if purchase_id is not None:
            self._send_json(self.core.purchase(purchase_id))
            return
        product_prices_id = product_prices_path(parsed.path)
        if product_prices_id is not None:
            self._send_json(self.core.product_prices(product_prices_id))
            return
        if parsed.path in ("/api/purchases", "/api/v1/purchases"):
            self._send_json({"items": self.core.purchases()})
            return
        barcode = barcode_lookup_path(parsed.path)
        if barcode is not None:
            self._send_json(self.core.resolve_barcode(barcode))
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
            self._enforce_rate_limit(parsed.path)
            body = self._read_json()
            if parsed.path == "/api/session/login":
                self._handle_session_login(body)
                return
            if parsed.path == "/api/session/logout":
                self._handle_session_logout()
                return
            if parsed.path in ("/api/items", "/api/v1/inventory/lots"):
                result = self.core.add_inventory_lot(body)
                self._send_json({"item": lot_to_item(result["lot"]), "revision": result["revision"]}, HTTPStatus.CREATED)
                return
            if parsed.path in ("/api/barcodes/mappings", "/api/v1/barcodes/mappings"):
                self._send_json(self.core.save_barcode_mapping(body), HTTPStatus.CREATED)
                return
            if parsed.path in ("/api/receipts", "/api/v1/receipts"):
                self._send_json(self.core.upload_receipt(body), HTTPStatus.CREATED)
                return
            receipt_action = receipt_action_path(parsed.path)
            if receipt_action is not None:
                receipt_id, action = receipt_action
                if action == "extract":
                    self._send_json(self.core.extract_receipt(receipt_id))
                    return
                if action == "commit":
                    result = self.core.commit_receipt(receipt_id, body)
                    self._send_json({**result, "lots": [lot_to_item(row) for row in result["lots"]]}, HTTPStatus.CREATED)
                    return
                if action == "reject":
                    self._send_json(self.core.reject_receipt(receipt_id, reason=str(body.get("reason") or "rejected")))
                    return
            barcode_add = barcode_add_lot_path(parsed.path)
            if barcode_add is not None:
                result = self.core.add_lot_from_barcode(barcode_add, body)
                self._send_json({**result, "item": lot_to_item(result["lot"])}, HTTPStatus.CREATED)
                return
            if parsed.path in ("/api/cooking/sessions", "/api/v1/cooking/sessions"):
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
                if action == "open":
                    self._send_json(open_lot(self.core, lot_id, body.get("opened_at")))
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
            if parsed.path.startswith("/api/items/") and parsed.path.endswith("/open"):
                lot_id = parsed.path.removeprefix("/api/items/").removesuffix("/open")
                self._send_json(open_lot(self.core, lot_id, body.get("opened_at")))
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
            if parsed.path in ("/api/shopping/rebuild", "/api/v1/shopping/rebuild"):
                result = self.core.rebuild_shopping_demand()
                self._send_json({**result, "items": [shopping_to_legacy(row) for row in result["items"]]})
                return
            if parsed.path in ("/api/shopping/complete-purchase", "/api/v1/shopping/complete-purchase"):
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
        except RateLimitExceeded as exc:
            self._send_problem(
                HTTPStatus.TOO_MANY_REQUESTS,
                str(exc),
                code="rate_limited",
                title="Rate limited",
                headers={"Retry-After": str(exc.retry_after_seconds)},
            )
            return
        except RequestBodyTooLarge as exc:
            self._send_problem(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, str(exc), code="request_body_too_large", title="Request body too large")
            return
        except UnsupportedMediaType as exc:
            self._send_problem(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, str(exc), code="unsupported_media_type", title="Unsupported media type")
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
            self._enforce_rate_limit(parsed.path)
            body = self._read_json()
            receipt_id = receipt_review_path(parsed.path)
            if receipt_id is not None:
                self._send_json(self.core.update_receipt_review(receipt_id, body))
                return
            recipe_id = recipe_path(parsed.path)
            if recipe_id is not None:
                self._send_json(update_recipe(self.core, recipe_id, body))
                return
            product_id = product_path(parsed.path)
            if product_id is not None:
                self._send_json(update_product(self.core, product_id, body))
                return
            location_id = location_path(parsed.path)
            if location_id is not None:
                self._send_json(self.core.update_location(location_id, body))
                return
            shopping_id = shopping_item_path(parsed.path)
            if shopping_id is not None:
                result = self.core.update_shopping_item(shopping_id, body)
                self._send_json({**result, "item": shopping_to_legacy(result["item"])})
                return
        except json.JSONDecodeError:
            self._send_problem(HTTPStatus.BAD_REQUEST, "Request body must be valid JSON.", code="invalid_json", title="Invalid JSON")
            return
        except KeyError as exc:
            self._send_problem(
                HTTPStatus.BAD_REQUEST, f"Missing required field: {exc.args[0]}", code="missing_field", title="Missing field"
            )
            return
        except PantryOSError as exc:
            self._send_problem(domain_status(exc), str(exc), code=problem_code(exc), title=problem_title(exc))
            return
        except RateLimitExceeded as exc:
            self._send_problem(
                HTTPStatus.TOO_MANY_REQUESTS,
                str(exc),
                code="rate_limited",
                title="Rate limited",
                headers={"Retry-After": str(exc.retry_after_seconds)},
            )
            return
        except RequestBodyTooLarge as exc:
            self._send_problem(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, str(exc), code="request_body_too_large", title="Request body too large")
            return
        except UnsupportedMediaType as exc:
            self._send_problem(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, str(exc), code="unsupported_media_type", title="Unsupported media type")
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
            recipe_id = recipe_path(parsed.path)
            if recipe_id is not None:
                self._send_json(delete_recipe(self.core, recipe_id))
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

    def _enforce_rate_limit(self, path: str) -> None:
        bucket = rate_limit_bucket(path)
        if bucket is None:
            return
        client = self.client_address[0] if self.client_address else "local"
        self.rate_limiter.check(client=client, bucket=bucket)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer") from exc
        if length < 0:
            raise ValueError("Content-Length must be non-negative")
        if length == 0:
            return {}
        if length > MAX_REQUEST_BODY_BYTES:
            raise RequestBodyTooLarge(f"Request body exceeds {MAX_REQUEST_BODY_BYTES} bytes")
        if not is_json_content_type(self.headers.get("Content-Type", "")):
            raise UnsupportedMediaType("Request body must use application/json")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def _authorize(self, path: str) -> bool:
        if path in SESSION_ENDPOINTS:
            if self._origin_allowed():
                return True
            self._send_problem(
                HTTPStatus.FORBIDDEN,
                "Cross-origin browser requests are not allowed.",
                code="origin_forbidden",
                title="Forbidden",
            )
            return False
        if is_versioned_api(path) and path not in PUBLIC_V1_ENDPOINTS:
            return self._authorize_bearer()
        if is_browser_api(path):
            if not self._origin_allowed():
                self._send_problem(
                    HTTPStatus.FORBIDDEN,
                    "Cross-origin browser requests are not allowed.",
                    code="origin_forbidden",
                    title="Forbidden",
                )
                return False
            session = self._current_browser_session()
            if session is None:
                self._send_problem(
                    HTTPStatus.UNAUTHORIZED,
                    "A valid PantryOS browser session is required.",
                    code="browser_session_required",
                    title="Unauthorized",
                )
                return False
            if is_unsafe_method(self.command) and not self._csrf_matches(session):
                self._send_problem(
                    HTTPStatus.FORBIDDEN,
                    "A valid CSRF token is required.",
                    code="csrf_required",
                    title="Forbidden",
                )
                return False
            self._pantryos_browser_session = session
        return True

    def _authorize_bearer(self) -> bool:
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

    def _handle_session_login(self, body: dict[str, Any]) -> None:
        token = self.api_token
        if not token:
            self._send_problem(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "PANTRYOS_API_TOKEN is required before starting a browser session.",
                code="auth_not_configured",
                title="Authentication not configured",
            )
            return
        supplied_token = str(body.get("token") or "")
        if not secrets.compare_digest(supplied_token, token):
            self._send_problem(
                HTTPStatus.UNAUTHORIZED,
                "A valid setup token is required.",
                code="unauthorized",
                title="Unauthorized",
            )
            return
        session = self.session_store.create()
        self._send_session_status(session, headers={"Set-Cookie": self._session_cookie(str(session["id"]))})

    def _handle_session_logout(self) -> None:
        self.session_store.delete(self._session_id())
        self._send_json(
            {"ok": True, "authenticated": False},
            headers={"Set-Cookie": self._clear_session_cookie()},
        )

    def _send_session_status(self, session: dict[str, str | float] | None = None, *, headers: dict[str, str] | None = None) -> None:
        current = session or self._current_browser_session()
        authenticated = current is not None
        self._send_json(
            {
                "authenticated": authenticated,
                "setup_token_configured": bool(self.api_token),
                "csrf_token": str(current["csrf_token"]) if current else "",
                "expires_in_seconds": max(0, int(float(current["expires_at"]) - time.time())) if current else 0,
                "cookie": {
                    "name": BROWSER_SESSION_COOKIE,
                    "http_only": True,
                    "same_site": "Lax",
                    "secure": self._browser_cookie_secure(),
                },
            },
            headers=headers,
        )

    def _current_browser_session(self) -> dict[str, str | float] | None:
        cached = getattr(self, "_pantryos_browser_session", None)
        if cached is not None:
            return cached
        session = self.session_store.get(self._session_id())
        if session is not None:
            self._pantryos_browser_session = session
        return session

    def _session_id(self) -> str | None:
        return self._cookies().get(BROWSER_SESSION_COOKIE)

    def _cookies(self) -> dict[str, str]:
        cookies: dict[str, str] = {}
        for part in self.headers.get("Cookie", "").split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name:
                cookies[name] = value
        return cookies

    def _csrf_matches(self, session: dict[str, str | float]) -> bool:
        supplied = self.headers.get("X-CSRF-Token", "")
        expected = str(session["csrf_token"])
        return bool(supplied) and secrets.compare_digest(supplied, expected)

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed_origin = urlparse(origin)
        if parsed_origin.scheme not in {"http", "https"} or not parsed_origin.netloc:
            return False
        return parsed_origin.netloc.casefold() == self.headers.get("Host", "").casefold()

    def _cors_headers(self) -> dict[str, str]:
        origin = self.headers.get("Origin")
        if not origin or not self._origin_allowed():
            return {}
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}

    def _session_cookie(self, session_id: str) -> str:
        secure = "; Secure" if self._browser_cookie_secure() else ""
        return f"{BROWSER_SESSION_COOKIE}={session_id}; Path=/; Max-Age={self.session_store.ttl_seconds}; HttpOnly; SameSite=Lax{secure}"

    def _clear_session_cookie(self) -> str:
        secure = "; Secure" if self._browser_cookie_secure() else ""
        return f"{BROWSER_SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{secure}"

    def _browser_cookie_secure(self) -> bool:
        if self.secure_browser_cookies:
            return True
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().casefold()
        return forwarded_proto == "https"

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

    def _log_response(self, status: HTTPStatus) -> None:
        if getattr(self, "_pantryos_response_logged", False):
            return
        self._pantryos_response_logged = True
        started_at = getattr(self, "_pantryos_started_at", time.monotonic())
        record = {
            "event": "http.request",
            "request_id": self._request_id(),
            "method": getattr(self, "command", ""),
            "path": urlparse(self.path).path,
            "status": status.value,
            "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
            "client": self.client_address[0] if self.client_address else "local",
        }
        LOGGER.info(json.dumps(record, separators=(",", ":"), sort_keys=True))

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

    def _send_event_stream(self, parsed: Any) -> None:
        query = parse_qs(parsed.query)
        last_event_id = self.headers.get("Last-Event-ID")
        after_revision: int | None = None
        query_after = query.get("after_revision", [None])[0]
        if query_after and str(query_after).isdecimal():
            after_revision = int(query_after)
        if last_event_id and last_event_id.isdecimal():
            after_revision = int(last_event_id)
        stream_seconds = _bounded_float(
            query.get("timeout", [None])[0],
            default=DEFAULT_EVENT_STREAM_SECONDS,
            minimum=0.0,
            maximum=MAX_EVENT_STREAM_SECONDS,
        )
        heartbeat_seconds = _bounded_float(
            query.get("heartbeat", [None])[0],
            default=DEFAULT_EVENT_HEARTBEAT_SECONDS,
            minimum=MIN_EVENT_HEARTBEAT_SECONDS,
            maximum=max(DEFAULT_EVENT_HEARTBEAT_SECONDS, stream_seconds or DEFAULT_EVENT_HEARTBEAT_SECONDS),
        )
        events = self.core.events(limit=25, after_revision=after_revision)
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        hello = {"type": "hello", "revision": events["revision"], "retry_ms": 5000}
        self._write_sse("pantryos.hello", str(events["revision"]), hello)
        last_sent_revision = after_revision or 0
        for event in events["items"]:
            self._write_sse(event["type"], str(event["revision"]), event)
            last_sent_revision = max(last_sent_revision, int(event["revision"]))
        deadline = time.monotonic() + stream_seconds
        next_heartbeat = time.monotonic()
        while time.monotonic() < deadline:
            if time.monotonic() >= next_heartbeat:
                self.wfile.write(f": heartbeat {int(time.time())}\n\n".encode())
                self.wfile.flush()
                next_heartbeat = time.monotonic() + heartbeat_seconds
            events = self.core.events(limit=25, after_revision=last_sent_revision)
            for event in events["items"]:
                self._write_sse(event["type"], str(event["revision"]), event)
                last_sent_revision = max(last_sent_revision, int(event["revision"]))
            if events["items"]:
                self.wfile.flush()
            time.sleep(min(0.25, heartbeat_seconds, max(0.0, deadline - time.monotonic())))
        self.wfile.flush()
        self._log_response(HTTPStatus.OK)

    def _write_sse(self, event_type: str, event_id: str, data: dict[str, Any]) -> None:
        payload = json.dumps(data, separators=(",", ":"))
        self.wfile.write(f"id: {event_id}\n".encode())
        self.wfile.write(f"event: {event_type}\n".encode())
        self.wfile.write(f"data: {payload}\n\n".encode())

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
        self._log_response(status)

    def _send_empty_response(self, status: HTTPStatus, *, headers: dict[str, str] | None = None) -> None:
        self.send_response(status.value)
        self.send_header("Content-Length", "0")
        self.send_header("X-Request-ID", self._request_id())
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self._log_response(status)

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
        content_type = static_content_type(resolved)
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)
        self._log_response(HTTPStatus.OK)


def static_content_type(path: Path) -> str:
    if path.suffix == ".webmanifest":
        return "application/manifest+json"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def is_versioned_api(path: str) -> bool:
    return path == "/api/v1" or path.startswith("/api/v1/")


def is_browser_api(path: str) -> bool:
    return (path == "/api" or path.startswith("/api/")) and not is_versioned_api(path) and path not in SESSION_ENDPOINTS


def is_unsafe_method(method: str) -> bool:
    return method.upper() not in {"GET", "HEAD", "OPTIONS"}


def rate_limit_bucket(path: str) -> str | None:
    if path in {"/api/receipts", "/api/v1/receipts"}:
        return "receipt_upload"
    action = receipt_action_path(path)
    if action is not None and action[1] == "extract":
        return "receipt_extract"
    return None


def is_json_content_type(value: str) -> bool:
    media_type = value.split(";", 1)[0].strip().casefold()
    return media_type == "application/json" or media_type.endswith("+json")


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
    if len(parts) != 2 or not parts[0] or parts[1] not in {"consume", "move", "discard", "open"}:
        return None
    return unquote(parts[0]), parts[1]


def recipe_shopping_path(path: str) -> str | None:
    for prefix in ("/api/recipes/", "/api/v1/recipes/"):
        if path.startswith(prefix) and path.endswith("/shopping"):
            return unquote(path.removeprefix(prefix).removesuffix("/shopping"))
    return None


def recipe_path(path: str) -> str | None:
    for prefix in ("/api/recipes/", "/api/v1/recipes/"):
        if not path.startswith(prefix):
            continue
        suffix = path.removeprefix(prefix)
        if not suffix or "/" in suffix:
            return None
        return unquote(suffix)
    return None


def event_detail_path(path: str) -> str | None:
    prefix = "/api/v1/events/"
    if not path.startswith(prefix):
        return None
    suffix = path.removeprefix(prefix)
    if not suffix or "/" in suffix:
        return None
    return unquote(suffix)


def receipt_review_path(path: str) -> str | None:
    for prefix in ("/api/receipts/", "/api/v1/receipts/"):
        if not path.startswith(prefix):
            continue
        suffix = path.removeprefix(prefix)
        parts = suffix.split("/", 1)
        if len(parts) == 2 and parts[0] and parts[1] == "review":
            return unquote(parts[0])
    return None


def receipt_action_path(path: str) -> tuple[str, str] | None:
    for prefix in ("/api/receipts/", "/api/v1/receipts/"):
        if not path.startswith(prefix):
            continue
        suffix = path.removeprefix(prefix)
        parts = suffix.split("/", 1)
        if len(parts) != 2 or not parts[0] or parts[1] not in {"extract", "commit", "reject"}:
            return None
        return unquote(parts[0]), parts[1]
    return None


def purchase_path(path: str) -> str | None:
    for prefix in ("/api/purchases/", "/api/v1/purchases/"):
        if not path.startswith(prefix):
            continue
        suffix = path.removeprefix(prefix)
        if not suffix or "/" in suffix:
            return None
        return unquote(suffix)
    return None


def product_prices_path(path: str) -> str | None:
    for prefix in ("/api/products/", "/api/v1/products/"):
        if not path.startswith(prefix) or not path.endswith("/prices"):
            continue
        suffix = path.removeprefix(prefix).removesuffix("/prices")
        if not suffix or "/" in suffix:
            return None
        return unquote(suffix)
    return None


def product_path(path: str) -> str | None:
    for prefix in ("/api/products/", "/api/v1/products/"):
        if not path.startswith(prefix):
            continue
        suffix = path.removeprefix(prefix)
        if not suffix or "/" in suffix:
            return None
        return unquote(suffix)
    return None


def location_path(path: str) -> str | None:
    for prefix in ("/api/locations/", "/api/v1/locations/"):
        if not path.startswith(prefix):
            continue
        suffix = path.removeprefix(prefix)
        if not suffix or "/" in suffix or suffix == "summary":
            return None
        return unquote(suffix)
    return None


def barcode_lookup_path(path: str) -> str | None:
    for prefix in ("/api/barcodes/", "/api/v1/barcodes/"):
        if not path.startswith(prefix):
            continue
        suffix = path.removeprefix(prefix)
        if not suffix or "/" in suffix or suffix == "mappings":
            return None
        return unquote(suffix)
    return None


def barcode_add_lot_path(path: str) -> str | None:
    for prefix in ("/api/barcodes/", "/api/v1/barcodes/"):
        if not path.startswith(prefix):
            continue
        suffix = path.removeprefix(prefix)
        parts = suffix.split("/", 1)
        if len(parts) != 2 or not parts[0] or parts[1] != "add-lot":
            return None
        return unquote(parts[0])
    return None


def cooking_session_path(path: str) -> str | None:
    for prefix in ("/api/cooking/sessions/", "/api/v1/cooking/sessions/"):
        if not path.startswith(prefix):
            continue
        suffix = path.removeprefix(prefix)
        if not suffix or "/" in suffix:
            return None
        return unquote(suffix)
    return None


def cooking_action_path(path: str) -> tuple[str, str] | None:
    for prefix in ("/api/cooking/sessions/", "/api/v1/cooking/sessions/"):
        if not path.startswith(prefix):
            continue
        parts = path.removeprefix(prefix).split("/")
        if len(parts) != 2 or not parts[0] or parts[1] not in {"complete", "cancel"}:
            return None
        return unquote(parts[0]), parts[1]
    return None


def shopping_item_path(path: str) -> str | None:
    for prefix in ("/api/shopping/", "/api/v1/shopping/"):
        if not path.startswith(prefix):
            continue
        suffix = path.removeprefix(prefix)
        if not suffix or "/" in suffix or suffix in {"manual", "rebuild", "complete-purchase", "promote-suggestions"}:
            return None
        return unquote(suffix)
    return None


def shopping_action_path(path: str) -> tuple[str, str] | None:
    for prefix in ("/api/shopping/", "/api/v1/shopping/"):
        if not path.startswith(prefix):
            continue
        parts = path.removeprefix(prefix).split("/")
        if len(parts) != 2 or not parts[0] or parts[1] not in {"check", "uncheck"}:
            return None
        return unquote(parts[0]), parts[1]
    return None


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
    return core.discard_lot(lot_id, reason=reason, source="api")


def open_lot(core: PantryCore, lot_id: str, opened_at: str | None = None) -> dict[str, Any]:
    result = core.open_lot(lot_id, opened_at=opened_at, source="api")
    return {"item": lot_to_item(result["lot"]), "opened": result["opened"], "revision": result["revision"]}


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


def update_recipe(core: PantryCore, recipe_id: str, body: dict[str, Any]) -> dict[str, Any]:
    result = core.update_recipe(recipe_id, body, source="api")
    return {"recipe": recipe_to_legacy(result["recipe"]), "revision": result["revision"]}


def update_product(core: PantryCore, product_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return core.update_product(product_id, body, source="api")


def delete_recipe(core: PantryCore, recipe_id: str) -> dict[str, Any]:
    return core.delete_recipe(recipe_id, source="api")


def plan_meal(core: PantryCore, day: str, recipe_name: str) -> dict[str, Any]:
    core.migrate()
    with core.transaction() as connection:
        recipe = connection.execute(
            "SELECT id FROM recipes WHERE normalized_name = ? AND active = 1",
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
            "SELECT id FROM recipes WHERE normalized_name = ? AND active = 1",
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


def _bounded_float(value: str | None, *, default: float, minimum: float, maximum: float) -> float:
    if value in (None, ""):
        return default
    try:
        numeric = float(value)
    except ValueError:
        return default
    return max(minimum, min(numeric, maximum))


def _env_bool(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _browser_session_store_path(db_path: Path) -> Path | None:
    configured = os.environ.get("PANTRYOS_BROWSER_SESSION_STORE")
    if configured and configured.strip().casefold() in {"memory", ":memory:", "none", "off"}:
        return None
    if configured:
        return Path(configured)
    return db_path.parent / "browser_sessions.json"


def make_server(host: str, port: int, db_path: Path) -> ThreadingHTTPServer:
    handler = type("ConfiguredPantryRequestHandler", (PantryRequestHandler,), {})
    handler.core = PantryCore(db_path)
    handler.api_token = os.environ.get("PANTRYOS_API_TOKEN")
    handler.rate_limiter = RateLimiter(
        limit=int(os.environ.get("PANTRYOS_RATE_LIMIT_REQUESTS", str(DEFAULT_RATE_LIMIT_REQUESTS))),
        window_seconds=int(os.environ.get("PANTRYOS_RATE_LIMIT_WINDOW_SECONDS", str(DEFAULT_RATE_LIMIT_WINDOW_SECONDS))),
    )
    handler.session_store = BrowserSessionStore(
        ttl_seconds=int(os.environ.get("PANTRYOS_BROWSER_SESSION_SECONDS", str(DEFAULT_BROWSER_SESSION_SECONDS))),
        storage_path=_browser_session_store_path(db_path),
    )
    handler.secure_browser_cookies = _env_bool(os.environ.get("PANTRYOS_BROWSER_SECURE_COOKIES"))
    handler.core.migrate()
    if LEGACY_JSON_PATH.exists() and handler.core.dashboard()["summary"]["product_count"] == 0:
        handler.core.import_legacy_json(LEGACY_JSON_PATH)
    return PantryHTTPServer((host, port), handler)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _default_host() -> str:
    return os.environ.get("PANTRYOS_LISTEN_HOST") or os.environ.get("PANTRYOS_HOST") or "127.0.0.1"


def _default_data_path() -> Path:
    configured = os.environ.get("PANTRYOS_DATABASE_PATH") or os.environ.get("PANTRYOS_DATA_PATH")
    return Path(configured) if configured else DEFAULT_DB_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PantryOS local app")
    parser.add_argument("--host", default=_default_host())
    parser.add_argument("--port", type=int, default=_env_int("PANTRYOS_LISTEN_PORT", _env_int("PANTRYOS_PORT", 8765)))
    parser.add_argument("--data", type=Path, default=_default_data_path())
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    data_path = (
        path_within(args.data, os.environ["PANTRYOS_DATA_DIR"], "Database path") if os.environ.get("PANTRYOS_DATA_DIR") else args.data
    )
    server = make_server(args.host, args.port, data_path)
    print(f"PantryOS running at http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
