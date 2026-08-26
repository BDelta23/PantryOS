"""Docker smoke for the PantryOS custom integration in an installed HA runtime."""

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


class HAInstalledSmokeFailure(AssertionError):
    """Raised when the installed Home Assistant smoke cannot prove its contract."""


CONTAINER_SMOKE = r"""
from __future__ import annotations

import asyncio
import importlib
import json
import sys

sys.path.insert(0, "/config")

import custom_components.pantryos as integration
from homeassistant.exceptions import ConfigEntryAuthFailed


class FakeServices:
    def __init__(self):
        self.handlers = {}
        self.removed = []

    def has_service(self, domain, service):
        return (domain, service) in self.handlers

    def async_register(self, domain, service, handler, schema=None):
        self.handlers[(domain, service)] = {"handler": handler, "schema": schema}

    def async_remove(self, domain, service):
        self.removed.append((domain, service))
        self.handlers.pop((domain, service), None)


class FakeBus:
    def __init__(self):
        self.events = []
        self.last_event_data = {}

    def async_fire(self, event_type, event_data=None):
        self.events.append(event_type)
        self.last_event_data = event_data or {}


class FakeConfigEntries:
    def __init__(self):
        self.forwarded = []
        self.unloaded = []

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append((entry.entry_id, tuple(platforms)))

    async def async_unload_platforms(self, entry, platforms):
        self.unloaded.append((entry.entry_id, tuple(platforms)))
        return True


class FakeHass:
    def __init__(self):
        self.data = {}
        self.services = FakeServices()
        self.bus = FakeBus()
        self.config_entries = FakeConfigEntries()
        self.tasks = []

    def async_create_task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task


class FakeEntry:
    def __init__(self, data=None, entry_id="entry-1"):
        self.data = data or {"base_url": "http://pantry.local:8765", "api_token": "good-token"}
        self.entry_id = entry_id
        self.runtime_data = None


class FakeServiceCall:
    def __init__(self, data):
        self.data = data


class FakeClient:
    instances = []

    def __init__(self, base_url, token):
        self.base_url = base_url
        self.token = token
        self.available = False
        self.refresh_count = 0
        self.added_items = []
        self.opened_items = []
        FakeClient.instances.append(self)

    async def async_instance(self):
        if self.token == "bad-token":
            raise integration.PantryAPIAuthError("invalid token", status=401, code="unauthorized")
        return {"instance_id": "pantry-instance", "schema_version": 4}

    async def async_refresh(self):
        self.refresh_count += 1
        self.available = True
        return {
            "revision": self.refresh_count,
            "summary": {
                "total_items": self.refresh_count,
                "state_revision": self.refresh_count,
                "leftover_count": 1,
            },
        }

    async def async_event_stream(self, *, after_revision=None, timeout_seconds=25, heartbeat_seconds=10):
        return {
            "items": [
                {
                    "id": "evt-1",
                    "event_type": "cooking.started",
                    "revision": self.refresh_count + 1,
                    "lot_id": "lot-1",
                }
            ],
            "revision": self.refresh_count + 1,
        }

    async def async_add_item(self, data):
        self.added_items.append(dict(data))
        return {"revision": self.refresh_count + 1, "item": {"id": "lot-1", **dict(data)}}

    async def async_open_item(self, item_id, *, opened_at=None):
        self.opened_items.append({"item_id": item_id, "opened_at": opened_at})
        return {"revision": self.refresh_count + 1, "opened": True}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


async def scenario():
    # These imports must resolve against the installed Home Assistant package.
    importlib.import_module("custom_components.pantryos.config_flow")
    importlib.import_module("custom_components.pantryos.sensor")
    importlib.import_module("custom_components.pantryos.diagnostics")

    integration.PantryAPIClient = FakeClient

    hass = FakeHass()
    entry = FakeEntry()
    require(await integration.async_setup_entry(hass, entry) is True, "setup did not return True")
    runtime = hass.data[integration.DOMAIN][entry.entry_id]
    require(entry.runtime_data is runtime, "runtime_data was not stored on entry")
    require(runtime.coordinator.available is True, "coordinator did not become available")
    require(hass.config_entries.forwarded == [(entry.entry_id, tuple(integration.PLATFORMS))], "platforms not forwarded")
    require(hass.services.has_service(integration.DOMAIN, "add_item"), "add_item service missing")
    require(hass.services.has_service(integration.DOMAIN, "open_item"), "open_item service missing")

    add_registration = hass.services.handlers[(integration.DOMAIN, "add_item")]
    add_registration["schema"]({"name": "Installed HA Oats", "quantity": "1", "unit": "count"})
    await add_registration["handler"](FakeServiceCall({"name": "Installed HA Oats", "quantity": "1", "unit": "count"}))
    require(runtime.client.added_items == [{"name": "Installed HA Oats", "quantity": "1", "unit": "count"}], "add_item did not call client")

    open_registration = hass.services.handlers[(integration.DOMAIN, "open_item")]
    open_registration["schema"]({"item_id": "lot-1", "opened_at": "2026-08-26"})
    await open_registration["handler"](FakeServiceCall({"item_id": "lot-1", "opened_at": "2026-08-26"}))
    require(runtime.client.opened_items == [{"item_id": "lot-1", "opened_at": "2026-08-26"}], "open_item did not call client")

    for _ in range(40):
        if hass.bus.last_event_data.get("event_types") == ["cooking.started"]:
            break
        await asyncio.sleep(0.05)
    require(hass.bus.events[-1] == f"{integration.DOMAIN}_updated", "update event was not fired")
    require(hass.bus.last_event_data["event_types"] == ["cooking.started"], "event metadata was not surfaced")
    require(hass.bus.last_event_data["events"][0]["lot_id"] == "lot-1", "event details were not bounded and copied")

    require(await integration.async_unload_entry(hass, entry) is True, "unload did not return True")
    await asyncio.sleep(0)
    require(hass.tasks and all(task.cancelled() for task in hass.tasks), "stream task was not cancelled")
    require((integration.DOMAIN, "add_item") in hass.services.removed, "services were not removed")

    try:
        await integration.async_setup_entry(FakeHass(), FakeEntry({"base_url": "http://pantry.local:8765", "api_token": "bad-token"}, "bad"))
    except ConfigEntryAuthFailed:
        pass
    else:
        raise AssertionError("auth failure did not raise ConfigEntryAuthFailed")

    return {
        "ok": True,
        "ha_version": importlib.import_module("homeassistant.const").__version__,
        "services": sorted(service for domain, service in hass.services.removed if domain == integration.DOMAIN),
        "platforms": list(integration.PLATFORMS),
        "last_event_types": hass.bus.last_event_data["event_types"],
        "last_revision": runtime.coordinator.last_revision,
        "stream_task_cancelled": all(task.cancelled() for task in hass.tasks),
    }


print(json.dumps(asyncio.run(scenario()), sort_keys=True))
"""


