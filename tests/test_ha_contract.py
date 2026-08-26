from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from contextlib import contextmanager, suppress
from pathlib import Path
from types import SimpleNamespace

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
        "open_item",
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
    assert "async_refresh_from_event_stream" in coordinator_py
    assert "async_refresh_from_events" in coordinator_py
    assert "async_create_task" in init_py
    assert "event_types" in init_py
    assert "last_events" in coordinator_py
    assert "async_listen_for_events" in init_py
    assert "async_listen_for_events" in coordinator_py
    assert "self._coordinator.summary()" in sensor_py
    assert "await self._pantry.async_refresh()" not in sensor_py
    assert "async_open_item" in init_py
    assert "async_rebuild_shopping" in init_py
    assert "async_start_cooking_session" in init_py
    assert "async_complete_cooking_session" in init_py
    assert "async_cancel_cooking_session" in init_py
    assert "leftover_count" in sensor_py
    assert "state_revision" in sensor_py
    sensor_strings = strings_json["entity"]["sensor"]
    assert sensor_strings["leftover_count"]["name"] == "Leftovers"
    assert sensor_strings["state_revision"]["name"] == "State revision"


def test_home_assistant_example_automations_cover_required_outcomes() -> None:
    examples = (ROOT / "docs" / "home_assistant" / "example_automations.yaml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for alias in (
        "PantryOS use-soon notification",
        "PantryOS grocery arrival count",
        "PantryOS cooking mode",
        "PantryOS freezer risk and value alert",
    ):
        assert f"alias: {alias}" in examples
    for entity_id in (
        "sensor.pantryos_expiring_soon",
        "sensor.pantryos_total_items",
        "sensor.pantryos_freezer_value",
        "sensor.pantryos_freezer_items",
    ):
        assert entity_id in examples
    for service in ("notify.notify", "scene.turn_on", "media_player.play_media"):
        assert f"service: {service}" in examples
    assert "event_type: pantryos_updated" in examples
    assert "cooking.started" in examples
    assert "trigger.event.data.event_types" in examples
    assert "docs/home_assistant/example_automations.yaml" in readme


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


def test_home_assistant_event_summaries_skip_malformed_revisions() -> None:
    module = load_coordinator_module()

    summaries = module._event_summaries(
        {
            "items": [
                {"id": "bad", "event_type": "cooking.started", "revision": "not-a-number"},
                {"id": "ok", "event_type": "cooking.started", "revision": "7", "product_id": "p1"},
            ]
        }
    )

    assert summaries == [{"event_type": "cooking.started", "revision": 7, "id": "ok", "product_id": "p1"}]


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


def test_home_assistant_coordinator_refreshes_snapshot_when_event_stream_advances_revision() -> None:
    module = load_coordinator_module()

    class FakeClient:
        def __init__(self) -> None:
            self.refresh_count = 0
            self.stream_after_revision = None

        async def async_refresh(self):
            self.refresh_count += 1
            return {
                "revision": self.refresh_count,
                "summary": {"total_items": self.refresh_count, "state_revision": self.refresh_count},
            }

        async def async_event_stream(self, *, after_revision=None, timeout_seconds=25, heartbeat_seconds=10):
            self.stream_after_revision = after_revision
            assert timeout_seconds == 0.1
            assert heartbeat_seconds == 0.1
            return {"items": [{"revision": 2, "type": "ADD"}], "revision": 2, "stream": True}

    async def scenario() -> None:
        client = FakeClient()
        coordinator = module.PantryDataCoordinator(client)
        await coordinator.async_refresh()

        changed = await coordinator.async_refresh_from_event_stream(timeout_seconds=0.1, heartbeat_seconds=0.1)
        assert changed is True
        assert client.stream_after_revision == 1
        assert client.refresh_count == 2
        assert coordinator.last_revision == 2
        assert coordinator.last_event_revision == 2
        assert coordinator.summary()["total_items"] == 2

    asyncio.run(scenario())


