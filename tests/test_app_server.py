import base64
import io
import json
import logging
import os
import sys
import threading
from contextlib import closing, contextmanager
from http.client import HTTPConnection
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.parse import urlparse
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


@contextmanager
def temporary_env(name: str, value: str):
    original = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if original is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = original


def request_json(
    url: str,
    method: str = "GET",
    payload: dict | None = None,
    *,
    token: str | None = None,
    request_id: str | None = None,
    cookie: str | None = None,
    csrf_token: str | None = None,
    origin: str | None = None,
) -> dict:
    data, _headers = request_json_with_headers(
        url,
        method=method,
        payload=payload,
        token=token,
        request_id=request_id,
        cookie=cookie,
        csrf_token=csrf_token,
        origin=origin,
    )
    return data


def request_json_with_headers(
    url: str,
    method: str = "GET",
    payload: dict | None = None,
    *,
    token: str | None = None,
    request_id: str | None = None,
    cookie: str | None = None,
    csrf_token: str | None = None,
    origin: str | None = None,
) -> tuple[dict, dict[str, str]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method)
    request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    if request_id is not None:
        request.add_header("X-Request-ID", request_id)
    if cookie is not None:
        request.add_header("Cookie", cookie)
    if csrf_token is not None:
        request.add_header("X-CSRF-Token", csrf_token)
    if origin is not None:
        request.add_header("Origin", origin)
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8")), dict(response.headers.items())


def request_error(
    url: str,
    method: str = "GET",
    payload: dict | None = None,
    *,
    token: str | None = None,
    request_id: str | None = None,
    cookie: str | None = None,
    csrf_token: str | None = None,
    origin: str | None = None,
) -> tuple[int, dict]:
    status, problem, _headers = request_error_with_headers(
        url,
        method=method,
        payload=payload,
        token=token,
        request_id=request_id,
        cookie=cookie,
        csrf_token=csrf_token,
        origin=origin,
    )
    return status, problem


def request_error_with_headers(
    url: str,
    method: str = "GET",
    payload: dict | None = None,
    *,
    token: str | None = None,
    request_id: str | None = None,
    cookie: str | None = None,
    csrf_token: str | None = None,
    origin: str | None = None,
) -> tuple[int, dict, dict[str, str]]:
    try:
        request_json(
            url,
            method=method,
            payload=payload,
            token=token,
            request_id=request_id,
            cookie=cookie,
            csrf_token=csrf_token,
            origin=origin,
        )
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8")), dict(exc.headers.items())
    raise AssertionError("Expected request to fail")


def browser_login(base: str, token: str = "test-token") -> tuple[str, str, str]:
    session, headers = request_json_with_headers(
        f"{base}/api/session/login",
        method="POST",
        payload={"token": token},
        origin=base,
    )
    set_cookie = headers["Set-Cookie"]
    return set_cookie.split(";", 1)[0], session["csrf_token"], set_cookie


def request_options(url: str, *, origin: str) -> tuple[int, dict[str, str]]:
    request = Request(url, method="OPTIONS")
    request.add_header("Origin", origin)
    try:
        with urlopen(request, timeout=5) as response:
            response.read()
            return response.status, dict(response.headers.items())
    except HTTPError as exc:
        exc.read()
        return exc.code, dict(exc.headers.items())


def request_text(
    url: str,
    method: str = "GET",
    *,
    token: str | None = None,
    request_id: str | None = None,
    accept: str | None = None,
) -> tuple[str, str]:
    request = Request(url, method=method)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    if request_id is not None:
        request.add_header("X-Request-ID", request_id)
    if accept is not None:
        request.add_header("Accept", accept)
    with urlopen(request, timeout=5) as response:
        return response.headers.get("Content-Type", ""), response.read().decode("utf-8")

def request_raw_error(
    url: str,
    body: bytes,
    *,
    token: str | None = None,
    request_id: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, dict]:
    request = Request(url, data=body, method="POST")
    request.add_header("Content-Type", content_type)
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


