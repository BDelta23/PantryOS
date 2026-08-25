import json
import sys
import threading
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen

_SERVER_PATH = Path(__file__).resolve().parents[1] / "app" / "server.py"
_SPEC = spec_from_file_location("pantryos_server", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
server_module = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = server_module
_SPEC.loader.exec_module(server_module)


def request_json(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method)
    request.add_header("Content-Type", "application/json")
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


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
    with TemporaryDirectory() as directory:
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
            instance = request_json(f"{base}/api/v1/instance")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert created["item"]["name"] == "Heavy Cream"
    assert any(item["name"] == "Heavy Cream" for item in state["items"])
    assert state["summary"]["total_items"] == 8
    assert instance["schema_version"] == 1
    assert instance["state_revision"] >= 1