def test_home_assistant_coordinator_continuous_listener_signals_change_and_cancels() -> None:
    module = load_coordinator_module()

    class FakeClient:
        def __init__(self) -> None:
            self.refresh_count = 0
            self.stream_count = 0

        async def async_refresh(self):
            self.refresh_count += 1
            return {
                "revision": self.refresh_count,
                "summary": {"total_items": self.refresh_count, "state_revision": self.refresh_count},
            }

        async def async_event_stream(self, *, after_revision=None, timeout_seconds=25, heartbeat_seconds=10):
            self.stream_count += 1
            if self.stream_count == 1:
                assert after_revision == 1
                assert timeout_seconds == 0.1
                assert heartbeat_seconds == 0.1
                return {"items": [{"id": "evt-2", "event_type": "ADD", "revision": 2}], "revision": 2, "stream": True}
            await asyncio.sleep(60)
            return {"items": [], "revision": 2, "stream": True}

    async def scenario() -> None:
        client = FakeClient()
        coordinator = module.PantryDataCoordinator(client)
        await coordinator.async_refresh()
        changes = []

        task = asyncio.create_task(
            coordinator.async_listen_for_events(
                lambda: changes.append(coordinator.last_revision),
                timeout_seconds=0.1,
                heartbeat_seconds=0.1,
                reconnect_seconds=0.01,
                retry_seconds=0.01,
            )
        )
        try:
            for _ in range(50):
                if changes:
                    break
                await asyncio.sleep(0.01)
            assert changes == [2]
            assert coordinator.last_events == [{"event_type": "ADD", "revision": 2, "id": "evt-2"}]
            assert coordinator.summary()["total_items"] == 2
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


@contextmanager
def fake_homeassistant_modules():
    module_names = [
        "voluptuous",
        "homeassistant",
        "homeassistant.config_entries",
        "homeassistant.core",
        "homeassistant.exceptions",
        "homeassistant.helpers",
        "homeassistant.helpers.config_validation",
        "homeassistant.helpers.event",
    ]
    original = {name: sys.modules.get(name) for name in module_names}

    voluptuous = types.ModuleType("voluptuous")

    class Invalid(Exception):
        pass

    class Schema:
        def __init__(self, schema):
            self.schema = schema

        def __call__(self, value):
            return value

    def required(key, *, default=None):
        return key

    def optional(key, *, default=None):
        return key

    def all_validator(*validators):
        def validate(value):
            for validator in validators:
                value = validator(value)
            return value

        return validate

    voluptuous.Invalid = Invalid
    voluptuous.Schema = Schema
    voluptuous.Required = required
    voluptuous.Optional = optional
    voluptuous.All = all_validator

    ha = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")
    core = types.ModuleType("homeassistant.core")
    exceptions = types.ModuleType("homeassistant.exceptions")
    helpers = types.ModuleType("homeassistant.helpers")
    cv = types.ModuleType("homeassistant.helpers.config_validation")
    event = types.ModuleType("homeassistant.helpers.event")

    class ConfigFlow:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

        async def async_set_unique_id(self, unique_id):
            self.unique_id = unique_id

        def _abort_if_unique_id_configured(self):
            self.unique_id_configured_checked = True

        def _abort_if_unique_id_mismatch(self, *, reason="wrong_instance"):
            self.unique_id_mismatch_checked = reason

        def _get_reconfigure_entry(self):
            return self.reconfigure_entry

        def _get_reauth_entry(self):
            return self.reauth_entry

        def async_create_entry(self, *, title, data):
            return {"type": "create_entry", "title": title, "data": data}

        def async_show_form(self, *, step_id, data_schema, errors):
            return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors}

        def async_update_reload_and_abort(self, entry, *, data_updates):
            entry.data = {**entry.data, **data_updates}
            return {"type": "abort", "reason": "reconfigure_successful", "data_updates": data_updates}

    class ConfigEntryNotReady(Exception):
        pass

    class ConfigEntryAuthFailed(Exception):
        pass

    class HomeAssistantError(Exception):
        pass

    config_entries.ConfigFlow = ConfigFlow
    config_entries.ConfigFlowResult = dict
    config_entries.ConfigEntry = object
    core.HomeAssistant = object
    core.ServiceCall = object
    core.CALLBACK_TYPE = object
    core.callback = lambda func: func
    exceptions.ConfigEntryNotReady = ConfigEntryNotReady
    exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    exceptions.HomeAssistantError = HomeAssistantError
    cv.string = str
    cv.date = str
    cv.boolean = bool
    cv.positive_int = int
    cv.ensure_list = lambda value: value if isinstance(value, list) else [value]

    ha.config_entries = config_entries
    ha.helpers = helpers
    helpers.config_validation = cv
    helpers.event = event

    sys.modules.update(
        {
            "voluptuous": voluptuous,
            "homeassistant": ha,
            "homeassistant.config_entries": config_entries,
            "homeassistant.core": core,
            "homeassistant.exceptions": exceptions,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.config_validation": cv,
            "homeassistant.helpers.event": event,
        }
    )
    try:
        yield SimpleNamespace(
            ConfigEntryAuthFailed=ConfigEntryAuthFailed,
            ConfigEntryNotReady=ConfigEntryNotReady,
            HomeAssistantError=HomeAssistantError,
        )
    finally:
        for name, module in original.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        for name in list(sys.modules):
            if name.startswith("custom_components.pantryos"):
                sys.modules.pop(name, None)