def request_declared_length_error(
    url: str,
    *,
    content_length: int,
    token: str | None = None,
    request_id: str | None = None,
) -> tuple[int, dict]:
    parsed = urlparse(url)
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        connection.putrequest("POST", parsed.path)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(content_length))
        if token is not None:
            connection.putheader("Authorization", f"Bearer {token}")
        if request_id is not None:
            connection.putheader("X-Request-ID", request_id)
        connection.endheaders()
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


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
            cookie, csrf_token, _set_cookie = browser_login(base)
            request_json(f"{base}/api/seed?reset=true", method="POST", cookie=cookie, csrf_token=csrf_token)
            created = request_json(
                f"{base}/api/items",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={
                    "name": "Heavy Cream",
                    "quantity": "1",
                    "unit": "cup",
                    "location": "Kitchen/Refrigerator",
                    "expires": "2026-08-30",
                },
            )
            state = request_json(f"{base}/api/state", cookie=cookie)
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


def test_browser_routes_require_session_csrf_and_same_origin() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            anonymous_session = request_json(f"{base}/api/session")
            status, unauth = request_error(f"{base}/api/state", request_id="browser-unauth")
            wrong_status, wrong_login = request_error(
                f"{base}/api/session/login",
                method="POST",
                payload={"token": "wrong-token"},
                origin=base,
            )
            cookie, csrf_token, set_cookie = browser_login(base)
            session = request_json(f"{base}/api/session", cookie=cookie)
            csrf_status, csrf_problem = request_error(
                f"{base}/api/items",
                method="POST",
                cookie=cookie,
                payload={"name": "Missing CSRF", "quantity": "1"},
            )
            origin_status, origin_problem = request_error(
                f"{base}/api/items",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                origin="http://evil.local",
                payload={"name": "Evil Origin", "quantity": "1"},
            )
            bad_preflight_status, bad_preflight_headers = request_options(
                f"{base}/api/items",
                origin="http://evil.local",
            )
            preflight_status, preflight_headers = request_options(f"{base}/api/items", origin=base)
            created = request_json(
                f"{base}/api/items",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                origin=base,
                payload={"name": "Session Apples", "quantity": "3", "unit": "count"},
            )
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert anonymous_session["authenticated"] is False
    assert anonymous_session["csrf_token"] == ""
    assert status == 401
    assert unauth["code"] == "browser_session_required"
    assert unauth["request_id"] == "browser-unauth"
    assert wrong_status == 401
    assert wrong_login["code"] == "unauthorized"
    assert set_cookie.startswith("pantryos_session=")
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie
    assert session["authenticated"] is True
    assert session["csrf_token"] == csrf_token
    assert csrf_status == 403
    assert csrf_problem["code"] == "csrf_required"
    assert origin_status == 403
    assert origin_problem["code"] == "origin_forbidden"
    assert bad_preflight_status == 403
    assert bad_preflight_headers.get("Access-Control-Allow-Origin") is None
    assert preflight_status == 204
    assert preflight_headers["Access-Control-Allow-Origin"] == base
    assert preflight_headers["Access-Control-Allow-Credentials"] == "true"
    assert created["item"]["name"] == "Session Apples"

def test_browser_session_persists_across_server_restart_and_logout_removes_it() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        session_path = Path(directory) / "browser_sessions.json"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            cookie, csrf_token, _set_cookie = browser_login(base)
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

        stored_sessions = session_path.read_text(encoding="utf-8")
        assert "test-token" not in stored_sessions
        assert "csrf_token" in stored_sessions

        restarted = server_module.make_server("127.0.0.1", 0, data_path)
        restarted_thread = threading.Thread(target=restarted.serve_forever, daemon=True)
        restarted_thread.start()
        try:
            restarted_base = f"http://127.0.0.1:{restarted.server_port}"
            session = request_json(f"{restarted_base}/api/session", cookie=cookie)
            created = request_json(
                f"{restarted_base}/api/items",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={"name": "Restart Session Rice", "quantity": "1", "unit": "count"},
            )
            logout = request_json(f"{restarted_base}/api/session/logout", method="POST", cookie=cookie, csrf_token=csrf_token)
            after_logout_status, after_logout = request_error(f"{restarted_base}/api/state", cookie=cookie, request_id="after-logout")
        finally:
            restarted.shutdown()
            restarted_thread.join(timeout=5)
            restarted.server_close()

    assert session["authenticated"] is True
    assert session["csrf_token"] == csrf_token
    assert created["item"]["name"] == "Restart Session Rice"
    assert logout == {"ok": True, "authenticated": False}
    assert after_logout_status == 401
    assert after_logout["code"] == "browser_session_required"


