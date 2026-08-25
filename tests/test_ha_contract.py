from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_component(name: str) -> str:
    return (ROOT / "custom_components" / "pantryos" / name).read_text(encoding="utf-8")


def test_home_assistant_services_sensors_and_translations_cover_current_surface() -> None:
    init_py = read_component("__init__.py")
    sensor_py = read_component("sensor.py")
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

    assert "_active_pantry(hass)" in init_py
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