def import_pantryos_component(name: str):
    module_name = "custom_components.pantryos" if name == "__init__" else f"custom_components.pantryos.{name.removesuffix('.py')}"
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


class FakeServices:
    def __init__(self) -> None:
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
    def __init__(self) -> None:
        self.events = []
        self.last_event_data = {}

    def async_fire(self, event_type, event_data=None):
        self.events.append(event_type)
        self.last_event_data = event_data or {}


class FakeConfigEntries:
    def __init__(self) -> None:
        self.forwarded = []
        self.unloaded = []

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append((entry.entry_id, tuple(platforms)))

    async def async_unload_platforms(self, entry, platforms):
        self.unloaded.append((entry.entry_id, tuple(platforms)))
        return True


class FakeHass:
    def __init__(self) -> None:
        self.data = {}
        self.services = FakeServices()
        self.bus = FakeBus()
        self.config_entries = FakeConfigEntries()
        self.tasks = []

    def async_create_task(self, coroutine):
        task = FakeTask(coroutine)
        self.tasks.append(task)
        return task


class FakeTask:
    def __init__(self, coroutine) -> None:
        self.coroutine = coroutine
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        self.coroutine.close()


class FakeEntry:
    def __init__(self, data=None, entry_id="entry-1") -> None:
        self.data = data or {"base_url": "http://pantry.local:8765", "api_token": "good-token"}
        self.entry_id = entry_id
        self.runtime_data = None


class FakeServiceCall:
    def __init__(self, data) -> None:
        self.data = data


def test_home_assistant_setup_service_runtime_unload_and_auth_recovery_paths() -> None:
    with fake_homeassistant_modules() as fake_ha:
        module = import_pantryos_component("__init__")

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
                    raise module.PantryAPIAuthError("invalid token", status=401, code="unauthorized")
                return {"instance_id": "pantry-instance", "schema_version": 4}

            async def async_refresh(self):
                self.refresh_count += 1
                self.available = True
                return {
                    "revision": self.refresh_count,
                    "summary": {"total_items": self.refresh_count, "state_revision": self.refresh_count},
                }

            async def async_events(self, *, limit=25, after_revision=None):
                return {
                    "items": [{"id": "evt-1", "event_type": "cooking.started", "revision": self.refresh_count + 1}],
                    "revision": self.refresh_count + 1,
                }

            async def async_add_item(self, data):
                self.added_items.append(data)
                return {"revision": self.refresh_count + 1, "item": {"id": "lot-1", **data}}

            async def async_open_item(self, item_id, *, opened_at=None):
                self.opened_items.append({"item_id": item_id, "opened_at": opened_at})
                return {"revision": self.refresh_count + 1, "opened": True}

        module.PantryAPIClient = FakeClient
        hass = FakeHass()
        entry = FakeEntry()

        async def scenario() -> None:
            assert await module.async_setup_entry(hass, entry) is True
            runtime = hass.data[module.DOMAIN][entry.entry_id]
            assert entry.runtime_data is runtime
            assert runtime.instance["instance_id"] == "pantry-instance"
            assert runtime.coordinator.available is True
            assert hass.config_entries.forwarded == [(entry.entry_id, tuple(module.PLATFORMS))]
            assert hass.services.has_service(module.DOMAIN, "add_item")

            handler = hass.services.handlers[(module.DOMAIN, "add_item")]["handler"]
            await handler(FakeServiceCall({"name": "Runtime Oats", "quantity": "1", "unit": "count"}))
            assert runtime.client.added_items == [{"name": "Runtime Oats", "quantity": "1", "unit": "count"}]

            open_handler = hass.services.handlers[(module.DOMAIN, "open_item")]["handler"]
            await open_handler(FakeServiceCall({"item_id": "lot-1", "opened_at": "2026-08-26"}))
            assert runtime.client.opened_items == [{"item_id": "lot-1", "opened_at": "2026-08-26"}]
            assert runtime.coordinator.last_revision >= 2
            assert hass.bus.events[-1] == f"{module.DOMAIN}_updated"

            assert runtime.stream_task is hass.tasks[0]

            assert await module.async_unload_entry(hass, entry) is True
            assert hass.tasks[0].cancelled is True
            assert (module.DOMAIN, "add_item") in hass.services.removed
            assert entry.entry_id not in hass.data.get(module.DOMAIN, {})

            bad_entry = FakeEntry({"base_url": "http://pantry.local:8765", "api_token": "bad-token"}, entry_id="bad")
            try:
                await module.async_setup_entry(FakeHass(), bad_entry)
            except fake_ha.ConfigEntryAuthFailed:
                pass
            else:
                raise AssertionError("auth failures should raise ConfigEntryAuthFailed")

        asyncio.run(scenario())