def test_browser_secure_cookie_mode_is_explicit_and_reported() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"), temporary_env("PANTRYOS_BROWSER_SECURE_COOKIES", "true"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            cookie, _csrf_token, set_cookie = browser_login(base)
            session = request_json(f"{base}/api/session", cookie=cookie)
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert "Secure" in set_cookie
    assert session["cookie"]["secure"] is True

def test_structured_request_logs_include_request_id_without_sensitive_values() -> None:
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    logger = server_module.LOGGER
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        with TemporaryDirectory() as directory, api_token("secret-token"):
            data_path = Path(directory) / "pantryos.sqlite3"
            httpd = server_module.make_server("127.0.0.1", 0, data_path)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{httpd.server_port}"
                cookie, csrf_token, set_cookie = browser_login(base, token="secret-token")
                request_json(
                    f"{base}/api/v1/receipts",
                    method="POST",
                    token="secret-token",
                    request_id="log-test-request",
                    payload={
                        "filename": "log-receipt.txt",
                        "mime_type": "text/plain",
                        "text": "Store: Private Market\nPrivate Apples,1,count,2.00\n",
                    },
                )
            finally:
                httpd.shutdown()
                thread.join(timeout=5)
                httpd.server_close()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate

    lines = [line for line in log_stream.getvalue().splitlines() if line]
    records = [json.loads(line) for line in lines]
    receipt_record = next(record for record in records if record["request_id"] == "log-test-request")
    serialized_logs = log_stream.getvalue()
    session_cookie_value = set_cookie.split(";", 1)[0].split("=", 1)[1]

    assert receipt_record["event"] == "http.request"
    assert receipt_record["method"] == "POST"
    assert receipt_record["path"] == "/api/v1/receipts"
    assert receipt_record["status"] == 201
    assert isinstance(receipt_record["duration_ms"], float)
    assert "secret-token" not in serialized_logs
    assert "Bearer" not in serialized_logs
    assert csrf_token not in serialized_logs
    assert session_cookie_value not in serialized_logs
    assert "Private Market" not in serialized_logs
    assert "Private Apples" not in serialized_logs

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


def test_v1_json_requests_enforce_content_type_and_body_size() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            media_status, media_problem = request_raw_error(
                f"{base}/api/v1/inventory/lots",
                b'{"name":"Wrong Media"}',
                token="test-token",
                request_id="wrong-media",
                content_type="text/plain",
            )
            oversized_status, oversized_problem = request_declared_length_error(
                f"{base}/api/v1/inventory/lots",
                content_length=server_module.MAX_REQUEST_BODY_BYTES + 1,
                token="test-token",
                request_id="too-large",
            )
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert media_status == 415
    assert media_problem["code"] == "unsupported_media_type"
    assert media_problem["request_id"] == "wrong-media"
    assert oversized_status == 413
    assert oversized_problem["code"] == "request_body_too_large"
    assert oversized_problem["request_id"] == "too-large"

