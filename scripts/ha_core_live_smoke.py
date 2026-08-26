"""Docker smoke for PantryOS against real Home Assistant Core and PantryOS Core.

This smoke creates a disposable Docker network, a disposable PantryOS Core
container, and a fixed non-secret smoke token on that private network. The Home
Assistant container talks to that isolated Core instance, so the release proof
does not require the user's live token and does not mutate the user's database.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HA_IMAGE = "ghcr.io/home-assistant/home-assistant:stable"
DEFAULT_CORE_IMAGE = "pantryos-pantryos:latest"
SMOKE_TOKEN = "pantryos-ha-core-smoke-public-token"


class HACoreLiveSmokeFailure(AssertionError):
    """Raised when the live Home Assistant Core smoke cannot prove its contract."""


CONTAINER_SMOKE = r"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import sys
from datetime import UTC, datetime
from types import MappingProxyType

sys.path.insert(0, "/config")
logging.getLogger().setLevel(logging.ERROR)

from homeassistant.bootstrap import async_setup_hass
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.runner import RuntimeConfig

from custom_components.pantryos.const import CONF_API_TOKEN, CONF_BASE_URL, DOMAIN

BASE_URL = os.environ["PANTRYOS_BASE_URL"]
TOKEN = "pantryos-ha-core-smoke-public-token"


def progress(message):
    print(f"[ha-core-smoke] {message}", file=sys.stderr, flush=True)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


async def wait_for(predicate, *, timeout=45.0, interval=0.2):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    raise AssertionError("Timed out waiting for Home Assistant state to update")


async def main():
    progress("setting up Home Assistant")
    hass = await asyncio.wait_for(async_setup_hass(RuntimeConfig(config_dir="/config", skip_pip=True, log_no_color=True)), timeout=90)
    require(hass is not None, "async_setup_hass returned None")
    progress("starting Home Assistant")
    await asyncio.wait_for(hass.async_start(), timeout=60)
    await asyncio.wait_for(hass.async_block_till_done(), timeout=60)

    event_payloads = []
    unsubscribe_event = None
    entry = None
    try:
        now = datetime.now(UTC)
        entry = ConfigEntry(
            created_at=now,
            data={CONF_BASE_URL: BASE_URL, CONF_API_TOKEN: TOKEN},
            disabled_by=None,
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            entry_id="pantryos_live_core_smoke",
            minor_version=1,
            modified_at=now,
            options={},
            source="user",
            state=ConfigEntryState.NOT_LOADED,
            subentries_data=(),
            title="PantryOS Live Core Smoke",
            unique_id="pantryos-live-core-smoke",
            version=1,
        )
        progress("adding PantryOS config entry")
        add_result = hass.config_entries.async_add(entry)
        if inspect.isawaitable(add_result):
            await add_result
        if entry.state is ConfigEntryState.LOADED:
            setup_ok = True
            progress("config entry was loaded by async_add")
        else:
            progress("setting up PantryOS config entry")
            setup_ok = await asyncio.wait_for(hass.config_entries.async_setup(entry.entry_id), timeout=60)
            progress(f"config entry setup returned {setup_ok}")
        await asyncio.sleep(0)
        require(setup_ok, f"Config entry setup failed: {entry.state}")

        runtime = entry.runtime_data
        coordinator = runtime.coordinator
        progress("waiting for coordinator availability")
        await wait_for(lambda: coordinator.available, timeout=30)
        revision_before = coordinator.last_revision

        unsubscribe_event = hass.bus.async_listen(
            f"{DOMAIN}_updated",
            lambda event: event_payloads.append(dict(event.data)),
        )

        services = hass.services.async_services().get(DOMAIN, {})
        require("add_item" in services, "pantryos.add_item service is missing")
        require("open_item" in services, "pantryos.open_item service is missing")

        progress("calling pantryos.add_item service")
        ha_item_name = "Live HA Core Service Oats " + datetime.now(UTC).strftime("%H%M%S%f")
        await asyncio.wait_for(hass.services.async_call(
            DOMAIN,
            "add_item",
            {
                "name": ha_item_name,
                "quantity": "1",
                "unit": "count",
                "location": "Pantry",
            },
            blocking=True,
        ), timeout=60)
        await asyncio.sleep(0)
        progress("waiting for service revision")
        revision_after_service = await wait_for(
            lambda: coordinator.last_revision if coordinator.last_revision > revision_before else None,
            timeout=45,
        )

        progress("writing direct Core item through HA client")
        core_item_name = "Live HA Core Direct Rice " + datetime.now(UTC).strftime("%H%M%S%f")
        await asyncio.wait_for(runtime.client.async_add_item(
            {
                "name": core_item_name,
                "quantity": "1",
                "unit": "count",
                "location": "Pantry",
            }
        ), timeout=60)
        progress("waiting for direct Core revision")
        revision_after_core_push = await wait_for(
            lambda: coordinator.last_revision if coordinator.last_revision > revision_after_service else None,
            timeout=45,
        )
        await asyncio.sleep(0)

        sensor_ids = sorted(entity_id for entity_id in hass.states.async_entity_ids("sensor") if "pantryos" in entity_id)
        state_revision_entity = next((entity_id for entity_id in sensor_ids if entity_id.endswith("state_revision")), None)
        total_items_entity = next((entity_id for entity_id in sensor_ids if entity_id.endswith("total_items")), None)
        require(state_revision_entity, f"State revision sensor is missing; sensor_ids={sensor_ids}")
        require(total_items_entity, f"Total items sensor is missing; sensor_ids={sensor_ids}")
        state_revision_state = hass.states.get(state_revision_entity).state
        total_items_state = hass.states.get(total_items_entity).state

        progress("unloading PantryOS config entry")
        unloaded = await asyncio.wait_for(hass.config_entries.async_unload(entry.entry_id), timeout=60)
        await asyncio.sleep(0)
        remaining_services = sorted(hass.services.async_services().get(DOMAIN, {}))
        require(unloaded, "Config entry unload returned false")

        print(
            json.dumps(
                {
                    "ok": True,
                    "ha_version": HA_VERSION,
                    "setup_ok": setup_ok,
                    "unloaded": unloaded,
                    "remaining_services": remaining_services,
                    "sensor_count": len(sensor_ids),
                    "state_revision_entity": state_revision_entity,
                    "state_revision_state": state_revision_state,
                    "total_items_entity": total_items_entity,
                    "total_items_state": total_items_state,
                    "revision_before": revision_before,
                    "revision_after_service": revision_after_service,
                    "revision_after_core_push": revision_after_core_push,
                    "event_count": len(event_payloads),
                    "event_payloads": event_payloads[-5:],
                    "ha_service_item": ha_item_name,
                    "core_push_item": core_item_name,
                },
                sort_keys=True,
            )
        )
        sys.stdout.flush()
        os._exit(0)
    finally:
        if unsubscribe_event is not None:
            unsubscribe_event()
        runtime = getattr(entry, "runtime_data", None) if entry is not None else None
        stream_task = getattr(runtime, "stream_task", None)
        if stream_task is not None:
            stream_task.cancel()
        progress("leaving Home Assistant smoke process")


asyncio.run(main())
"""


