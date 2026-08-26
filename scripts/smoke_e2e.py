"""Scripted PantryOS v1 vertical-slice smoke demo.

Starts a temporary PantryOS server, signs in through the browser-session surface,
then verifies the same state through the Home Assistant API client. The flow is
kept synthetic and local-only so it is safe to run in checkout and container
release checks.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from contextlib import contextmanager
from datetime import date, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for candidate in (ROOT / "scripts", ROOT, SRC):
    candidate_text = str(candidate)
    while candidate_text in sys.path:
        sys.path.remove(candidate_text)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SRC))

from app import server as server_module  # noqa: E402

_API_CLIENT_SPEC = spec_from_file_location("pantryos_smoke_api_client", ROOT / "custom_components" / "pantryos" / "api_client.py")
if _API_CLIENT_SPEC is None or _API_CLIENT_SPEC.loader is None:
    raise RuntimeError("Could not load PantryOS Home Assistant API client")
_api_client_module = module_from_spec(_API_CLIENT_SPEC)
_API_CLIENT_SPEC.loader.exec_module(_api_client_module)
PantryAPIClient = _api_client_module.PantryAPIClient

TOKEN = os.environ.get("PANTRYOS_API_TOKEN") or "pantryos-e2e-smoke-token"


class SmokeFailure(AssertionError):
    """Raised when the scripted demo cannot prove an expected release step."""


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    cookie: str | None = None,
    csrf_token: str | None = None,
    token: str | None = None,
    origin: str | None = None,
) -> dict[str, Any]:
    data, _headers = request_json_with_headers(
        url,
        method=method,
        payload=payload,
        cookie=cookie,
        csrf_token=csrf_token,
        token=token,
        origin=origin,
    )
    return data


def request_json_with_headers(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    cookie: str | None = None,
    csrf_token: str | None = None,
    token: str | None = None,
    origin: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method)
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if cookie:
        request.add_header("Cookie", cookie)
    if csrf_token:
        request.add_header("X-CSRF-Token", csrf_token)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if origin:
        request.add_header("Origin", origin)
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8")), dict(response.headers.items())
    except HTTPError as exc:
        try:
            problem = json.loads(exc.read().decode("utf-8"))
        except json.JSONDecodeError:
            problem = {"detail": f"HTTP {exc.code}"}
        raise SmokeFailure(f"{method} {url} failed: {exc.code} {problem}") from exc


@contextmanager
def running_server(data_path: Path):
    original_token = os.environ.get("PANTRYOS_API_TOKEN")
    os.environ["PANTRYOS_API_TOKEN"] = TOKEN
    httpd = server_module.make_server("127.0.0.1", 0, data_path)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()
        if original_token is None:
            os.environ.pop("PANTRYOS_API_TOKEN", None)
        else:
            os.environ["PANTRYOS_API_TOKEN"] = original_token


def browser_login(base_url: str) -> tuple[str, str]:
    session, headers = request_json_with_headers(
        f"{base_url}/api/session/login",
        method="POST",
        payload={"token": TOKEN},
        origin=base_url,
    )
    if not session.get("authenticated") or not session.get("csrf_token"):
        raise SmokeFailure(f"Browser login did not create a session: {session}")
    set_cookie = headers.get("Set-Cookie", "")
    if not set_cookie.startswith("pantryos_session="):
        raise SmokeFailure("Browser login did not return a PantryOS session cookie")
    return set_cookie.split(";", 1)[0], str(session["csrf_token"])


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def find_named(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for row in rows:
        if row.get("name") == name or row.get("product_name") == name or row.get("display_name") == name:
            return row
    raise SmokeFailure(f"Could not find {name!r} in rows")


async def run_demo(base_url: str) -> dict[str, Any]:
    cookie, csrf_token = browser_login(base_url)
    ha_client = PantryAPIClient(base_url, TOKEN)
    today = date.today()
    use_by = (today + timedelta(days=1)).isoformat()
    leftover_use_by = (today + timedelta(days=2)).isoformat()

    browser_item = request_json(
        f"{base_url}/api/items",
        method="POST",
        cookie=cookie,
        csrf_token=csrf_token,
        origin=base_url,
        payload={
            "name": "Smoke Rice",
            "quantity": "2",
            "unit": "count",
            "location": "Kitchen/Pantry",
            "expires": use_by,
            "estimated_cost": "4.00",
        },
    )["item"]

    synced = await ha_client.async_refresh()
    synced_item = find_named(synced["items"], "Smoke Rice")
    require(synced_item["id"] == browser_item["id"], "HA client did not observe the browser-created lot")

    await ha_client.async_add_recipe(
        {
            "name": "Smoke Rice Bowl",
            "prep_minutes": 20,
            "ingredients": [
                {"name": "Smoke Rice", "quantity": "1", "unit": "count"},
                {"name": "Smoke Sauce", "quantity": "1", "unit": "count"},
            ],
            "instructions": "Synthetic release smoke recipe.",
        }
    )
    await ha_client.async_plan_meal("Tonight", "Smoke Rice Bowl")
    shopping = await ha_client.async_add_missing_to_shopping_list("Smoke Rice Bowl")
    shopping_item = find_named(shopping["items"], "Smoke Sauce")
    await ha_client.async_check_shopping_item(shopping_item["id"])
    purchase = await ha_client.async_complete_purchase(
        {
            "store": "Smoke Market",
            "location": "Kitchen/Pantry",
            "items": [
                {
                    "shopping_id": shopping_item["id"],
                    "quantity": "1",
                    "unit": "count",
                    "total_cost": "3.50",
                }
            ],
        }
    )
    require(purchase["purchase"]["store"] == "Smoke Market", "Purchase completion did not record store")
    require(
        any((lot.get("product_name") or lot.get("name")) == "Smoke Sauce" for lot in purchase["lots"]),
        "Purchase did not create Smoke Sauce lot",
    )

    started = await ha_client.async_start_cooking_session({"recipe_name": "Smoke Rice Bowl", "planned_servings": "1"})
    session_id = started["session"]["id"]
    completed = await ha_client.async_complete_cooking_session(
        session_id,
        {
            "allocations": [{"lot_id": browser_item["id"], "quantity": "1", "unit": "count"}],
            "actual_servings": "1",
            "leftovers": [
                {
                    "name": "Smoke Rice Bowl Leftovers",
                    "quantity": "1",
                    "unit": "serving",
                    "location": "Kitchen/Refrigerator",
                    "use_by": leftover_use_by,
                }
            ],
        },
    )
    require(completed["session"]["status"] == "completed", "Cooking session did not complete")
    require(completed["leftovers"], "Cooking completion did not create leftovers")

    browser_state = request_json(f"{base_url}/api/state", cookie=cookie)
    leftovers = browser_state["leftovers"]
    use_soon_names = {row["name"] for row in browser_state["summary"]["expiring_soon"]}
    require(any(row["name"] == "Smoke Rice Bowl Leftovers" for row in leftovers), "Browser state did not show created leftover")
    require("Smoke Rice" in use_soon_names, "Use-soon summary did not include the expiring browser-created item")
    require(browser_state["meal_plan"].get("Tonight") == "Smoke Rice Bowl", "Meal plan was not visible through browser state")

    events = await ha_client.async_events(limit=12)
    event_types = {row.get("event_type") or row.get("type") for row in events["items"]}
    require("cooking.started" in event_types, "Event history did not include cooking.started")
    require("cooking.completed" in event_types, "Event history did not include cooking.completed")

    return {
        "ok": True,
        "base_url": base_url,
        "revision": browser_state["summary"]["state_revision"],
        "browser_added_lot_id": browser_item["id"],
        "ha_synced_lot_id": synced_item["id"],
        "shopping_item_id": shopping_item["id"],
        "purchase_id": purchase["purchase"]["id"],
        "cooking_session_id": session_id,
        "leftover_count": browser_state["summary"]["leftover_count"],
        "use_soon": sorted(use_soon_names),
        "event_types": sorted(str(event_type) for event_type in event_types if event_type),
    }


def main() -> None:
    with TemporaryDirectory() as directory:
        data_path = Path(directory) / "pantryos-e2e.sqlite3"
        with running_server(data_path) as base_url:
            result = asyncio.run(run_demo(base_url))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