def test_barcode_api_and_browser_routes_map_and_add_lots() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            cookie, csrf_token, _set_cookie = browser_login(base)
            unknown = request_json(f"{base}/api/barcodes/000111222333", cookie=cookie)
            mapping = request_json(
                f"{base}/api/barcodes/mappings",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
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
                cookie=cookie,
                csrf_token=csrf_token,
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
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            cookie, csrf_token, _set_cookie = browser_login(base)
            request_json(f"{base}/api/seed?reset=true", method="POST", cookie=cookie, csrf_token=csrf_token)
            shopping = request_json(
                f"{base}/api/shopping",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={"name": "Browser Oats", "quantity": "2", "unit": "count"},
            )
            checked = request_json(
                f"{base}/api/shopping/{shopping['item']['id']}/check",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
            )
            purchase = request_json(
                f"{base}/api/shopping/complete-purchase",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={
                    "store": "Browser Market",
                    "location": "Kitchen/Pantry",
                    "items": [{"shopping_id": shopping["item"]["id"], "quantity": "2"}],
                },
            )
            rice = request_json(
                f"{base}/api/items",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={"name": "Browser Rice", "quantity": "2", "unit": "cup", "location": "Kitchen/Pantry"},
            )
            moved = request_json(
                f"{base}/api/items/{rice['item']['id']}/move",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={"location": "Kitchen/Refrigerator/Top Shelf"},
            )
            seeded_state = request_json(f"{base}/api/state", cookie=cookie)
            eggs_product = next(product for product in seeded_state["core"]["products"] if product["name"] == "Eggs")
            eggs_lot = next(item for item in seeded_state["items"] if item["name"] == "Eggs")
            product_settings = request_json(
                f"{base}/api/products/{eggs_product['id']}",
                method="PATCH",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={
                    "category": "Protein",
                    "default_unit": "count",
                    "minimum_stock_quantity": "18",
                    "minimum_stock_unit": "count",
                    "preferred_location": "Kitchen/Refrigerator/Top Shelf",
                    "default_shelf_life_days": 21,
                    "opened_shelf_life_days": 7,
                },
            )
            request_json(
                f"{base}/api/items/{eggs_lot['id']}/consume",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={"quantity": eggs_lot["quantity"]},
            )
            recipe = request_json(
                f"{base}/api/recipes",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={
                    "name": "Browser Rice Bowl",
                    "ingredients": [{"name": "Browser Rice", "quantity": "1", "unit": "cup"}],
                },
            )
            updated_recipe = request_json(
                f"{base}/api/recipes/{recipe['recipe']['id']}",
                method="PATCH",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={
                    "name": "Browser Rice Bowl Deluxe",
                    "prep_minutes": 18,
                    "instructions": "Warm rice and plate it.",
                    "ingredients": [
                        {"name": "Browser Rice", "quantity": "1", "unit": "cup"},
                        {"name": "Browser Scallion", "quantity": "1", "unit": "count"},
                    ],
                },
            )
            request_json(
                f"{base}/api/meal-plan",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={"day": "Tonight", "recipe_name": "Browser Rice Bowl Deluxe"},
            )
            blocked_delete_status, blocked_delete = request_error(
                f"{base}/api/recipes/{recipe['recipe']['id']}",
                method="DELETE",
                cookie=cookie,
                csrf_token=csrf_token,
            )
            delete_recipe = request_json(
                f"{base}/api/recipes",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={"name": "Browser Delete Soup", "ingredients": [{"name": "Water", "quantity": "1", "unit": "cup"}]},
            )
            deleted_recipe = request_json(
                f"{base}/api/recipes/{delete_recipe['recipe']['id']}",
                method="DELETE",
                cookie=cookie,
                csrf_token=csrf_token,
            )
            started = request_json(
                f"{base}/api/cooking/sessions",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={"recipe_name": "Browser Rice Bowl Deluxe", "planned_servings": "1"},
            )
            completed = request_json(
                f"{base}/api/cooking/sessions/{started['session']['id']}/complete",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={
                    "allocations": [{"lot_id": rice["item"]["id"], "quantity": "1", "unit": "cup"}],
                    "leftovers": [{"name": "Browser Rice Bowl Leftovers", "quantity": "1", "unit": "serving"}],
                },
            )
            state = request_json(f"{base}/api/state", cookie=cookie)
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert checked["item"]["checked"] is True
    assert purchase["purchase"]["store"] == "Browser Market"
    assert purchase["lots"][0]["name"] == "Browser Oats"
    assert moved["item"]["location"] == "Kitchen/Refrigerator/Top Shelf"
    assert moved["revision"] > rice["revision"]
    assert product_settings["product"]["category"] == "Protein"
    assert product_settings["product"]["minimum_stock_quantity"] == "18"
    assert product_settings["product"]["default_shelf_life_days"] == 21
    assert updated_recipe["recipe"]["id"] == recipe["recipe"]["id"]
    assert updated_recipe["recipe"]["name"] == "Browser Rice Bowl Deluxe"
    assert updated_recipe["recipe"]["prep_minutes"] == 18
    assert len(updated_recipe["recipe"]["ingredients"]) == 2
    assert blocked_delete_status == 400
    assert blocked_delete["code"] == "validation_error"
    assert deleted_recipe["ok"] is True
    assert started["session"]["status"] == "cooking"
    assert completed["session"]["status"] == "completed"
    assert completed["leftovers"][0]["name"] == "Browser Rice Bowl Leftovers"
    browser_rice = next(item for item in state["items"] if item["name"] == "Browser Rice")
    assert browser_rice["quantity"] == "1"
    eggs_suggestion = next(item for item in state["summary"]["suggested_purchases"] if item["name"] == "Eggs")
    assert eggs_suggestion["quantity"] == "18"
    assert eggs_suggestion["unit"] == "count"
    recipe_names = [recipe["name"] for recipe in state["recipes"]]
    assert "Browser Rice Bowl Deluxe" in recipe_names
    assert "Browser Rice Bowl" not in recipe_names
    assert "Browser Delete Soup" not in recipe_names
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
            cookie, _csrf_token, _set_cookie = browser_login(base)
            browser_prices = request_json(f"{base}/api/products/{product_id}/prices", cookie=cookie)
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
    assert prices["analysis"]["baseline_policy"] == "recent_median_compatible_unit"
    assert prices["analysis"]["latest"]["status"] == "baseline"
    assert browser_prices["analysis"]["baseline_policy"] == "recent_median_compatible_unit"
    assert browser_prices["product"]["id"] == product_id


def test_receipt_api_accepts_image_upload_and_extracts_with_local_ocr_boundary() -> None:
    original = server_module.PantryCore._extract_receipt_image

    def fake_ocr(self, storage_path: Path) -> str:
        assert storage_path.suffix == ".png"
        return "Store: API Image Market\nDate: 2026-08-26\nAPI Image Rice,1,count,3.50\nTotal: 3.50\n"

    server_module.PantryCore._extract_receipt_image = fake_ocr
    try:
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
                        "filename": "image-receipt.png",
                        "mime_type": "image/png",
                        "content_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (120).to_bytes(4, "big") + (80).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00").decode("ascii"),
                    },
                )
                extracted = request_json(
                    f"{base}/api/v1/receipts/{uploaded['receipt']['id']}/extract",
                    method="POST",
                    token="test-token",
                )
            finally:
                httpd.shutdown()
                thread.join(timeout=5)
                httpd.server_close()
    finally:
        server_module.PantryCore._extract_receipt_image = original

    assert uploaded["receipt"]["mime_type"] == "image/png"
    assert "storage_path" not in uploaded["receipt"]
    assert extracted["receipt"]["status"] == "review"
    assert extracted["review"]["store"] == "API Image Market"
    assert extracted["review"]["items"][0]["name"] == "API Image Rice"


