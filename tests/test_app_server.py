import json
import os
import sys
import threading
from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

_SERVER_PATH = Path(__file__).resolve().parents[1] / "app" / "server.py"
_SPEC = spec_from_file_location("pantryos_server", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
server_module = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = server_module
_SPEC.loader.exec_module(server_module)


@contextmanager
def api_token(value: str):
    original = os.environ.get("PANTRYOS_API_TOKEN")
    os.environ["PANTRYOS_API_TOKEN"] = value
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("PANTRYOS_API_TOKEN", None)
        else:
            os.environ["PANTRYOS_API_TOKEN"] = original


def request_json(
    url: str,
    method: str = "GET",
    payload: dict | None = None,
    *,
    token: str | None = None,
    request_id: str | None = None,
) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method)
    request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    if request_id is not None:
        request.add_header("X-Request-ID", request_id)
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def request_error(
    url: str,
    method: str = "GET",
    payload: dict | None = None,
    *,
    token: str | None = None,
    request_id: str | None = None,
) -> tuple[int, dict]:
    try:
        request_json(url, method=method, payload=payload, token=token, request_id=request_id)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    raise AssertionError("Expected request to fail")


def request_raw_error(
    url: str,
    body: bytes,
    *,
    token: str | None = None,
    request_id: str | None = None,
) -> tuple[int, dict]:
    request = Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    if request_id is not None:
        request.add_header("X-Request-ID", request_id)
    try:
        with urlopen(request, timeout=5) as response:
            response.read()
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    raise AssertionError("Expected request to fail")


def test_seed_core_builds_vertical_slice_state() -> None:
    with TemporaryDirectory() as directory:
        core = server_module.PantryCore(Path(directory) / "pantryos.sqlite3")

        state = server_module.seed_core(core, reset=True)

        assert state["summary"]["total_items"] == 7
        assert state["summary"]["suggested_purchase_count"] == 2
        assert state["meal_plan"]["Tonight"] == "Spinach Omelette"
        assert [item["name"] for item in state["leftovers"]] == ["Taco Meat"]
        assert "Chicken Alfredo" in [meal["name"] for meal in state["meals_with_two_or_fewer_missing"]]
        assert state["core"]["products"]
        assert state["core"]["events"]


def test_core_backed_server_persists_mutations() -> None:
    with TemporaryDirectory() as directory:
        core = server_module.PantryCore(Path(directory) / "pantryos.sqlite3")
        server_module.seed_core(core, reset=True)
        eggs = next(product for product in core.dashboard()["products"] if product["name"] == "Eggs")

        core.consume_product(product_id=eggs["id"], quantity="4", unit="count")

        updated = server_module.public_state(core)
        eggs_lots = [lot for lot in updated["core"]["lots"] if lot["product_name"] == "Eggs"]
        assert eggs_lots[0]["quantity"] == "0"
        assert eggs_lots[0]["status"] == "closed"
        assert updated["summary"]["suggested_purchases"][0]["name"] == "Eggs"


def test_http_api_serves_state_and_accepts_items() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            request_json(f"{base}/api/seed?reset=true", method="POST")
            created = request_json(
                f"{base}/api/items",
                method="POST",
                payload={
                    "name": "Heavy Cream",
                    "quantity": "1",
                    "unit": "cup",
                    "location": "Kitchen/Refrigerator",
                    "expires": "2026-08-30",
                },
            )
            state = request_json(f"{base}/api/state")
            instance = request_json(f"{base}/api/v1/instance", token="test-token")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert created["item"]["name"] == "Heavy Cream"
    assert any(item["name"] == "Heavy Cream" for item in state["items"])
    assert state["summary"]["total_items"] == 8
    assert instance["schema_version"] == 4
    assert instance["state_revision"] >= 1


