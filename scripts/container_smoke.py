"""Docker release smoke test for PantryOS.

This script exercises the production container contract from the host using only
stdlib Python and Docker Compose. It intentionally avoids pytest so it can run on
release machines after Docker Desktop is started.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_TOKEN = "local-dev-token"
DEFAULT_SERVICE = "pantryos"
DEFAULT_CONTAINER = "pantryos"


class ContainerSmokeFailure(AssertionError):
    """Raised when the Docker smoke cannot prove an expected release contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContainerSmokeFailure(message)


def log(message: str) -> None:
    print(f"[container-smoke] {message}", file=sys.stderr)


def tail(text: str, limit: int = 2400) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def run_command(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            args,
            cwd=ROOT,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ContainerSmokeFailure(f"Required command is not available: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ContainerSmokeFailure(f"Command timed out after {timeout}s: {' '.join(args)}") from exc
    if check and completed.returncode != 0:
        detail = tail(completed.stderr or completed.stdout)
        raise ContainerSmokeFailure(f"Command failed ({completed.returncode}): {' '.join(args)}\n{detail}")
    return completed


def compose_env(token: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PANTRYOS_API_TOKEN"] = token
    env.setdefault("PANTRYOS_PORT", "8765")
    env.setdefault("PANTRYOS_RATE_LIMIT_REQUESTS", "100")
    env.setdefault("PANTRYOS_RATE_LIMIT_WINDOW_SECONDS", "60")
    env.setdefault("PANTRYOS_BROWSER_SESSION_SECONDS", "43200")
    env.setdefault("PANTRYOS_BROWSER_SECURE_COOKIES", "false")
    return env


def docker_compose(args: list[str], *, token: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return run_command(["docker", "compose", *args], env=compose_env(token), timeout=timeout)


def docker_exec(
    container: str,
    args: list[str],
    *,
    timeout: int = 120,
    user: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = ["docker", "exec"]
    if user:
        command.extend(["--user", user])
    command.extend([container, *args])
    return run_command(command, timeout=timeout)


def docker_json(container: str, args: list[str], *, timeout: int = 120, user: str | None = None) -> dict[str, Any]:
    completed = docker_exec(container, args, timeout=timeout, user=user)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ContainerSmokeFailure(f"Container command did not return JSON: {' '.join(args)}\n{completed.stdout}") from exc


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method)
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            problem = json.loads(exc.read().decode("utf-8"))
        except json.JSONDecodeError:
            problem = {"detail": f"HTTP {exc.code}"}
        raise ContainerSmokeFailure(f"{method} {url} failed: {exc.code} {problem}") from exc
    except URLError as exc:
        raise ContainerSmokeFailure(f"{method} {url} failed: {exc}") from exc


def request_status(url: str, *, method: str = "GET", token: str | None = None) -> tuple[int, dict[str, Any]]:
    request = Request(url, method=method)
    request.add_header("Accept", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except json.JSONDecodeError:
            payload = {"detail": f"HTTP {exc.code}"}
        return exc.code, payload


def wait_ready(base_url: str, *, timeout: int = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return request_json(f"{base_url}/api/v1/health/ready", timeout=5)
        except (ContainerSmokeFailure, OSError) as exc:
            last_error = exc
            time.sleep(1)
    raise ContainerSmokeFailure(f"PantryOS did not become ready within {timeout}s: {last_error}")


def dashboard_lots(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    lots = dashboard.get("lots")
    if isinstance(lots, list) and lots:
        return lots
    core = dashboard.get("core")
    if isinstance(core, dict) and isinstance(core.get("lots"), list):
        return core["lots"]
    return []


def find_lot(dashboard: dict[str, Any], lot_id: str, name: str) -> dict[str, Any]:
    for lot in dashboard_lots(dashboard):
        if lot.get("id") == lot_id or lot.get("product_name") == name or lot.get("name") == name:
            return lot
    raise ContainerSmokeFailure(f"Dashboard did not include expected lot {lot_id!r} / {name!r}")


def receipt_item_names(review: dict[str, Any]) -> set[str]:
    return {str(item.get("name")) for item in review.get("items", []) if item.get("name")}


def run_container_verifier(container: str) -> str:
    completed = docker_exec(container, ["python", "scripts/check.py"], timeout=180, user="10001:10001")
    output = completed.stdout.strip().splitlines()
    return output[-1] if output else "scripts/check.py completed"


def process_status(container: str) -> dict[str, str]:
    status = docker_exec(container, ["cat", "/proc/1/status"], timeout=30).stdout
    fields: dict[str, str] = {}
    for line in status.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key] = value.strip()
    return fields


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    token = args.token or os.environ.get("PANTRYOS_API_TOKEN") or DEFAULT_TOKEN
    base_url = args.base_url
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    lot_name = f"Container Smoke Rice {stamp}"
    receipt_item = f"Container Smoke Beans {stamp}"
    backup_path = f"/app/data/backups/container-smoke-{stamp}.zip"
    restored_db = f"/app/data/container-smoke-restored-{stamp}.sqlite3"

    log("checking Docker access")
    docker_version = run_command(["docker", "version", "--format", "{{.Server.Version}}"], timeout=30).stdout.strip()

    log("validating Compose file")
    docker_compose(["config", "--quiet"], token=token, timeout=60)

    log("building and starting PantryOS container")
    docker_compose(["up", "--build", "--detach", args.service], token=token, timeout=args.build_timeout)
    health = wait_ready(base_url, timeout=args.ready_timeout)

    log("verifying container hardening")
    status_fields = process_status(args.container)
    uid = status_fields.get("Uid", "").split()[0]
    gid = status_fields.get("Gid", "").split()[0]
    require(uid == "10001", f"Container process should run as UID 10001, got {status_fields.get('Uid')!r}")
    require(gid == "10001", f"Container process should run as GID 10001, got {status_fields.get('Gid')!r}")
    require(status_fields.get("NoNewPrivs") == "1", "Container process should have no-new-privileges enabled")
    app_write = run_command(
        [
            "docker",
            "exec",
            "--user",
            "10001:10001",
            args.container,
            "python",
            "-c",
            "import pathlib; pathlib.Path('/app/container-smoke-forbidden').write_text('x')",
        ],
        timeout=30,
        check=False,
    )
    require(app_write.returncode != 0, "Read-only root filesystem allowed writing under /app")

    status, auth_problem = request_status(f"{base_url}/api/v1/dashboard")
    require(status == 401, f"Dashboard without bearer token should return 401, got {status}: {auth_problem}")

    log("creating inventory and receipt state through the live API")
    dashboard_before = request_json(f"{base_url}/api/v1/dashboard", token=token)
    use_by = (datetime.now(UTC).date() + timedelta(days=2)).isoformat()
    created = request_json(
        f"{base_url}/api/v1/inventory/lots",
        method="POST",
        token=token,
        payload={
            "name": lot_name,
            "quantity": "1",
            "unit": "count",
            "location": "Kitchen/Pantry",
            "expires": use_by,
            "estimated_cost": "2.50",
        },
    )
    lot_id = created["item"]["id"]

    uploaded = request_json(
        f"{base_url}/api/v1/receipts",
        method="POST",
        token=token,
        payload={
            "filename": f"container-smoke-{stamp}.txt",
            "mime_type": "text/plain",
            "text": f"Store: Container Market\nDate: {datetime.now(UTC).date().isoformat()}\n{receipt_item},2,count,5.00\nTotal: 5.00\n",
        },
    )
    receipt_id = uploaded["receipt"]["id"]
    extracted = request_json(f"{base_url}/api/v1/receipts/{receipt_id}/extract", method="POST", token=token)
    review = extracted["review"]
    require(review.get("store") == "Container Market", f"Unexpected receipt store: {review}")
    require(receipt_item in receipt_item_names(review), f"Receipt extraction missed {receipt_item!r}: {review}")
    committed = request_json(f"{base_url}/api/v1/receipts/{receipt_id}/commit", method="POST", token=token, payload={})
    committed_lot_names = {str(row.get("name") or row.get("product_name")) for row in committed.get("lots", [])}
    require(receipt_item in committed_lot_names, f"Receipt commit did not create expected lot: {committed}")

    dashboard_after = request_json(f"{base_url}/api/v1/dashboard", token=token)
    find_lot(dashboard_after, lot_id, lot_name)
    revision_after_mutation = dashboard_after["revision"]
    require(revision_after_mutation > dashboard_before["revision"], "State revision did not advance after API mutations")

    log("restarting service and checking persisted state")
    docker_compose(["restart", args.service], token=token, timeout=90)
    wait_ready(base_url, timeout=args.ready_timeout)
    dashboard_after_restart = request_json(f"{base_url}/api/v1/dashboard", token=token)
    find_lot(dashboard_after_restart, lot_id, lot_name)
    require(
        dashboard_after_restart["revision"] >= revision_after_mutation,
        "State revision regressed after container restart",
    )

    log("running CLI backup and restore inside the image")
    doctor = docker_json(
        args.container, ["python", "scripts/pantryos.py", "--db", "/app/data/pantryos.sqlite3", "doctor"], user="10001:10001"
    )
    backup = docker_json(
        args.container,
        ["python", "scripts/pantryos.py", "--db", "/app/data/pantryos.sqlite3", "backup", "--output", backup_path],
        user="10001:10001",
    )
    verify = docker_json(
        args.container,
        ["python", "scripts/pantryos.py", "--db", restored_db, "restore", "--input", backup_path, "--verify-only"],
        user="10001:10001",
    )
    restore = docker_json(
        args.container,
        ["python", "scripts/pantryos.py", "--db", restored_db, "restore", "--input", backup_path, "--verify"],
        user="10001:10001",
    )
    restored_doctor = docker_json(args.container, ["python", "scripts/pantryos.py", "--db", restored_db, "doctor"], user="10001:10001")
    require(backup.get("format") == "archive", f"Expected archive backup, got {backup}")
    require(backup.get("receipt_upload_count", 0) >= 1, f"Backup archive did not include receipt uploads: {backup}")
    require(verify.get("verified") is True and verify.get("restored") is False, f"Verify-only restore failed: {verify}")
    require(restore.get("verified") is True and restore.get("restored") is True, f"Restore failed: {restore}")
    require(restored_doctor.get("counts") == doctor.get("counts"), "Restored database counts do not match source database")

    verifier_tail = "skipped"
    if not args.skip_image_verifier:
        log("running dependency-free verifier inside the image")
        verifier_tail = run_container_verifier(args.container)

    return {
        "ok": True,
        "base_url": base_url,
        "docker_server_version": docker_version,
        "health": health,
        "container": args.container,
        "uid": uid,
        "created_lot_id": lot_id,
        "created_lot_name": lot_name,
        "receipt_id": receipt_id,
        "receipt_item": receipt_item,
        "revision_after_restart": dashboard_after_restart["revision"],
        "backup": backup_path,
        "restore_database": restored_db,
        "source_counts": doctor["counts"],
        "restored_counts": restored_doctor["counts"],
        "image_verifier": verifier_tail,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run PantryOS Docker release smoke checks.")
    parser.add_argument("--base-url", default=os.environ.get("PANTRYOS_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--token", default=os.environ.get("PANTRYOS_API_TOKEN") or DEFAULT_TOKEN)
    parser.add_argument("--service", default=os.environ.get("PANTRYOS_COMPOSE_SERVICE") or DEFAULT_SERVICE)
    parser.add_argument("--container", default=os.environ.get("PANTRYOS_CONTAINER") or DEFAULT_CONTAINER)
    parser.add_argument("--ready-timeout", type=int, default=90)
    parser.add_argument("--build-timeout", type=int, default=300)
    parser.add_argument(
        "--skip-image-verifier", action="store_true", help="Skip docker exec python scripts/check.py after backup/restore checks."
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_smoke(args)
    except ContainerSmokeFailure as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