def test_receipt_upload_enforces_limits_and_private_storage() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            unsupported_status, unsupported = request_error(
                f"{base}/api/v1/receipts",
                method="POST",
                token="test-token",
                payload={"filename": "receipt.pdf", "mime_type": "application/pdf", "content_base64": "AAAA"},
                request_id="bad-mime",
            )
            path_status, path_problem = request_error(
                f"{base}/api/v1/receipts",
                method="POST",
                token="test-token",
                payload={"filename": "../receipt.txt", "mime_type": "text/plain", "text": "Store: Bad Path"},
                request_id="bad-path",
            )
            mismatch_status, mismatch = request_error(
                f"{base}/api/v1/receipts",
                method="POST",
                token="test-token",
                payload={"filename": "receipt.csv", "mime_type": "text/plain", "text": "Store: Wrong Extension"},
                request_id="bad-extension",
            )
            csv_status, csv_problem = request_error(
                f"{base}/api/v1/receipts",
                method="POST",
                token="test-token",
                payload={"filename": "receipt.csv", "mime_type": "text/csv", "text": "no comma rows here"},
                request_id="bad-csv",
            )
            oversized_status, oversized = request_error(
                f"{base}/api/v1/receipts",
                method="POST",
                token="test-token",
                payload={"filename": "big.txt", "mime_type": "text/plain", "text": "x" * 64001},
                request_id="big-receipt",
            )
            uploaded = request_json(
                f"{base}/api/v1/receipts",
                method="POST",
                token="test-token",
                payload={
                    "filename": "receipt.csv",
                    "mime_type": "text/csv",
                    "text": "Store: CSV Market\nDate: 2026-08-25\nCSV Beans,2,count,3.00\n",
                },
            )
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

        core = server_module.PantryCore(data_path)
        with closing(core.connect()) as connection:
            row = connection.execute("SELECT * FROM receipt_uploads WHERE id = ?", (uploaded["receipt"]["id"],)).fetchone()
        storage_path = Path(row["storage_path"]).resolve()
        receipt_dir = (data_path.parent / "receipts").resolve()
        static_dir = server_module.STATIC_DIR.resolve()
        storage_text = storage_path.read_text(encoding="utf-8")

    assert unsupported_status == 400
    assert unsupported["code"] == "validation_error"
    assert unsupported["request_id"] == "bad-mime"
    assert path_status == 400
    assert path_problem["detail"] == "Receipt filename must not include a path"
    assert mismatch_status == 400
    assert mismatch["detail"] == "Receipt filename extension does not match MIME type"
    assert csv_status == 400
    assert csv_problem["detail"] == "CSV receipt content must contain comma-separated rows"
    assert oversized_status == 400
    assert oversized["detail"] == "Receipt upload exceeds 64000 bytes"
    assert uploaded["receipt"]["status"] == "uploaded"
    assert "storage_path" not in uploaded["receipt"]
    assert storage_path.suffix == ".csv"
    assert storage_text.startswith("Store: CSV Market")
    assert storage_path.parent == receipt_dir
    assert static_dir not in storage_path.parents