def test_v1_api_requires_bearer_token_and_uses_problem_shape() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            ready = request_json(f"{base}/api/v1/health/ready")
            status, unauth = request_error(f"{base}/api/v1/dashboard", request_id="req-test")
            dashboard = request_json(f"{base}/api/v1/dashboard", token="test-token")
            created = request_json(
                f"{base}/api/v1/inventory/lots",
                method="POST",
                token="test-token",
                payload={
                    "name": "Butter",
                    "quantity": "1",
                    "unit": "lb",
                    "location": "Kitchen/Refrigerator",
                    "estimated_cost": "4.00",
                },
            )
            consumed = request_json(
                f"{base}/api/v1/inventory/lots/{created['item']['id']}/consume",
                method="POST",
                token="test-token",
                payload={"quantity": "0.5", "reason": "api test"},
            )
            over_status, over_consume = request_error(
                f"{base}/api/v1/inventory/lots/{created['item']['id']}/consume",
                method="POST",
                token="test-token",
                payload={"quantity": "2", "reason": "api test"},
                request_id="too-much",
            )
            discarded = request_json(
                f"{base}/api/v1/inventory/lots/{created['item']['id']}/discard",
                method="POST",
                token="test-token",
                payload={"reason": "spoiled"},
            )
            locations = request_json(f"{base}/api/v1/locations/summary", token="test-token")
            waste = request_json(f"{base}/api/v1/waste/monthly", token="test-token")
            bad_status, invalid_json = request_raw_error(
                f"{base}/api/v1/inventory/lots",
                b"{",
                token="test-token",
                request_id="bad-json",
            )
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert ready == {"status": "ready"}
    assert status == 401
    assert unauth["code"] == "unauthorized"
    assert unauth["status"] == 401
    assert unauth["request_id"] == "req-test"
    assert dashboard["revision"] >= 0
    assert created["item"]["name"] == "Butter"
    assert consumed["allocations"][0]["lot_id"] == created["item"]["id"]
    assert over_status == 409
    assert over_consume["code"] == "insufficient_inventory"
    assert over_consume["request_id"] == "too-much"
    assert discarded["discarded_value"] == "2.00"
    assert locations["currency"] == "USD"
    assert "Refrigerator" in locations["values"]
    assert waste == {"food_waste_this_month": "2.00", "currency": "USD"}
    assert bad_status == 400
    assert invalid_json["code"] == "invalid_json"
    assert invalid_json["request_id"] == "bad-json"


def test_barcode_api_and_browser_routes_map_and_add_lots() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            unknown = request_json(f"{base}/api/barcodes/000111222333")
            mapping = request_json(
                f"{base}/api/barcodes/mappings",
                method="POST",
                payload={
                    "barcode": "000111222333",
                    "name": "Browser Barcode Soup",
                    "package_quantity": "2",
                    "package_unit": "can",
                },
            )
            browser_added = request_json(
                f"{base}/api/barcodes/000111222333/add-lot",
                method="POST",
                payload={"location": "Kitchen/Pantry", "estimated_cost": "4.50"},
            )
            versioned_resolved = request_json(f"{base}/api/v1/barcodes/000111222333", token="test-token")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert unknown == {"matched": False, "barcode": "000111222333"}
    assert mapping["mapping"]["product_name"] == "Browser Barcode Soup"
    assert browser_added["item"]["name"] == "Browser Barcode Soup"
    assert browser_added["item"]["quantity"] == "2"
    assert browser_added["item"]["unit"] == "can"
    assert versioned_resolved["matched"] is True
    assert versioned_resolved["mapping"]["package_unit"] == "can"

def test_browser_routes_complete_purchase_and_cooking_workflows() -> None:
    with TemporaryDirectory() as directory:
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            request_json(f"{base}/api/seed?reset=true", method="POST")
            shopping = request_json(
                f"{base}/api/shopping",
                method="POST",
                payload={"name": "Browser Oats", "quantity": "2", "unit": "count"},
            )
            checked = request_json(f"{base}/api/shopping/{shopping['item']['id']}/check", method="POST")
            purchase = request_json(
                f"{base}/api/shopping/complete-purchase",
                method="POST",
                payload={
                    "store": "Browser Market",
                    "location": "Kitchen/Pantry",
                    "items": [{"shopping_id": shopping["item"]["id"], "quantity": "2"}],
                },
            )
            rice = request_json(
                f"{base}/api/items",
                method="POST",
                payload={"name": "Browser Rice", "quantity": "2", "unit": "cup", "location": "Kitchen/Pantry"},
            )
            request_json(
                f"{base}/api/recipes",
                method="POST",
                payload={
                    "name": "Browser Rice Bowl",
                    "ingredients": [{"name": "Browser Rice", "quantity": "1", "unit": "cup"}],
                },
            )
            request_json(f"{base}/api/meal-plan", method="POST", payload={"day": "Tonight", "recipe_name": "Browser Rice Bowl"})
            started = request_json(
                f"{base}/api/cooking/sessions",
                method="POST",
                payload={"recipe_name": "Browser Rice Bowl", "planned_servings": "1"},
            )
            completed = request_json(
                f"{base}/api/cooking/sessions/{started['session']['id']}/complete",
                method="POST",
                payload={
                    "allocations": [{"lot_id": rice["item"]["id"], "quantity": "1", "unit": "cup"}],
                    "leftovers": [{"name": "Browser Rice Bowl Leftovers", "quantity": "1", "unit": "serving"}],
                },
            )
            state = request_json(f"{base}/api/state")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert checked["item"]["checked"] is True
    assert purchase["purchase"]["store"] == "Browser Market"
    assert purchase["lots"][0]["name"] == "Browser Oats"
    assert started["session"]["status"] == "cooking"
    assert completed["session"]["status"] == "completed"
    assert completed["leftovers"][0]["name"] == "Browser Rice Bowl Leftovers"
    browser_rice = next(item for item in state["items"] if item["name"] == "Browser Rice")
    assert browser_rice["quantity"] == "1"
    assert any(item["name"] == "Browser Rice Bowl Leftovers" for item in state["leftovers"])