def run_command(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            args,
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise HAInstalledSmokeFailure(f"Required command is not available: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HAInstalledSmokeFailure(f"Command timed out after {timeout}s: {' '.join(args)}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise HAInstalledSmokeFailure(f"Command failed ({completed.returncode}): {' '.join(args)}\n{detail}")
    return completed


def prepare_config(directory: Path) -> None:
    custom_components = directory / "custom_components"
    custom_components.mkdir()
    shutil.copytree(ROOT / "custom_components" / "pantryos", custom_components / "pantryos")
    (directory / "configuration.yaml").write_text("homeassistant:\n  name: PantryOS Installed Smoke\n", encoding="utf-8")
    (directory / "ha_installed_smoke.py").write_text(CONTAINER_SMOKE, encoding="utf-8")


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pantryos-ha-smoke-") as temp:
        config_dir = Path(temp)
        prepare_config(config_dir)
        completed = run_command(
            [
                "docker",
                "run",
                "--rm",
                "--volume",
                f"{config_dir}:/config:ro",
                args.image,
                "python",
                "/config/ha_installed_smoke.py",
            ],
            timeout=args.timeout,
        )
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise HAInstalledSmokeFailure(f"Smoke did not return JSON:\n{completed.stdout}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run PantryOS against an installed Home Assistant Docker runtime.")
    parser.add_argument("--image", default=os.environ.get("PANTRYOS_HA_IMAGE") or DEFAULT_IMAGE)
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("PANTRYOS_HA_SMOKE_TIMEOUT", "240")))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_smoke(args)
    except HAInstalledSmokeFailure as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