def test_receipt_upload_and_extract_are_rate_limited() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        httpd.RequestHandlerClass.rate_limiter = server_module.RateLimiter(limit=1, window_seconds=60)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            uploaded = request_json(
                f"{base}/api/v1/receipts",
                method="POST",
                token="test-token",
                payload={
                    "filename": "rate-one.txt",
                    "mime_type": "text/plain",
                    "text": "Store: Rate Market\nDate: 2026-08-25\nRate Beans,1,count,2.00\n",
                },
            )
            upload_status, upload_problem, upload_headers = request_error_with_headers(
                f"{base}/api/v1/receipts",
                method="POST",
                token="test-token",
                payload={
                    "filename": "rate-two.txt",
                    "mime_type": "text/plain",
                    "text": "Store: Rate Market\nDate: 2026-08-25\nRate Rice,1,count,2.00\n",
                },
                request_id="upload-limit",
            )
            extracted = request_json(
                f"{base}/api/v1/receipts/{uploaded['receipt']['id']}/extract",
                method="POST",
                token="test-token",
            )
            extract_status, extract_problem, extract_headers = request_error_with_headers(
                f"{base}/api/v1/receipts/{uploaded['receipt']['id']}/extract",
                method="POST",
                token="test-token",
                request_id="extract-limit",
            )
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert uploaded["receipt"]["status"] == "uploaded"
    assert upload_status == 429
    assert upload_problem["code"] == "rate_limited"
    assert upload_problem["request_id"] == "upload-limit"
    assert int(upload_headers["Retry-After"]) >= 1
    assert extracted["receipt"]["status"] == "review"
    assert extract_status == 429
    assert extract_problem["code"] == "rate_limited"
    assert extract_problem["request_id"] == "extract-limit"
    assert int(extract_headers["Retry-After"]) >= 1