def test_receipt_api_review_commit_and_price_history() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            uploaded = request_json(
                f"{base}/api/v1/receipts",
                method="POST",
                token="test-token",
                payload={
                    "filename": "receipt.txt",
                    "mime_type": "text/plain",
                    "text": "Store: API Market\nDate: 2026-08-24\nAPI Apples,3,count,6.00,555000111222\nTotal: 6.00\n",
                },
            )
            extracted = request_json(
                f"{base}/api/v1/receipts/{uploaded['receipt']['id']}/extract",
                method="POST",
                token="test-token",
            )
            review_snapshot = request_json(f"{base}/api/v1/receipts/{uploaded['receipt']['id']}/review", token="test-token")
            review = review_snapshot["review"]
            review["location"] = "Kitchen/Fruit Bowl"
            updated = request_json(
                f"{base}/api/v1/receipts/{uploaded['receipt']['id']}/review",
                method="PATCH",
                token="test-token",
                payload=review,
            )
            committed = request_json(
                f"{base}/api/v1/receipts/{uploaded['receipt']['id']}/commit",
                method="POST",
                token="test-token",
            )
            duplicate = request_json(
                f"{base}/api/v1/receipts/{uploaded['receipt']['id']}/commit",
                method="POST",
                token="test-token",
            )
            purchases = request_json(f"{base}/api/v1/purchases", token="test-token")
            purchase_detail = request_json(f"{base}/api/v1/purchases/{committed['purchase']['id']}", token="test-token")
            product_id = purchase_detail["lines"][0]["product_id"]
            prices = request_json(f"{base}/api/v1/products/{product_id}/prices", token="test-token")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert uploaded["receipt"]["status"] == "uploaded"
    assert "storage_path" not in uploaded["receipt"]
    assert extracted["receipt"]["status"] == "review"
    assert extracted["review"]["items"][0]["name"] == "API Apples"
    assert updated["review"]["location"] == "Kitchen/Fruit Bowl"
    assert committed["purchase"]["store"] == "API Market"
    assert committed["lots"][0]["name"] == "API Apples"
    assert committed["lots"][0]["location"] == "Kitchen/Fruit Bowl"
    assert duplicate["duplicate"] is True
    assert purchases["items"][0]["id"] == committed["purchase"]["id"]
    assert purchase_detail["prices"][0]["unit_price"] == "2.00"
    assert prices["product"]["name"] == "API Apples"
    assert prices["prices"][0]["comparable_unit"] == "count"
def test_static_browser_workflows_are_not_stubbed() -> None:
    app_js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    index_html = (Path(__file__).resolve().parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")

    assert "Cooking mode queued" not in app_js
    assert "/api/cooking/sessions" in app_js
    assert "/api/shopping/complete-purchase" in app_js
    assert "/api/barcodes/" in app_js
    assert "handleBarcodeSubmit" in app_js
    assert "handleStartCooking" in app_js
    assert "handlePurchaseSubmit" in app_js
    assert "id=\"cookingForm\"" in index_html
    assert "id=\"purchaseForm\"" in index_html
    assert "id=\"barcodeForm\"" in index_html
