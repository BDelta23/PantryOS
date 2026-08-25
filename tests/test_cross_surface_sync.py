import asyncio
import json
import os
import sys
import threading
from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen

_SERVER_PATH = Path(__file__).resolve().parents[1] / "app" / "server.py"
_SERVER_SPEC = spec_from_file_location("pantryos_server_for_sync_tests", _SERVER_PATH)
assert _SERVER_SPEC is not None and _SERVER_SPEC.loader is not None
server_module = module_from_spec(_SERVER_SPEC)
sys.modules[_SERVER_SPEC.name] = server_module
_SERVER_SPEC.loader.exec_module(server_module)

_CLIENT_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "pantryos" / "api_client.py"
_CLIENT_SPEC = spec_from_file_location("pantryos_api_client_for_sync_tests", _CLIENT_PATH)
assert _CLIENT_SPEC is not None and _CLIENT_SPEC.loader is not None
client_module = module_from_spec(_CLIENT_SPEC)
sys.modules[_CLIENT_SPEC.name] = client_module
_CLIENT_SPEC.loader.exec_module(client_module)
PantryAPIClient = client_module.PantryAPIClient


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


@contextmanager
def running_server():
    with TemporaryDirectory() as directory, api_token("sync-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}"
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def request_json(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method)
    request.add_header("Content-Type", "application/json")
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_web_and_ha_client_share_one_core_revision_without_restart() -> None:
    async def scenario(base_url: str) -> None:
        ha_client = PantryAPIClient(base_url, "sync-token")
        before = await ha_client.async_refresh()
        before_revision = before["revision"]
        before_total = before["summary"]["total_items"]

        web_created = request_json(
            f"{base_url}/api/items",
            method="POST",
            payload={"name": "Sync Apples", "quantity": "5", "unit": "count", "location": "Kitchen/Pantry"},
        )
        ha_snapshot = await ha_client.async_refresh()
        ha_seen = next(item for item in ha_snapshot["items"] if item["id"] == web_created["item"]["id"])

        ha_consumed = await ha_client.async_consume_item(web_created["item"]["id"], "2", reason="sync proof")
        web_snapshot = request_json(f"{base_url}/api/state")
        web_seen = next(item for item in web_snapshot["items"] if item["id"] == web_created["item"]["id"])

        assert ha_snapshot["revision"] > before_revision
        assert ha_snapshot["summary"]["total_items"] == before_total + 1
        assert ha_seen["name"] == "Sync Apples"
        assert ha_consumed["allocations"][0]["quantity"] == "2"
        assert web_snapshot["revision"] > ha_snapshot["revision"]
        assert web_seen["quantity"] == "3"

    with running_server() as base_url:
        asyncio.run(scenario(base_url))