def test_event_api_requires_auth_and_streams_hello_and_recent_events() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            status, unauth = request_error(f"{base}/api/v1/events", request_id="events-unauth")
            created = request_json(
                f"{base}/api/v1/inventory/lots",
                method="POST",
                token="test-token",
                payload={"name": "Event Apples", "quantity": "2", "unit": "count", "location": "Kitchen/Pantry"},
            )
            events = request_json(f"{base}/api/v1/inventory/events?limit=5", token="test-token")
            event_detail = request_json(f"{base}/api/v1/events/{events['items'][-1]['id']}", token="test-token")
            content_type, stream = request_text(f"{base}/api/v1/events?timeout=0.1&heartbeat=0.1", token="test-token", accept="text/event-stream")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert status == 401
    assert unauth["code"] == "unauthorized"
    assert unauth["request_id"] == "events-unauth"
    assert created["revision"] >= 1
    assert events["limit"] == 5
    assert events["items"][-1]["type"] == "ADD"
    assert events["items"][-1]["data"]["quantity"] == "2"
    assert event_detail["id"] == events["items"][-1]["id"]
    assert event_detail["revision"] == events["items"][-1]["revision"]
    assert content_type.startswith("text/event-stream")
    assert "event: pantryos.hello" in stream
    assert "event: ADD" in stream
    assert "data:" in stream
    assert ": heartbeat" in stream


def test_event_stream_waits_for_new_revisions_before_closing() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            revision = request_json(f"{base}/api/v1/dashboard", token="test-token")["revision"]

            def add_item() -> None:
                request_json(
                    f"{base}/api/v1/inventory/lots",
                    method="POST",
                    token="test-token",
                    payload={"name": "Stream Beans", "quantity": "1", "unit": "count", "location": "Kitchen/Pantry"},
                )

            timer = threading.Timer(0.1, add_item)
            timer.start()
            content_type, stream = request_text(
                f"{base}/api/v1/events?after_revision={revision}&timeout=0.6&heartbeat=0.1",
                token="test-token",
                accept="text/event-stream",
            )
            timer.join(timeout=1)
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert content_type.startswith("text/event-stream")
    assert "event: pantryos.hello" in stream
    assert "event: ADD" in stream
    assert "Stream Beans" not in stream
    assert f"id: {revision + 1}" in stream
    assert ": heartbeat" in stream

def test_static_browser_workflows_are_not_stubbed() -> None:
    static_dir = Path(__file__).resolve().parents[1] / "app" / "static"
    app_js = (static_dir / "app.js").read_text(encoding="utf-8")
    index_html = (static_dir / "index.html").read_text(encoding="utf-8")
    manifest = json.loads((static_dir / "manifest.webmanifest").read_text(encoding="utf-8"))
    service_worker = (static_dir / "service-worker.js").read_text(encoding="utf-8")

    assert "Cooking mode queued" not in app_js
    assert "/api/cooking/sessions" in app_js
    assert "/api/shopping/complete-purchase" in app_js
    assert "/api/purchases" in app_js
    assert "/api/products/" in app_js
    assert "/prices" in app_js
    assert "/api/barcodes/" in app_js
    assert "handleBarcodeSubmit" in app_js
    assert "supportsBarcodeCamera" in app_js
    assert "BarcodeDetector" in app_js
    assert "navigator.mediaDevices.getUserMedia" in app_js
    assert "facingMode: { ideal: \"environment\" }" in app_js
    assert "track.stop()" in app_js
    assert "Manual barcode entry is available" in app_js
    assert "handleStartCooking" in app_js
    assert "handlePurchaseSubmit" in app_js
    assert "startRecipeEdit" in app_js
    assert "resetRecipeEditor" in app_js
    assert "data-recipe-edit" in app_js
    assert "data-recipe-delete" in app_js
    assert "PATCH" in app_js
    assert "DELETE" in app_js
    assert "renderKnownLocations" in app_js
    assert "data-move-location" in app_js
    assert "data-open" in app_js
    assert "/open" in app_js
    assert "/move" in app_js
    assert "renderPurchases" in app_js
    assert "handlePurchaseDetail" in app_js
    assert "handlePriceAnalysis" in app_js
    assert "renderProductSettings" in app_js
    assert "data-product-save" in app_js
    assert "minimum_stock_quantity" in app_js
    assert "recent_median_compatible_unit" not in app_js
    assert "navigator.serviceWorker.register(\"/service-worker.js\")" in app_js
    assert "PantryOS Core is offline; the request was not committed." in app_js
    assert "rel=\"manifest\" href=\"/manifest.webmanifest\"" in index_html
    assert "name=\"theme-color\"" in index_html
    assert "id=\"cookingForm\"" in index_html
    assert "id=\"recipeFormTitle\"" in index_html
    assert "id=\"recipeSubmitButton\"" in index_html
    assert "id=\"cancelRecipeEditButton\"" in index_html
    assert "name=\"instructions\"" in index_html
    assert "id=\"knownLocations\"" in index_html
    assert "id=\"purchaseForm\"" in index_html
    assert "id=\"purchaseHistoryList\"" in index_html
    assert "id=\"purchaseDetail\"" in index_html
    assert "id=\"priceAnalysis\"" in index_html
    assert "id=\"productSettingsList\"" in index_html
    assert "id=\"barcodeForm\"" in index_html
    assert "id=\"barcodeScannerPanel\"" in index_html
    assert "id=\"barcodeVideo\"" in index_html
    assert "id=\"barcodeCameraButton\"" in index_html
    assert "id=\"barcodeStopButton\"" in index_html
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["icons"][0]["src"] == "/icon.svg"
    assert 'url.pathname.startsWith("/api/")' in service_worker
    assert "the request was not committed" in service_worker
    assert "sync" not in service_worker.casefold()
    assert "queue" not in service_worker.casefold()


