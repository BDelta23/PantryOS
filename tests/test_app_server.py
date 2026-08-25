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


def test_seed_manager_builds_vertical_slice_state() -> None:
    manager = server_module.seed_manager()
    state = server_module.public_state(manager)

    assert state["summary"]["total_items"] == 7
    assert state["summary"]["suggested_purchase_count"] == 2
    assert state["meal_plan"]["Tonight"] == "Spinach Omelette"
    assert [item["name"] for item in state["leftovers"]] == ["Taco Meat"]
    assert "Chicken Alfredo" in [meal["name"] for meal in state["meals_with_two_or_fewer_missing"]]


def test_repository_persists_mutations() -> None:
    with TemporaryDirectory() as directory:
        repository = server_module.JsonInventoryRepository(Path(directory) / "pantry.json")
        repository.save(server_module.seed_manager())
        manager = repository.load()
        item = next(item for item in manager.state.items if item.name == "Eggs")
        repository.mutate(lambda loaded: loaded.consume_item(item.id, server_module.Decimal("4")))

        updated = repository.load()
        eggs = next(item for item in updated.state.items if item.name == "Eggs")
        assert eggs.quantity == server_module.Decimal("0")
        assert updated.suggested_purchases()[0]["name"] == "Eggs"


def test_http_api_serves_state_and_accepts_items() -> None:
    with TemporaryDirectory() as directory:
        data_path = Path(directory) / "pantry.json"
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
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert created["item"]["name"] == "Heavy Cream"
    assert any(item["name"] == "Heavy Cream" for item in state["items"])
    assert state["summary"]["total_items"] == 8
