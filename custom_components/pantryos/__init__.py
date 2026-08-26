"""The PantryOS Home Assistant integration."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval

from .api_client import PantryAPIAuthError, PantryAPIClient, PantryAPIError
from .const import CONF_API_TOKEN, CONF_BASE_URL, DOMAIN, PLATFORMS
from .coordinator import PantryDataCoordinator, PantryRuntime

SCAN_INTERVAL = timedelta(seconds=30)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PantryOS from a config entry."""
    pantry = PantryAPIClient(entry.data[CONF_BASE_URL], entry.data[CONF_API_TOKEN])
    coordinator = PantryDataCoordinator(pantry)
    try:
        instance = await pantry.async_instance()
        await coordinator.async_refresh()
    except PantryAPIAuthError as exc:
        raise ConfigEntryAuthFailed(str(exc)) from exc
    except PantryAPIError as exc:
        raise ConfigEntryNotReady(str(exc)) from exc

    runtime = PantryRuntime(client=pantry, coordinator=coordinator, instance=instance)

    async def _poll_events(now: Any) -> None:
        try:
            changed = await coordinator.async_refresh_from_event_stream()
        except PantryAPIError:
            try:
                changed = await coordinator.async_refresh_from_events()
            except PantryAPIError:
                try:
                    await coordinator.async_refresh()
                except PantryAPIError:
                    changed = True
                else:
                    changed = True
        if changed:
            _signal_entities_updated(hass)

    runtime.unsubscribers.append(async_track_time_interval(hass, _poll_events, SCAN_INTERVAL))
    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    _register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload PantryOS."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime = hass.data[DOMAIN].pop(entry.entry_id)
        for unsubscribe in runtime.unsubscribers:
            unsubscribe()
        if not hass.data[DOMAIN]:
            for service in SERVICES:
                hass.services.async_remove(DOMAIN, service)
    return unloaded


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "add_item"):
        return

    async def _run(call: ServiceCall, operation: Any) -> None:
        runtime = _active_runtime(hass)
        try:
            await runtime.coordinator.async_call_and_refresh(lambda: operation(runtime.client, call))
            _signal_entities_updated(hass)
        except PantryAPIError as exc:
            raise HomeAssistantError(str(exc)) from exc

    async def add_item(call: ServiceCall) -> None:
        await _run(call, lambda pantry, call: pantry.async_add_item(dict(call.data)))

    async def consume_item(call: ServiceCall) -> None:
        await _run(
            call,
            lambda pantry, call: pantry.async_consume_item(
                str(call.data["item_id"]),
                str(call.data["quantity"]),
                reason="Home Assistant consume_item service",
            ),
        )

    async def delete_item(call: ServiceCall) -> None:
        await _run(call, lambda pantry, call: pantry.async_discard_item(str(call.data["item_id"]), reason="Home Assistant delete_item service"))

    async def discard_item(call: ServiceCall) -> None:
        await _run(call, lambda pantry, call: pantry.async_discard_item(str(call.data["item_id"]), reason=str(call.data.get("reason") or "Home Assistant discard_item service")))

    async def move_item(call: ServiceCall) -> None:
        await _run(call, lambda pantry, call: pantry.async_move_item(str(call.data["item_id"]), str(call.data["location"])))

    async def open_item(call: ServiceCall) -> None:
        opened_at = call.data.get("opened_at")
        await _run(call, lambda pantry, call: pantry.async_open_item(str(call.data["item_id"]), opened_at=str(opened_at) if opened_at else None))

    async def add_recipe(call: ServiceCall) -> None:
        await _run(call, lambda pantry, call: pantry.async_add_recipe(dict(call.data)))

    async def plan_meal(call: ServiceCall) -> None:
        await _run(call, lambda pantry, call: pantry.async_plan_meal(str(call.data["day"]), str(call.data["recipe_name"])))

    async def add_shopping_item(call: ServiceCall) -> None:
        await _run(
            call,
            lambda pantry, call: pantry.async_add_shopping_item(
                {
                    "name": str(call.data["name"]),
                    "quantity": str(call.data["quantity"]),
                    "unit": str(call.data.get("unit") or "count"),
                    "source": str(call.data.get("source") or "manual"),
                }
            ),
        )

    async def add_missing_to_shopping_list(call: ServiceCall) -> None:
        await _run(call, lambda pantry, call: pantry.async_add_missing_to_shopping_list(str(call.data["recipe_name"])))

    async def rebuild_shopping(call: ServiceCall) -> None:
        await _run(call, lambda pantry, call: pantry.async_rebuild_shopping())

    async def promote_suggested_purchases(call: ServiceCall) -> None:
        await _run(call, lambda pantry, call: pantry.async_promote_suggested_purchases())

    async def start_cooking(call: ServiceCall) -> None:
        await _run(call, lambda pantry, call: pantry.async_start_cooking_session(dict(call.data)))

    async def complete_cooking(call: ServiceCall) -> None:
        await _run(
            call,
            lambda pantry, call: pantry.async_complete_cooking_session(
                str(call.data["session_id"]),
                {
                    "actual_servings": str(call.data.get("actual_servings")) if call.data.get("actual_servings") is not None else None,
                    "allocations": list(call.data.get("allocations") or []),
                    "leftovers": list(call.data.get("leftovers") or []),
                    "notes": call.data.get("notes"),
                },
            ),
        )

    async def cancel_cooking(call: ServiceCall) -> None:
        await _run(call, lambda pantry, call: pantry.async_cancel_cooking_session(str(call.data["session_id"]), {"reason": call.data.get("reason")}))

    registrations = {
        "add_item": (add_item, ADD_ITEM_SCHEMA),
        "consume_item": (consume_item, CONSUME_ITEM_SCHEMA),
        "delete_item": (delete_item, ITEM_ID_SCHEMA),
        "discard_item": (discard_item, DISCARD_ITEM_SCHEMA),
        "move_item": (move_item, MOVE_ITEM_SCHEMA),
        "open_item": (open_item, OPEN_ITEM_SCHEMA),
        "add_recipe": (add_recipe, ADD_RECIPE_SCHEMA),
        "plan_meal": (plan_meal, PLAN_MEAL_SCHEMA),
        "add_shopping_item": (add_shopping_item, ADD_SHOPPING_ITEM_SCHEMA),
        "add_missing_to_shopping_list": (add_missing_to_shopping_list, RECIPE_NAME_SCHEMA),
        "rebuild_shopping": (rebuild_shopping, None),
        "promote_suggested_purchases": (promote_suggested_purchases, None),
        "start_cooking": (start_cooking, START_COOKING_SCHEMA),
        "complete_cooking": (complete_cooking, COMPLETE_COOKING_SCHEMA),
        "cancel_cooking": (cancel_cooking, CANCEL_COOKING_SCHEMA),
    }
    for name, (handler, schema) in registrations.items():
        hass.services.async_register(DOMAIN, name, handler, schema=schema)