def test_pwa_metadata_and_service_worker_are_served() -> None:
    with TemporaryDirectory() as directory:
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            manifest_type, manifest_text = request_text(f"{base}/manifest.webmanifest")
            worker_type, worker_text = request_text(f"{base}/service-worker.js")
            icon_type, icon_text = request_text(f"{base}/icon.svg")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    manifest = json.loads(manifest_text)
    assert manifest_type.startswith("application/manifest+json")
    assert worker_type.startswith(("text/javascript", "application/javascript"))
    assert icon_type.startswith("image/svg+xml")
    assert manifest["name"] == "PantryOS"
    assert "offlineProblem" in worker_text
    assert "the request was not committed" in worker_text
    assert "<svg" in icon_text


def test_open_lot_api_and_browser_routes_apply_opened_shelf_life_idempotently() -> None:
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            created = request_json(
                f"{base}/api/v1/inventory/lots",
                method="POST",
                token="test-token",
                payload={
                    "name": "Open API Yogurt",
                    "quantity": "1",
                    "unit": "count",
                    "location": "Kitchen/Refrigerator",
                    "expires": "2026-09-30",
                },
            )
            request_json(
                f"{base}/api/v1/products/{created['item']['product_id']}",
                method="PATCH",
                token="test-token",
                payload={"opened_shelf_life_days": 3},
            )
            opened = request_json(
                f"{base}/api/v1/inventory/lots/{created['item']['id']}/open",
                method="POST",
                token="test-token",
                payload={"opened_at": "2026-08-26"},
            )
            opened_again = request_json(
                f"{base}/api/v1/inventory/lots/{created['item']['id']}/open",
                method="POST",
                token="test-token",
                payload={"opened_at": "2026-08-27"},
            )

            cookie, csrf_token, _set_cookie = browser_login(base)
            browser_created = request_json(
                f"{base}/api/items",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={"name": "Open Browser Jam", "quantity": "1", "unit": "count", "location": "Kitchen/Pantry"},
            )
            request_json(
                f"{base}/api/products/{browser_created['item']['product_id']}",
                method="PATCH",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={"opened_shelf_life_days": 2},
            )
            browser_opened = request_json(
                f"{base}/api/items/{browser_created['item']['id']}/open",
                method="POST",
                cookie=cookie,
                csrf_token=csrf_token,
                payload={"opened_at": "2026-08-26"},
            )
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    assert opened["opened"] is True
    assert opened["item"]["opened"] is True
    assert opened["item"]["expires"] == "2026-08-29"
    assert opened_again["opened"] is False
    assert opened_again["revision"] == opened["revision"]
    assert browser_opened["opened"] is True
    assert browser_opened["item"]["opened"] is True
    assert browser_opened["item"]["expires"] == "2026-08-28"
