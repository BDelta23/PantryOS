from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_component(name: str) -> str:
    return (ROOT / "custom_components" / "pantryos" / name).read_text(encoding="utf-8")


def test_home_assistant_services_sensors_and_translations_cover_current_surface() -> None:
    init_py = read_component("__init__.py")
    sensor_py = read_component("sensor.py")
    coordinator_py = read_component("coordinator.py")
    services_yaml = read_component("services.yaml")
    strings_json = json.loads(read_component("strings.json"))

    for service in (
        "discard_item",
        "rebuild_shopping",
        "start_cooking",
        "complete_cooking",
        "cancel_cooking",
        "promote_suggested_purchases",
    ):
        assert f'"{service}"' in init_py
        assert f"{service}:" in services_yaml

    assert "_active_runtime(hass)" in init_py
    assert "PantryRuntime" in init_py
    assert "entry.runtime_data" in init_py
    assert "PantryDataCoordinator" in coordinator_py
    assert "async_refresh_from_events" in init_py
    assert "async_track_time_interval" in init_py
    assert "self._coordinator.summary()" in sensor_py
    assert "await self._pantry.async_refresh()" not in sensor_py
    assert "async_rebuild_shopping" in init_py
    assert "async_start_cooking_session" in init_py
    assert "async_complete_cooking_session" in init_py
    assert "async_cancel_cooking_session" in init_py
    assert "leftover_count" in sensor_py
    assert "state_revision" in sensor_py
    sensor_strings = strings_json["entity"]["sensor"]
    assert sensor_strings["leftover_count"]["name"] == "Leftovers"
    assert sensor_strings["state_revision"]["name"] == "State revision"


def test_home_assistant_diagnostics_redact_tokens_receipts_and_paths() -> None:
    diagnostics_path = ROOT / "custom_components" / "pantryos" / "diagnostics.py"
    spec = importlib.util.spec_from_file_location("pantryos_diagnostics_contract", diagnostics_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    payload = {
        "api_token": "secret-token",
        "Authorization": "Bearer secret-token",
        "safe": "visible",
        "receipt_text": "Store: Private Market",
        "nested": [{"storage_path": "C:/private/receipt.txt", "count": 1}],
    }
    sanitized = module.sanitize_diagnostics_payload(payload)
    serialized = json.dumps(sanitized)

    assert sanitized["safe"] == "visible"
    assert "secret-token" not in serialized
    assert "Private Market" not in serialized
    assert "C:/private/receipt.txt" not in serialized
    assert sanitized["api_token"] == module.REDACTED
    assert sanitized["nested"][0]["storage_path"] == module.REDACTED
    assert module.sanitized_base_url("http://user:pass@example.local:8765/private?token=secret") == "http://example.local:8765"


def load_coordinator_module():
    coordinator_path = ROOT / "custom_components" / "pantryos" / "coordinator.py"
    spec = importlib.util.spec_from_file_location("pantryos_coordinator_contract", coordinator_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_home_assistant_coordinator_marks_unavailable_and_recovers_without_losing_cache() -> None:
    module = load_coordinator_module()

    class FakeClient:
        def __init__(self) -> None:
            self.available = False
            self.responses = [
                {"revision": 1, "summary": {"total_items": 3, "state_revision": 1}},
                RuntimeError("offline"),
                {"revision": 2, "summary": {"total_items": 4, "state_revision": 2}},
            ]

        async def async_refresh(self):
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            self.available = True
            return response

    async def scenario() -> None:
        client = FakeClient()
        coordinator = module.PantryDataCoordinator(client)

        await coordinator.async_refresh()
        assert coordinator.available is True
        assert coordinator.summary()["total_items"] == 3

        try:
            await coordinator.async_refresh()
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected refresh failure")

        assert coordinator.available is False
        assert client.available is False
        assert coordinator.summary()["total_items"] == 3
        assert coordinator.last_error == "offline"

        await coordinator.async_refresh()
        assert coordinator.available is True
        assert coordinator.summary()["total_items"] == 4
        assert coordinator.last_revision == 2
        assert coordinator.last_error is None

    asyncio.run(scenario())


def test_home_assistant_coordinator_refreshes_snapshot_when_events_advance_revision() -> None:
    module = load_coordinator_module()

    class FakeClient:
        def __init__(self) -> None:
            self.refresh_count = 0

        async def async_refresh(self):
            self.refresh_count += 1
            return {
                "revision": self.refresh_count,
                "summary": {"total_items": self.refresh_count, "state_revision": self.refresh_count},
            }

        async def async_events(self, *, limit: int = 25, after_revision: int | None = None):
            assert limit == 25
            if after_revision == 1:
                return {"items": [{"revision": 2, "type": "inventory.lot_added"}], "revision": 2, "limit": limit}
            return {"items": [], "revision": after_revision or 0, "limit": limit}

    async def scenario() -> None:
        client = FakeClient()
        coordinator = module.PantryDataCoordinator(client)
        await coordinator.async_refresh()

        changed = await coordinator.async_refresh_from_events()
        assert changed is True
        assert client.refresh_count == 2
        assert coordinator.last_revision == 2
        assert coordinator.summary()["total_items"] == 2

        changed = await coordinator.async_refresh_from_events()
        assert changed is False
        assert client.refresh_count == 2

    asyncio.run(scenario())