def redact(text: str) -> str:
    return text.replace(SMOKE_TOKEN, "<smoke-token>")


def run_command(args: list[str], *, timeout: int, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            args,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        raise HACoreLiveSmokeFailure(f"Required command is not available: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        detail = redact((stderr + "\n" + stdout).strip())
        raise HACoreLiveSmokeFailure(f"Command timed out after {timeout}s: {' '.join(args)}\n{detail}") from exc
    if completed.returncode != 0:
        detail = redact((completed.stderr or completed.stdout).strip())
        raise HACoreLiveSmokeFailure(f"Command failed ({completed.returncode}): {' '.join(args)}\n{detail}")
    return completed


def cleanup_command(args: list[str], *, env: dict[str, str]) -> None:
    subprocess.run(args, cwd=ROOT, check=False, capture_output=True, text=True, timeout=60, env=env)


def prepare_config(directory: Path) -> None:
    custom_components = directory / "custom_components"
    custom_components.mkdir()
    shutil.copytree(ROOT / "custom_components" / "pantryos", custom_components / "pantryos")
    (directory / "configuration.yaml").write_text("homeassistant:\n  name: PantryOS Live Core Smoke\n", encoding="utf-8")
    (directory / "ha_core_live_smoke.py").write_text(CONTAINER_SMOKE, encoding="utf-8")


def isolated_names() -> dict[str, str]:
    suffix = uuid.uuid4().hex[:12]
    return {
        "network": f"pantryos-ha-core-smoke-{suffix}",
        "container": f"pantryos-ha-core-smoke-core-{suffix}",
        "volume": f"pantryos-ha-core-smoke-data-{suffix}",
    }


def start_isolated_core(args: argparse.Namespace, *, env: dict[str, str], names: dict[str, str]) -> None:
    run_command(["docker", "network", "create", names["network"]], timeout=60, env=env)
    run_command(["docker", "volume", "create", names["volume"]], timeout=60, env=env)
    core_env = env.copy()
    core_env["PANTRYOS_API_TOKEN"] = SMOKE_TOKEN
    run_command(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            names["container"],
            "--network",
            names["network"],
            "--volume",
            f"{names['volume']}:/app/data",
            "--env",
            "PANTRYOS_API_TOKEN",
            "--env",
            "PANTRYOS_DATA_DIR=/app/data",
            "--env",
            "PANTRYOS_BACKUP_DIR=/app/data/backups",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "SETGID",
            "--cap-add",
            "SETUID",
            "--pids-limit",
            "256",
            "--tmpfs",
            "/tmp:mode=1777,size=64m",
            "--security-opt",
            "no-new-privileges:true",
            args.core_image,
        ],
        timeout=args.timeout,
        env=core_env,
    )
    wait_for_healthy_container(names["container"], timeout=args.timeout, env=env)


def wait_for_healthy_container(container: str, *, timeout: int, env: dict[str, str]) -> None:
    deadline = time.monotonic() + timeout
    last_status = "unknown"
    while time.monotonic() < deadline:
        completed = run_command(
            ["docker", "inspect", "--format", "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}", container],
            timeout=30,
            env=env,
        )
        last_status = completed.stdout.strip()
        if last_status.endswith(" healthy"):
            return
        if last_status.startswith("exited") or last_status.startswith("dead"):
            logs = subprocess.run(
                ["docker", "logs", container],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            raise HACoreLiveSmokeFailure(f"Isolated PantryOS Core container stopped: {last_status}\n{redact(logs.stderr or logs.stdout)}")
        time.sleep(1)
    raise HACoreLiveSmokeFailure(f"Timed out waiting for isolated PantryOS Core health: {last_status}")


def cleanup_isolated_core(names: dict[str, str], *, env: dict[str, str]) -> None:
    cleanup_command(["docker", "rm", "--force", names["container"]], env=env)
    cleanup_command(["docker", "volume", "rm", names["volume"]], env=env)
    cleanup_command(["docker", "network", "rm", names["network"]], env=env)


def run_ha_container(
    args: argparse.Namespace, *, config_dir: Path, env: dict[str, str], base_url: str, network: str
) -> subprocess.CompletedProcess[str]:
    ha_env = env.copy()
    ha_env["PANTRYOS_BASE_URL"] = base_url
    return run_command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            "--volume",
            f"{config_dir}:/config",
            "--env",
            "PANTRYOS_BASE_URL",
            args.image,
            "python",
            "/config/ha_core_live_smoke.py",
        ],
        timeout=args.timeout,
        env=ha_env,
    )


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    env = os.environ.copy()
    names = isolated_names()
    base_url = f"http://{names['container']}:8765"
    try:
        start_isolated_core(args, env=env, names=names)
        with tempfile.TemporaryDirectory(prefix="pantryos-ha-core-live-") as temp:
            config_dir = Path(temp)
            prepare_config(config_dir)
            completed = run_ha_container(args, config_dir=config_dir, env=env, base_url=base_url, network=names["network"])
    finally:
        cleanup_isolated_core(names, env=env)
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise HACoreLiveSmokeFailure(f"Smoke did not return JSON:\n{redact(completed.stdout)}") from exc
    result["core_mode"] = "isolated"
    result["base_url"] = base_url
    result["network"] = names["network"]
    result["isolated_container"] = names["container"]
    result["isolated_volume"] = names["volume"]
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run PantryOS against real Home Assistant Core and an isolated PantryOS Core Docker runtime."
    )
    parser.add_argument("--image", default=os.environ.get("PANTRYOS_HA_IMAGE") or DEFAULT_HA_IMAGE, help="Home Assistant Core image to run")
    parser.add_argument(
        "--core-image", default=os.environ.get("PANTRYOS_CORE_IMAGE") or DEFAULT_CORE_IMAGE, help="PantryOS image to run as isolated Core"
    )
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("PANTRYOS_HA_CORE_SMOKE_TIMEOUT", "300")))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_smoke(args)
    except HACoreLiveSmokeFailure as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
