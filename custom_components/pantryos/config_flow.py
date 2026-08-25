"""Config flow for PantryOS."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries

from .const import DOMAIN


class PantryOSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a PantryOS config flow."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Create the single local PantryOS config entry."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title="PantryOS", data={})

        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

