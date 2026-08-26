"""Concurrent PantryOS API mutation smoke.

Starts a temporary local PantryOS server, seeds independent inventory lots, then
runs twenty authenticated mutation requests at the same time. The smoke verifies
that every successful mutation is reflected in the final dashboard and that the
SQLite database still passes integrity checks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing, contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for candidate in (ROOT / "scripts", ROOT, SRC):
    candidate_text = str(candidate)
    while candidate_text in sys.path:
        sys.path.remove(candidate_text)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SRC))

from pantryos.core import PantryCore  # noqa: E402

_SERVER_SPEC = spec_from_file_location("pantryos_concurrency_server", ROOT / "app" / "server.py")
if _SERVER_SPEC is None or _SERVER_SPEC.loader is None:
    raise RuntimeError("Could not load PantryOS server module")
server_module = module_from_spec(_SERVER_SPEC)
sys.modules[_SERVER_SPEC.name] = server_module
_SERVER_SPEC.loader.exec_module(server_module)

DEFAULT_TOKEN = "pantryos-concurrency-smoke-token"


class ConcurrencySmokeFailure(AssertionError):
    """Raised when the API concurrency smoke cannot prove its invariants."""


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str,
    timeout: int = 15,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method)
    request.add_header("Accept", "application/json")
    request.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            problem = json.loads(exc.read().decode("utf-8"))
        except json.JSONDecodeError:
            problem = {"detail": f"HTTP {exc.code}"}
        raise ConcurrencySmokeFailure(f"{method} {url} failed: {exc.code} {problem}") from exc
    except (ConnectionResetError, TimeoutError, URLError, OSError) as exc:
        raise ConcurrencySmokeFailure(f"{method} {url} failed before an HTTP response: {exc}") from exc


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
def running_server(data_path: Path, *, token: str):
    with api_token(token):
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}"
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ConcurrencySmokeFailure(message)


def add_lot(base_url: str, token: str, name: str, quantity: str = "1") -> dict[str, Any]:
    return request_json(
        f"{base_url}/api/v1/inventory/lots",
        method="POST",
        token=token,
        payload={"name": name, "quantity": quantity, "unit": "count", "location": "Kitchen/Pantry"},
    )


def dashboard(base_url: str, token: str) -> dict[str, Any]:
    return request_json(f"{base_url}/api/v1/dashboard", token=token)


def lot_by_name(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    for lot in snapshot["core"]["lots"]:
        if lot["product_name"] == name:
            return lot
    raise ConcurrencySmokeFailure(f"Missing lot {name!r} from dashboard")


def mutation_plan(base_url: str, token: str, seeded: dict[str, list[str]]) -> list[tuple[str, str, dict[str, Any] | None]]:
    planned: list[tuple[str, str, dict[str, Any] | None]] = []
    for index in range(8):
        planned.append(
            (
                "POST",
                f"{base_url}/api/v1/inventory/lots",
                {"name": f"Concurrent API Add {index}", "quantity": "1", "unit": "count", "location": "Kitchen/Pantry"},
            )
        )
    for lot_id in seeded["open"]:
        planned.append(("POST", f"{base_url}/api/v1/inventory/lots/{lot_id}/open", {}))
    for lot_id in seeded["consume"]:
        planned.append(("POST", f"{base_url}/api/v1/inventory/lots/{lot_id}/consume", {"quantity": "1", "reason": "concurrency smoke"}))
    for lot_id in seeded["discard"]:
        planned.append(("POST", f"{base_url}/api/v1/inventory/lots/{lot_id}/discard", {"reason": "concurrency smoke"}))
    require(len(planned) == 20, f"Expected 20 planned mutations, got {len(planned)}")
    return planned


def run_concurrent_mutations(base_url: str, token: str, seeded: dict[str, list[str]]) -> list[dict[str, Any]]:
    planned = mutation_plan(base_url, token, seeded)
    barrier = threading.Barrier(len(planned))

    def worker(operation: tuple[str, str, dict[str, Any] | None]) -> dict[str, Any]:
        method, url, payload = operation
        barrier.wait(timeout=10)
        result = request_json(url, method=method, payload=payload, token=token)
        return {"method": method, "url": url, "result": result}

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(planned)) as executor:
        futures = [executor.submit(worker, operation) for operation in planned]
        for future in as_completed(futures, timeout=45):
            results.append(future.result())
    require(len(results) == len(planned), f"Expected {len(planned)} results, got {len(results)}")
    return results


def verify_final_state(
    base_url: str, token: str, data_path: Path, baseline: dict[str, Any], results: list[dict[str, Any]]
) -> dict[str, Any]:
    final = dashboard(base_url, token)
    baseline_revision = int(baseline["revision"])
    expected_revision = baseline_revision + len(results)
    require(final["revision"] == expected_revision, f"Expected revision {expected_revision}, got {final['revision']}")
    require(final["core"]["summary"]["event_count"] == expected_revision, "Event count did not match final revision")

    for index in range(8):
        lot = lot_by_name(final, f"Concurrent API Add {index}")
        require(lot["status"] == "active" and lot["quantity"] == "1", f"Concurrent add {index} was not preserved: {lot}")
    for index in range(4):
        opened = lot_by_name(final, f"Concurrent API Open {index}")
        consumed = lot_by_name(final, f"Concurrent API Consume {index}")
        discarded = lot_by_name(final, f"Concurrent API Discard {index}")
        require(opened["opened_at"] is not None, f"Open mutation {index} did not persist opened_at")
        require(consumed["quantity"] == "2", f"Consume mutation {index} did not subtract exactly one: {consumed}")
        require(
            discarded["status"] == "discarded" and discarded["quantity"] == "0", f"Discard mutation {index} did not persist: {discarded}"
        )

    core = PantryCore(data_path)
    core.integrity_check()
    with closing(core.connect()) as connection:
        products = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        lots = connection.execute("SELECT COUNT(*) FROM inventory_lots").fetchone()[0]
        events = connection.execute("SELECT COUNT(*) FROM inventory_events").fetchone()[0]
    expected_products = int(baseline["core"]["summary"]["product_count"]) + 8
    expected_lots = len(baseline["core"]["lots"]) + 8
    require(products == expected_products, f"Expected {expected_products} products after 8 concurrent adds, got {products}")
    require(lots == expected_lots, f"Expected {expected_lots} lots after 8 concurrent adds, got {lots}")
    require(events == expected_revision, f"Expected {expected_revision} events, got {events}")
    return final


def run_smoke(token: str = DEFAULT_TOKEN) -> dict[str, Any]:
    with TemporaryDirectory(prefix="pantryos-concurrency-") as directory:
        data_path = Path(directory) / "pantryos.sqlite3"
        seeded: dict[str, list[str]] = {"open": [], "consume": [], "discard": []}
        with running_server(data_path, token=token) as base_url:
            ready = request_json(f"{base_url}/api/v1/health/ready", token=token)
            require(ready.get("status") == "ready", f"Server did not become ready: {ready}")
            for kind in seeded:
                for index in range(4):
                    quantity = "3" if kind in {"consume", "discard"} else "1"
                    created = add_lot(base_url, token, f"Concurrent API {kind.title()} {index}", quantity=quantity)
                    seeded[kind].append(created["item"]["id"])
            baseline = dashboard(base_url, token)
            baseline_revision = int(baseline["revision"])
            results = run_concurrent_mutations(base_url, token, seeded)
            final = verify_final_state(base_url, token, data_path, baseline, results)
            return {
                "ok": True,
                "base_url": base_url,
                "baseline_revision": baseline_revision,
                "final_revision": final["revision"],
                "successful_mutations": len(results),
                "active_lot_count": final["core"]["summary"]["active_lot_count"],
                "event_count": final["core"]["summary"]["event_count"],
                "product_count": final["core"]["summary"]["product_count"],
            }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run twenty concurrent authenticated PantryOS API mutations against a temporary server.")
    parser.add_argument("--token", default=os.environ.get("PANTRYOS_API_TOKEN") or DEFAULT_TOKEN)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_smoke(args.token)
    except ConcurrencySmokeFailure as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
