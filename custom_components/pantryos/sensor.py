"""PantryOS sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .api_client import PantryAPIClient, PantryAPIError


@dataclass(frozen=True, kw_only=True)
class PantrySensorDescription(SensorEntityDescription):
    """Describes a PantryOS summary sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSORS: tuple[PantrySensorDescription, ...] = (
    PantrySensorDescription(key="total_items", translation_key="total_items", value_fn=lambda summary: summary["total_items"]),
    PantrySensorDescription(
        key="expiring_soon",
        translation_key="expiring_soon",
        value_fn=lambda summary: summary["expiring_soon_count"],
        attributes_fn=lambda summary: {"items": summary["expiring_soon"]},
    ),
    PantrySensorDescription(key="shopping_list_count", translation_key="shopping_list_count", value_fn=lambda summary: summary["shopping_list_count"]),
    PantrySensorDescription(
        key="suggested_purchases",
        translation_key="suggested_purchases",
        value_fn=lambda summary: summary["suggested_purchase_count"],
        attributes_fn=lambda summary: {"items": summary["suggested_purchases"]},
    ),
    PantrySensorDescription(
        key="possible_meals",
        translation_key="possible_meals",
        value_fn=lambda summary: summary["possible_meal_count"],
        attributes_fn=lambda summary: {"meals": summary["possible_meals"]},
    ),
    PantrySensorDescription(
        key="food_waste_this_month",
        translation_key="food_waste_this_month",
        native_unit_of_measurement="USD",
        value_fn=lambda summary: summary["food_waste_this_month"],
    ),
    PantrySensorDescription(key="kitchen_items", translation_key="kitchen_items", value_fn=lambda summary: summary["location_counts"]["Kitchen"]),
    PantrySensorDescription(key="refrigerator_items", translation_key="refrigerator_items", value_fn=lambda summary: summary["location_counts"]["Refrigerator"]),
    PantrySensorDescription(key="freezer_items", translation_key="freezer_items", value_fn=lambda summary: summary["location_counts"]["Freezer"]),
    PantrySensorDescription(key="pantry_items", translation_key="pantry_items", value_fn=lambda summary: summary["location_counts"]["Pantry"]),
    PantrySensorDescription(
        key="kitchen_value",
        translation_key="kitchen_value",
        native_unit_of_measurement="USD",
        value_fn=lambda summary: summary["location_values"]["Kitchen"],
    ),
    PantrySensorDescription(
        key="refrigerator_value",
        translation_key="refrigerator_value",
        native_unit_of_measurement="USD",
        value_fn=lambda summary: summary["location_values"]["Refrigerator"],
    ),
    PantrySensorDescription(
        key="freezer_value",
        translation_key="freezer_value",
        native_unit_of_measurement="USD",
        value_fn=lambda summary: summary["location_values"]["Freezer"],
    ),
    PantrySensorDescription(
        key="pantry_value",
        translation_key="pantry_value",
        native_unit_of_measurement="USD",
        value_fn=lambda summary: summary["location_values"]["Pantry"],
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up PantryOS sensor entities."""
    pantry: PantryAPIClient = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(PantrySensor(pantry, description, entry.entry_id) for description in SENSORS)


class PantrySensor(SensorEntity):
    """A derived PantryOS summary sensor."""

    entity_description: PantrySensorDescription
    _attr_has_entity_name = True

    def __init__(self, pantry: PantryAPIClient, description: PantrySensorDescription, entry_id: str) -> None:
        self._pantry = pantry
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._remove_listener: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self.hass.bus.async_listen(f"{DOMAIN}_updated", self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    async def async_update(self) -> None:
        try:
            await self._pantry.async_refresh()
        except PantryAPIError:
            return

    @property
    def available(self) -> bool:
        return self._pantry.available

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._pantry.summary())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self._pantry.summary())

    @callback
    def _handle_update(self, event: Any) -> None:
        self.async_write_ha_state()