def _active_pantry(hass: HomeAssistant) -> PantryAPIClient:
    return _active_runtime(hass).client


def _active_runtime(hass: HomeAssistant) -> PantryRuntime:
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        raise HomeAssistantError("No PantryOS entry is loaded")
    return next(iter(entries.values()))


async def _refresh_entities(hass: HomeAssistant, coordinator: PantryDataCoordinator) -> None:
    await coordinator.async_refresh()
    _signal_entities_updated(hass)


def _signal_entities_updated(hass: HomeAssistant) -> None:
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
    "discard_item",
    "move_item",
    "open_item",
    "add_recipe",
    "plan_meal",
    "add_shopping_item",
    "add_missing_to_shopping_list",
    "rebuild_shopping",
    "promote_suggested_purchases",
    "start_cooking",
    "complete_cooking",
    "cancel_cooking",
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
DISCARD_ITEM_SCHEMA = vol.Schema({vol.Required("item_id"): cv.string, vol.Optional("reason", default="discarded"): cv.string})
MOVE_ITEM_SCHEMA = vol.Schema({vol.Required("item_id"): cv.string, vol.Required("location"): cv.string})
OPEN_ITEM_SCHEMA = vol.Schema({vol.Required("item_id"): cv.string, vol.Optional("opened_at"): cv.string})
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
START_COOKING_SCHEMA = vol.Schema(
    {
        vol.Optional("recipe_id"): cv.string,
        vol.Optional("recipe_name"): cv.string,
        vol.Optional("planned_servings"): _number,
        vol.Optional("notes"): cv.string,
    }
)
COMPLETE_COOKING_SCHEMA = vol.Schema(
    {
        vol.Required("session_id"): cv.string,
        vol.Optional("actual_servings"): _number,
        vol.Optional("allocations", default=[]): vol.All(cv.ensure_list, [dict]),
        vol.Optional("leftovers", default=[]): vol.All(cv.ensure_list, [dict]),
        vol.Optional("notes"): cv.string,
    }
)
CANCEL_COOKING_SCHEMA = vol.Schema({vol.Required("session_id"): cv.string, vol.Optional("reason", default="cancelled"): cv.string})