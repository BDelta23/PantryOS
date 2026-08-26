"""Diagnostics support for PantryOS."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    from .const import CONF_API_TOKEN, CONF_BASE_URL, DOMAIN
except ImportError:  # pragma: no cover - supports dependency-free contract tests
    CONF_API_TOKEN = "api_token"
    CONF_BASE_URL = "base_url"
    DOMAIN = "pantryos"

REDACTED = "**REDACTED**"
SENSITIVE_KEYS = {
    "authorization",
    "api_token",
    "token",
    "access_token",
    "refresh_token",
    "cookie",
    "set_cookie",
    "session",
    "session_id",
    "receipt_text",
    "receipt_image",
    "receipt_content",
    "content",
    "text",
    "storage_path",
    "raw_path",
}


def sanitize_diagnostics_payload(value: Any) -> Any:
    """Recursively redact secrets, receipt contents, and local file paths."""
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                sanitized[key_text] = REDACTED
            else:
                sanitized[key_text] = sanitize_diagnostics_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_diagnostics_payload(item) for item in value]
    return value


def sanitized_base_url(base_url: str) -> str:
    """Return a diagnostics-safe Core URL with credentials and path removed."""
    parts = urlsplit(base_url)
    hostname = parts.hostname or ""
    netloc = hostname
    if parts.port is not None:
        netloc = f"{hostname}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, "", "", ""))


async def async_get_config_entry_diagnostics(hass: Any, entry: Any) -> dict[str, Any]:
    """Return redacted diagnostics for a PantryOS config entry."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    coordinator = getattr(runtime, "coordinator", None)
    client = getattr(runtime, "client", runtime)
    dashboard = getattr(coordinator, "data", None) if coordinator is not None else getattr(client, "_dashboard", None)
    summary = dashboard.get("summary", {}) if isinstance(dashboard, dict) else {}
    payload = {
        "entry": {
            "entry_id": entry.entry_id,
            "base_url": sanitized_base_url(str(entry.data.get(CONF_BASE_URL, ""))),
            CONF_API_TOKEN: entry.data.get(CONF_API_TOKEN),
        },
        "client": {
            "available": getattr(coordinator, "available", getattr(client, "available", False)),
            "base_url": sanitized_base_url(getattr(client, "base_url", "")) if client is not None else None,
        },
        "coordinator": {
            "available": getattr(coordinator, "available", False),
            "last_revision": getattr(coordinator, "last_revision", None),
            "last_event_revision": getattr(coordinator, "last_event_revision", None),
            "last_successful_update": getattr(coordinator, "last_successful_update", None),
            "last_error": getattr(coordinator, "last_error", None),
        },
        "dashboard": {
            "revision": dashboard.get("revision") if isinstance(dashboard, dict) else None,
            "summary_keys": sorted(summary.keys()),
            "capabilities": dashboard.get("instance", {}).get("capabilities", []) if isinstance(dashboard, dict) else [],
        },
    }
    return sanitize_diagnostics_payload(payload)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return normalized in SENSITIVE_KEYS or normalized.endswith("_token") or "authorization" in normalized
