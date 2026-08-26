"""Audit PantryOS Docker image and container hardening controls."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = "pantryos-pantryos:latest"
DEFAULT_CONTAINER = "pantryos"
DEFAULT_TOKEN = "local-dev-token"


@dataclass(frozen=True)
class AuditCheck:
    name: str
    ok: bool
    detail: str


class ImageHardeningFailure(AssertionError):
    """Raised when a required hardening control is missing."""


def require(checks: list[AuditCheck], name: str, condition: bool, detail: str) -> None:
    checks.append(AuditCheck(name=name, ok=condition, detail=detail))


def run_command(args: list[str], *, env: dict[str, str] | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
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
        raise ImageHardeningFailure(f"Required command is not available: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ImageHardeningFailure(f"Command timed out after {timeout}s: {' '.join(args)}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ImageHardeningFailure(f"Command failed ({completed.returncode}): {' '.join(args)}\n{detail}")
    return completed


def compose_env(token: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PANTRYOS_API_TOKEN"] = token
    env.setdefault("PANTRYOS_PORT", "8765")
    return env


def load_compose_config(token: str) -> dict[str, Any]:
    completed = run_command(["docker", "compose", "config", "--format", "json"], env=compose_env(token), timeout=60)
    return json.loads(completed.stdout)


def docker_inspect(target: str) -> dict[str, Any]:
    completed = run_command(["docker", "inspect", target, "--format", "{{json .}}"], timeout=60)
    return json.loads(completed.stdout)


def canonical_capabilities(values: Any) -> list[str]:
    return sorted(str(value).upper().removeprefix("CAP_") for value in values or [])

def process_status(container: str) -> dict[str, str]:
    completed = run_command(["docker", "exec", container, "cat", "/proc/1/status"], timeout=30)
    fields: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key] = value.strip()
    return fields


def static_checks() -> list[AuditCheck]:
    checks: list[AuditCheck] = []
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    entrypoint = (ROOT / "scripts" / "docker_entrypoint.py").read_text(encoding="utf-8")

    require(checks, "dockerfile-slim-base", dockerfile.startswith("FROM python:3.12-slim"), "Dockerfile uses the Python slim runtime base.")
    require(checks, "dockerfile-no-pyc", "PYTHONDONTWRITEBYTECODE=1" in dockerfile, "Image disables Python bytecode writes.")
    require(checks, "dockerfile-apt-no-recommends", "--no-install-recommends" in dockerfile, "APT installs avoid recommended packages.")
    require(checks, "dockerfile-apt-cache-clean", "rm -rf /var/lib/apt/lists/*" in dockerfile, "APT package lists are removed in the same layer.")
    require(checks, "dockerfile-dedicated-user", "--uid 10001" in dockerfile and "--gid 10001" in dockerfile, "Image defines dedicated pantryos UID/GID 10001.")
    require(checks, "dockerfile-healthcheck", "HEALTHCHECK" in dockerfile and "/api/v1/health/ready" in dockerfile, "Image healthcheck probes readiness.")
    require(checks, "dockerfile-packages-docker-contract", ".dockerignore" in dockerfile and "compose.yaml" in dockerfile, "Image packages Docker contract files for in-image audits.")
    require(checks, "entrypoint-drops-privileges", "os.setuid(user.pw_uid)" in entrypoint and "os.setgid(user.pw_gid)" in entrypoint, "Entrypoint drops from root to pantryos user after data-volume repair.")
    require(checks, "compose-read-only-root", "read_only: true" in compose, "Compose enables a read-only root filesystem.")
    require(checks, "compose-drop-capabilities", "cap_drop:" in compose and "- ALL" in compose, "Compose drops all Linux capabilities by default.")
    require(checks, "compose-minimal-cap-add", all(item in compose for item in ("- CHOWN", "- SETGID", "- SETUID")), "Compose adds only startup capabilities needed for volume ownership repair and privilege drop.")
    require(checks, "compose-pids-limit", "pids_limit: 256" in compose, "Compose constrains process count.")
    require(checks, "compose-no-new-privileges", "no-new-privileges:true" in compose, "Compose enables no-new-privileges.")
    require(checks, "compose-tmpfs", "/tmp:mode=1777,size=64m" in compose, "Compose provides only bounded /tmp tmpfs scratch space.")
    require(checks, "compose-data-volume", "pantryos-data:/app/data" in compose, "Compose writes application state only through the named /app/data volume.")
    require(checks, "compose-path-allowlists", "PANTRYOS_DATA_DIR: /app/data" in compose and "PANTRYOS_BACKUP_DIR: /app/data/backups" in compose, "Container path allowlists constrain data and backup paths.")
    for pattern in (".git", ".codex", "data/*.sqlite3", "data/backups/"):
        require(checks, f"dockerignore-{pattern}", pattern in dockerignore, f".dockerignore excludes {pattern} from build context.")
    return checks


def compose_checks(config: dict[str, Any]) -> list[AuditCheck]:
    checks: list[AuditCheck] = []
    service = config.get("services", {}).get("pantryos", {})
    require(checks, "compose-config-read-only", service.get("read_only") is True, "Rendered Compose config has read_only=true.")
    require(checks, "compose-config-cap-drop", "ALL" in list(service.get("cap_drop") or []), "Rendered Compose config drops all capabilities by default.")
    require(checks, "compose-config-minimal-cap-add", canonical_capabilities(service.get("cap_add")) == ["CHOWN", "SETGID", "SETUID"], "Rendered Compose config adds only CHOWN, SETGID, and SETUID.")
    require(checks, "compose-config-pids-limit", int(service.get("pids_limit") or 0) == 256, "Rendered Compose config sets pids_limit=256.")
    require(checks, "compose-config-no-new-privileges", "no-new-privileges:true" in list(service.get("security_opt") or []), "Rendered Compose config has no-new-privileges.")
    tmpfs = service.get("tmpfs") or []
    require(checks, "compose-config-tmpfs", any(str(item).startswith("/tmp:") and "size=64m" in str(item) for item in tmpfs), "Rendered Compose config has bounded /tmp tmpfs.")
    volumes = [str(item.get("source", "")) + ":" + str(item.get("target", "")) if isinstance(item, dict) else str(item) for item in service.get("volumes") or []]
    require(checks, "compose-config-data-volume", any("pantryos-data" in item and "/app/data" in item for item in volumes), "Rendered Compose config mounts the named data volume at /app/data.")
    return checks


def live_checks(image: str, container: str) -> list[AuditCheck]:
    checks: list[AuditCheck] = []
    image_config = docker_inspect(image)
    container_config = docker_inspect(container)
    host_config = container_config.get("HostConfig", {})
    container_runtime = container_config.get("Config", {})
    state = container_config.get("State", {})
    status = process_status(container)

    image_env = list(image_config.get("Config", {}).get("Env") or [])
    require(checks, "image-no-runtime-token", not any(item.startswith("PANTRYOS_API_TOKEN=") for item in image_env), "Image metadata does not bake a PantryOS API token.")
    require(checks, "image-entrypoint", image_config.get("Config", {}).get("Entrypoint") == ["python", "scripts/docker_entrypoint.py"], "Image entrypoint uses the privilege-drop wrapper.")
    require(checks, "image-healthcheck", bool(image_config.get("Config", {}).get("Healthcheck")), "Image metadata includes a healthcheck.")
    require(checks, "container-healthy", state.get("Status") == "running" and state.get("Health", {}).get("Status") == "healthy", "Container is running and healthy.")
    require(checks, "container-not-privileged", host_config.get("Privileged") is False, "Container is not privileged.")
    require(checks, "container-read-only-root", host_config.get("ReadonlyRootfs") is True, "Container root filesystem is read-only.")
    require(checks, "container-no-new-privileges", "no-new-privileges:true" in list(host_config.get("SecurityOpt") or []), "Container runs with no-new-privileges.")
    require(checks, "container-cap-drop-all", "ALL" in list(host_config.get("CapDrop") or []), "Container drops all Linux capabilities by default.")
    require(checks, "container-minimal-cap-add", canonical_capabilities(host_config.get("CapAdd")) == ["CHOWN", "SETGID", "SETUID"], "Container adds only startup capabilities needed for chown/setuid.")
    require(checks, "container-pids-limit", int(host_config.get("PidsLimit") or 0) == 256, "Container pids limit is 256.")
    tmpfs = host_config.get("Tmpfs") or {}
    require(checks, "container-tmpfs", "/tmp" in tmpfs and "size=64m" in str(tmpfs["/tmp"]), "Container has bounded /tmp tmpfs.")
    mounts = container_config.get("Mounts") or []
    require(checks, "container-single-writable-mount", len(mounts) == 1 and mounts[0].get("Type") == "volume" and mounts[0].get("Destination") == "/app/data" and mounts[0].get("RW") is True, "Only /app/data named volume is writable.")
    require(checks, "container-runtime-data-dir", "PANTRYOS_DATA_DIR=/app/data" in list(container_runtime.get("Env") or []), "Runtime data directory is /app/data.")
    require(checks, "process-non-root", status.get("Uid", "").split()[0] == "10001" and status.get("Gid", "").split()[0] == "10001", "PantryOS process runs as UID/GID 10001.")
    require(checks, "process-no-new-privileges", status.get("NoNewPrivs") == "1", "PantryOS process has NoNewPrivs=1.")
    require(checks, "process-no-effective-capabilities", status.get("CapEff") in {"0000000000000000", "0"}, "PantryOS process has no effective Linux capabilities after privilege drop.")
    return checks


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    checks = static_checks()
    checks.extend(compose_checks(load_compose_config(args.token)))
    if not args.skip_live:
        checks.extend(live_checks(args.image, args.container))
    failures = [check for check in checks if not check.ok]
    if failures:
        return {"ok": False, "checks": [check.__dict__ for check in checks], "failed": [check.name for check in failures]}
    return {"ok": True, "checks": [check.__dict__ for check in checks], "check_count": len(checks), "live": not args.skip_live}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit PantryOS Docker image hardening controls.")
    parser.add_argument("--image", default=os.environ.get("PANTRYOS_IMAGE") or DEFAULT_IMAGE)
    parser.add_argument("--container", default=os.environ.get("PANTRYOS_CONTAINER") or DEFAULT_CONTAINER)
    parser.add_argument("--token", default=os.environ.get("PANTRYOS_API_TOKEN") or DEFAULT_TOKEN)
    parser.add_argument("--skip-live", action="store_true", help="Only audit files and rendered Compose config; skip docker inspect/exec.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_audit(args)
    except ImageHardeningFailure as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())