def test_home_assistant_config_flow_reconfigure_and_reauth_update_existing_entry() -> None:
    with fake_homeassistant_modules():
        module = import_pantryos_component("config_flow")

        class FakeClient:
            def __init__(self, base_url, token):
                self.base_url = base_url
                self.token = token

            async def async_instance(self):
                if self.token == "bad-token":
                    raise module.PantryAPIAuthError("invalid token", status=401, code="unauthorized")
                if self.base_url == "http://offline.local":
                    raise module.PantryAPIError("offline")
                return {"instance_id": "pantry-instance"}

        module.PantryAPIClient = FakeClient

        async def scenario() -> None:
            flow = module.PantryOSConfigFlow()
            flow.reconfigure_entry = SimpleNamespace(data={"base_url": "http://old.local", "api_token": "old-token"})
            shown = await flow.async_step_reconfigure()
            assert shown["type"] == "form"
            assert shown["step_id"] == "reconfigure"

            updated = await flow.async_step_reconfigure({"base_url": "http://new.local/", "api_token": "new-token"})
            assert updated["type"] == "abort"
            assert updated["data_updates"] == {"base_url": "http://new.local", "api_token": "new-token"}
            assert flow.reconfigure_entry.data["base_url"] == "http://new.local"
            assert flow.unique_id_mismatch_checked == "wrong_instance"

            flow.reauth_entry = SimpleNamespace(data={"base_url": "http://new.local", "api_token": "expired-token"})
            prompt = await flow.async_step_reauth(flow.reauth_entry.data)
            assert prompt["type"] == "form"
            assert prompt["step_id"] == "reauth_confirm"

            reauthed = await flow.async_step_reauth_confirm({"api_token": "rotated-token"})
            assert reauthed["type"] == "abort"
            assert reauthed["data_updates"] == {"base_url": "http://new.local", "api_token": "rotated-token"}
            assert flow.reauth_entry.data["api_token"] == "rotated-token"

            invalid = await flow.async_step_reauth_confirm({"api_token": "bad-token"})
            assert invalid["type"] == "form"
            assert invalid["errors"] == {"base": "invalid_auth"}

        asyncio.run(scenario())


def test_home_assistant_live_core_smoke_uses_real_core_api_and_release_docs() -> None:
    script = (ROOT / "scripts" / "ha_core_live_smoke.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    release = (ROOT / "scripts" / "release_readiness.py").read_text(encoding="utf-8")

    assert "async_setup_hass" in script
    assert "hass.config_entries.flow.async_init" in script
    assert "config_flow_result_type" in script
    assert "entry.unique_id" in script
    assert "ConfigEntry(" not in script
    assert "hass.config_entries.async_setup" in script
    assert "hass.services.async_call" in script
    assert "hass.bus.async_listen" in script
    assert "runtime.client.async_add_item" in script
    assert "PANTRYOS_BASE_URL" in script
    assert "expected_state_revision_state" in script
    assert "expected_total_items_state" in script
    assert "matching_sensor_states" in script
    assert "waiting for HA sensor entity states" in script
    assert "SMOKE_TOKEN" in script
    assert "PANTRYOS_API_TOKEN" in script
    assert 'ha_env["PANTRYOS_BASE_URL"] = base_url' in script
    assert 'ha_env["PANTRYOS_API_TOKEN"]' not in script
    assert '"--env",\n            "PANTRYOS_BASE_URL"' in script
    assert '"--env",\n            "PANTRYOS_API_TOKEN"' in script
    assert "FakeClient" not in script
    assert "python scripts/ha_core_live_smoke.py" in readme
    assert "config-flow manager" in readme
    assert "your running PantryOS database is not mutated" in readme
    assert "python scripts/ha_core_live_smoke.py" in release
