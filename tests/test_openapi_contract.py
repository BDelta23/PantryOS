from __future__ import annotations

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

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pantryos.openapi import API_PATHS, openapi_document  # noqa: E402

_SERVER_PATH = ROOT / "app" / "server.py"
_SPEC = spec_from_file_location("pantryos_server_for_openapi_tests", _SERVER_PATH)
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


def request_json(url: str, *, token: str | None = None) -> dict:
    request = Request(url, method="GET")
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def request_error(url: str) -> tuple[int, dict]:
    try:
        request_json(url)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    raise AssertionError("Expected request to fail")


def test_openapi_document_tracks_current_versioned_route_surface() -> None:
    document = openapi_document()

    assert document["openapi"] == "3.1.0"
    assert set(document["paths"]) == set(API_PATHS)
    assert document["components"]["securitySchemes"]["bearerAuth"] == {"type": "http", "scheme": "bearer"}
    assert document["paths"]["/api/v1/health/live"]["get"]["security"] == []
    assert document["paths"]["/api/v1/health/ready"]["get"]["security"] == []

    protected_operations = []
    for path, methods in document["paths"].items():
        for method, operation in methods.items():
            if path not in {"/api/v1/health/live", "/api/v1/health/ready"}:
                protected_operations.append((path, method, operation))
            for status in ("400", "401", "404", "409", "413", "415", "503"):
                assert operation["responses"][status]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/Problem"

    assert protected_operations
    assert all("security" not in operation for _, _, operation in protected_operations)
    assert document["security"] == [{"bearerAuth": []}]
    assert document["paths"]["/api/v1/events"]["get"]["responses"]["200"]["content"]["text/event-stream"]
    assert "201" in document["paths"]["/api/v1/inventory/lots"]["post"]["responses"]
    assert document["paths"]["/api/v1/receipts"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/ReceiptUploadRequest"


def test_openapi_endpoint_is_authenticated_and_serves_contract() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            status, problem = request_error(f"{base}/api/v1/openapi.json")
            document = request_json(f"{base}/api/v1/openapi.json", token="test-token")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert status == 401
    assert problem["code"] == "unauthorized"
    assert document["info"]["title"] == "PantryOS Core API"
    assert "/api/v1/inventory/lots" in document["paths"]
    assert "/api/v1/openapi.json" in document["paths"]