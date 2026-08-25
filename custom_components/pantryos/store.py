"""Home Assistant storage adapter for PantryOS."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION
from .inventory import InventoryManager


class PantryStore:
    """Persists PantryOS inventory state in Home Assistant storage."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.manager = InventoryManager()

    async def async_load(self) -> None:
        data = await self._store.async_load()
        self.manager = InventoryManager.from_dict(data)

    async def async_save(self) -> None:
        await self._store.async_save(self.manager.to_dict())
