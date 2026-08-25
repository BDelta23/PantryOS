"""Config flow for PantryOS."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries

from .api_client import PantryAPIAuthError, PantryAPIClient, PantryAPIError
from .const import CONF_API_TOKEN, CONF_BASE_URL, DOMAIN

DEFAULT_BASE_URL = "http://127.0.0.1:8765"


class PantryOSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a PantryOS config flow."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Create a PantryOS config entry for an existing Core API."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = str(user_input[CONF_BASE_URL]).rstrip("/")
            api_token = str(user_input[CONF_API_TOKEN])
            client = PantryAPIClient(base_url, api_token)
            try:
                instance = await client.async_instance()
            except PantryAPIAuthError:
                errors["base"] = "invalid_auth"
            except PantryAPIError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(str(instance["instance_id"]))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="PantryOS",
                    data={CONF_BASE_URL: base_url, CONF_API_TOKEN: api_token},
                )

        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)


DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
        vol.Required(CONF_API_TOKEN): str,
    }
)