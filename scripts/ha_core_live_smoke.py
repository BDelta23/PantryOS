"""Docker smoke for PantryOS against a real Home Assistant Core runtime and live Core API.

This smoke starts the public Home Assistant container, mounts the local PantryOS
custom integration into a temporary config directory, and passes
PANTRYOS_API_TOKEN through the container environment. It mutates the live
PantryOS database by adding two uniquely named inventory lots.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = "ghcr.io/home-assistant/home-assistant:stable"
DEFAULT_NETWORK = "pantryos_default"
DEFAULT_BASE_URL = "http://pantryos:8765"


class HACoreLiveSmokeFailure(AssertionError):
    """Raised when the live Home Assistant Core smoke cannot prove its contract."""


CONTAINER_SMOKE = r"""
from __future__ import annotations

import asyncio
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
from homeassistant.const import CONF_API_TOKEN, CONF_BASE_URL
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.runner import RuntimeConfig

from custom_components.pantryos.const import DOMAIN

BASE_URL = os.environ["PANTRYOS_BASE_URL"]
TOKEN = os.environ["PANTRYOS_API_TOKEN"]


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
    hass = await async_setup_hass(RuntimeConfig(config_dir="/config", skip_pip=True, log_no_color=True))
    require(hass is not None, "async_setup_hass returned None")
    await hass.async_start()
    await hass.async_block_till_done()

    event_payloads = []
    unsubscribe_event = None
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
        hass.config_entries.async_add(entry)
        setup_ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        require(setup_ok, f"Config entry setup failed: {entry.state}")

        runtime = entry.runtime_data
        coordinator = runtime.coordinator
        await wait_for(lambda: coordinator.available, timeout=30)
        revision_before = coordinator.last_revision

        unsubscribe_event = hass.bus.async_listen(
            f"{DOMAIN}_updated",
            lambda event: event_payloads.append(dict(event.data)),
        )

        services = hass.services.async_services().get(DOMAIN, {})
        require("add_item" in services, "pantryos.add_item service is missing")
        require("open_item" in services, "pantryos.open_item service is missing")

        ha_item_name = "Live HA Core Service Oats " + datetime.now(UTC).strftime("%H%M%S%f")
        await hass.services.async_call(
            DOMAIN,
            "add_item",
            {
                "name": ha_item_name,
                "quantity": "1",
                "unit": "count",
                "location": "Pantry",
            },
            blocking=True,
        )
        await hass.async_block_till_done()
        revision_after_service = await wait_for(
            lambda: coordinator.last_revision if coordinator.last_revision > revision_before else None,
            timeout=45,
        )

        core_item_name = "Live HA Core Direct Rice " + datetime.now(UTC).strftime("%H%M%S%f")
        await runtime.client.async_add_item(
            {
                "name": core_item_name,
                "quantity": "1",
                "unit": "count",
                "location": "Pantry",
            }
        )
        revision_after_core_push = await wait_for(
            lambda: coordinator.last_revision if coordinator.last_revision > revision_after_service else None,
            timeout=45,
        )
        await hass.async_block_till_done()

        sensor_ids = sorted(entity_id for entity_id in hass.states.async_entity_ids("sensor") if "pantryos" in entity_id)
        state_revision_entity = next((entity_id for entity_id in sensor_ids if entity_id.endswith("state_revision")), None)
        total_items_entity = next((entity_id for entity_id in sensor_ids if entity_id.endswith("total_items")), None)
        require(state_revision_entity, f"State revision sensor is missing; sensor_ids={sensor_ids}")
        require(total_items_entity, f"Total items sensor is missing; sensor_ids={sensor_ids}")
        state_revision_state = hass.states.get(state_revision_entity).state
        total_items_state = hass.states.get(total_items_entity).state

        unloaded = await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
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
    finally:
        if unsubscribe_event is not None:
            unsubscribe_event()
        await hass.async_stop()
        await hass.async_block_till_done()


asyncio.run(main())
"""


def redact(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


def run_command(args: list[str], *, timeout: int, env: dict[str, str], secrets: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            args,
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        raise HACoreLiveSmokeFailure(f"Required command is not available: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HACoreLiveSmokeFailure(f"Command timed out after {timeout}s: {' '.join(args)}") from exc
    if completed.returncode != 0:
        detail = redact((completed.stderr or completed.stdout).strip(), secrets)
        raise HACoreLiveSmokeFailure(f"Command failed ({completed.returncode}): {' '.join(args)}\n{detail}")
    return completed


def prepare_config(directory: Path) -> None:
    custom_components = directory / "custom_components"
    custom_components.mkdir()
    shutil.copytree(ROOT / "custom_components" / "pantryos", custom_components / "pantryos")
    (directory / "configuration.yaml").write_text("homeassistant:\n  name: PantryOS Live Core Smoke\n", encoding="utf-8")
    (directory / "ha_core_live_smoke.py").write_text(CONTAINER_SMOKE, encoding="utf-8")


def require_token() -> str:
    token = os.environ.get("PANTRYOS_API_TOKEN")
    if not token:
        raise HACoreLiveSmokeFailure("PANTRYOS_API_TOKEN must be set in the environment; do not pass it as a command-line argument")
    return token


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    token = require_token()
    env = os.environ.copy()
    env["PANTRYOS_API_TOKEN"] = token
    env["PANTRYOS_BASE_URL"] = args.base_url
    with tempfile.TemporaryDirectory(prefix="pantryos-ha-core-live-") as temp:
        config_dir = Path(temp)
        prepare_config(config_dir)
        completed = run_command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                args.network,
                "--volume",
                f"{config_dir}:/config:ro",
                "--env",
                "PANTRYOS_API_TOKEN",
                "--env",
                "PANTRYOS_BASE_URL",
                args.image,
                "python",
                "/config/ha_core_live_smoke.py",
            ],
            timeout=args.timeout,
            env=env,
            secrets=[token],
        )
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise HACoreLiveSmokeFailure(f"Smoke did not return JSON:\n{redact(completed.stdout, [token])}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run PantryOS against a real Home Assistant Core Docker runtime and live PantryOS API.")
    parser.add_argument("--image", default=os.environ.get("PANTRYOS_HA_IMAGE") or DEFAULT_IMAGE)
    parser.add_argument("--network", default=os.environ.get("PANTRYOS_DOCKER_NETWORK") or DEFAULT_NETWORK)
    parser.add_argument("--base-url", default=os.environ.get("PANTRYOS_HA_CORE_BASE_URL") or DEFAULT_BASE_URL)
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
