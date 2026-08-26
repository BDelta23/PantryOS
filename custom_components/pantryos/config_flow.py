"""Config flow for PantryOS."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries

from .api_client import PantryAPIAuthError, PantryAPIClient, PantryAPIError
from .const import CONF_API_TOKEN, CONF_BASE_URL, DOMAIN

DEFAULT_BASE_URL = "http://127.0.0.1:8765"


class PantryOSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle a PantryOS config flow."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Create a PantryOS config entry for an existing Core API."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data, instance, errors = await _validated_entry_data(user_input)
            if not errors:
                await self.async_set_unique_id(str(instance["instance_id"]))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="PantryOS", data=data)

        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Update Core URL/token while preserving the PantryOS instance identity."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            data, instance, errors = await _validated_entry_data(user_input)
            if not errors:
                await self.async_set_unique_id(str(instance["instance_id"]))
                self._abort_if_unique_id_mismatch(reason="wrong_instance")
                return self.async_update_reload_and_abort(entry, data_updates=data)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_data_schema(entry.data),
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> config_entries.ConfigFlowResult:
        """Start credential recovery for an existing PantryOS entry."""
        self._reauth_entry_data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Validate replacement credentials and reload the existing entry."""
        errors: dict[str, str] = {}
        entry_data = getattr(self, "_reauth_entry_data", {})

        if user_input is not None:
            merged = {**entry_data, **user_input}
            data, instance, errors = await _validated_entry_data(merged)
            if not errors:
                await self.async_set_unique_id(str(instance["instance_id"]))
                self._abort_if_unique_id_mismatch(reason="wrong_instance")
                return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_data_schema(entry_data),
            errors=errors,
        )


async def _validated_entry_data(user_input: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, Any], dict[str, str]]:
    """Normalize config input and validate it against PantryOS Core."""
    base_url = str(user_input[CONF_BASE_URL]).rstrip("/")
    api_token = str(user_input[CONF_API_TOKEN])
    client = PantryAPIClient(base_url, api_token)
    try:
        instance = await client.async_instance()
    except PantryAPIAuthError:
        return {}, {}, {"base": "invalid_auth"}
    except PantryAPIError:
        return {}, {}, {"base": "cannot_connect"}
    return {CONF_BASE_URL: base_url, CONF_API_TOKEN: api_token}, instance, {}


def _data_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Build a PantryOS config form schema with current entry defaults."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_BASE_URL, default=str(defaults.get(CONF_BASE_URL) or DEFAULT_BASE_URL)): str,
            vol.Required(CONF_API_TOKEN, default=str(defaults.get(CONF_API_TOKEN) or "")): str,
        }
    )


DATA_SCHEMA = _data_schema()
