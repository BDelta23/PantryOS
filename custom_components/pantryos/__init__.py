"""The PantryOS Home Assistant integration."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api_client import PantryAPIClient, PantryAPIError
from .const import CONF_API_TOKEN, CONF_BASE_URL, DOMAIN, PLATFORMS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PantryOS from a config entry."""
    pantry = PantryAPIClient(entry.data[CONF_BASE_URL], entry.data[CONF_API_TOKEN])
    try:
        await pantry.async_refresh()
    except PantryAPIError as exc:
        raise ConfigEntryNotReady(str(exc)) from exc

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = pantry
    _register_services(hass, pantry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload PantryOS."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            for service in SERVICES:
                hass.services.async_remove(DOMAIN, service)
    return unloaded


def _register_services(hass: HomeAssistant, pantry: PantryAPIClient) -> None:
    if hass.services.has_service(DOMAIN, "add_item"):
        return

    async def add_item(call: ServiceCall) -> None:
        try:
            await pantry.async_add_item(dict(call.data))
            await _refresh_entities(hass, pantry)
        except PantryAPIError as exc:
            raise HomeAssistantError(str(exc)) from exc

    async def consume_item(call: ServiceCall) -> None:
        try:
            await pantry.async_consume_item(
                str(call.data["item_id"]),
                str(call.data["quantity"]),
                reason="Home Assistant consume_item service",
            )
            await _refresh_entities(hass, pantry)
        except PantryAPIError as exc:
            raise HomeAssistantError(str(exc)) from exc

    async def delete_item(call: ServiceCall) -> None:
        try:
            await pantry.async_discard_item(
                str(call.data["item_id"]),
                reason="Home Assistant delete_item service",
            )
            await _refresh_entities(hass, pantry)
        except PantryAPIError as exc:
            raise HomeAssistantError(str(exc)) from exc

    async def move_item(call: ServiceCall) -> None:
        try:
            await pantry.async_move_item(str(call.data["item_id"]), str(call.data["location"]))
            await _refresh_entities(hass, pantry)
        except PantryAPIError as exc:
            raise HomeAssistantError(str(exc)) from exc

    async def add_recipe(call: ServiceCall) -> None:
        try:
            await pantry.async_add_recipe(dict(call.data))
            await _refresh_entities(hass, pantry)
        except PantryAPIError as exc:
            raise HomeAssistantError(str(exc)) from exc

    async def plan_meal(call: ServiceCall) -> None:
        try:
            await pantry.async_plan_meal(str(call.data["day"]), str(call.data["recipe_name"]))
            await _refresh_entities(hass, pantry)
        except PantryAPIError as exc:
            raise HomeAssistantError(str(exc)) from exc

    async def add_shopping_item(call: ServiceCall) -> None:
        try:
            await pantry.async_add_shopping_item(
                {
                    "name": str(call.data["name"]),
                    "quantity": str(call.data["quantity"]),
                    "unit": str(call.data.get("unit") or "count"),
                    "source": str(call.data.get("source") or "manual"),
                }
            )
            await _refresh_entities(hass, pantry)
        except PantryAPIError as exc:
            raise HomeAssistantError(str(exc)) from exc

    async def add_missing_to_shopping_list(call: ServiceCall) -> None:
        try:
            await pantry.async_add_missing_to_shopping_list(str(call.data["recipe_name"]))
            await _refresh_entities(hass, pantry)
        except PantryAPIError as exc:
            raise HomeAssistantError(str(exc)) from exc

    async def promote_suggested_purchases(call: ServiceCall) -> None:
        try:
            await pantry.async_promote_suggested_purchases()
            await _refresh_entities(hass, pantry)
        except PantryAPIError as exc:
            raise HomeAssistantError(str(exc)) from exc

    registrations = {
        "add_item": (add_item, ADD_ITEM_SCHEMA),
        "consume_item": (consume_item, CONSUME_ITEM_SCHEMA),
        "delete_item": (delete_item, ITEM_ID_SCHEMA),
        "move_item": (move_item, MOVE_ITEM_SCHEMA),
        "add_recipe": (add_recipe, ADD_RECIPE_SCHEMA),
        "plan_meal": (plan_meal, PLAN_MEAL_SCHEMA),
        "add_shopping_item": (add_shopping_item, ADD_SHOPPING_ITEM_SCHEMA),
        "add_missing_to_shopping_list": (add_missing_to_shopping_list, RECIPE_NAME_SCHEMA),
        "promote_suggested_purchases": (promote_suggested_purchases, None),
    }
    for name, (handler, schema) in registrations.items():
        hass.services.async_register(DOMAIN, name, handler, schema=schema)


async def _refresh_entities(hass: HomeAssistant, pantry: PantryAPIClient) -> None:
    await pantry.async_refresh()
    hass.bus.async_fire(f"{DOMAIN}_updated")


def _number(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise vol.Invalid("Expected a numeric value") from exc


SERVICES = (
    "add_item",
    "consume_item",
    "delete_item",
    "move_item",
    "add_recipe",
    "plan_meal",
    "add_shopping_item",
    "add_missing_to_shopping_list",
    "promote_suggested_purchases",
)

ADD_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required("quantity"): _number,
        vol.Optional("unit", default="count"): cv.string,
        vol.Optional("location", default="Unassigned"): cv.string,
        vol.Optional("purchased"): cv.date,
        vol.Optional("expires"): cv.date,
        vol.Optional("opened", default=False): cv.boolean,
        vol.Optional("minimum_stock"): _number,
        vol.Optional("barcode"): cv.string,
        vol.Optional("estimated_cost"): _number,
        vol.Optional("tags", default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("notes"): cv.string,
    }
)

CONSUME_ITEM_SCHEMA = vol.Schema({vol.Required("item_id"): cv.string, vol.Required("quantity"): _number})
ITEM_ID_SCHEMA = vol.Schema({vol.Required("item_id"): cv.string})
MOVE_ITEM_SCHEMA = vol.Schema({vol.Required("item_id"): cv.string, vol.Required("location"): cv.string})
INGREDIENT_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required("quantity"): _number,
        vol.Optional("unit", default="count"): cv.string,
    }
)
ADD_RECIPE_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required("ingredients"): vol.All(cv.ensure_list, [INGREDIENT_SCHEMA]),
        vol.Optional("prep_minutes"): cv.positive_int,
        vol.Optional("instructions"): cv.string,
        vol.Optional("tags", default=[]): vol.All(cv.ensure_list, [cv.string]),
    }
)
PLAN_MEAL_SCHEMA = vol.Schema({vol.Required("day"): cv.string, vol.Required("recipe_name"): cv.string})
ADD_SHOPPING_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required("quantity"): _number,
        vol.Optional("unit", default="count"): cv.string,
        vol.Optional("source", default="manual"): cv.string,
    }
)
RECIPE_NAME_SCHEMA = vol.Schema({vol.Required("recipe_name"): cv.string})