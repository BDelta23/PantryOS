"""Coordinator cache for the PantryOS Home Assistant integration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from inspect import isawaitable
from typing import Any


DEFAULT_SUMMARY: dict[str, Any] = {
    "total_items": 0,
    "state_revision": 0,
    "leftover_count": 0,
    "expiring_soon": [],
    "expiring_soon_count": 0,
    "shopping_list_count": 0,
    "suggested_purchases": [],
    "suggested_purchase_count": 0,
    "possible_meals": [],
    "possible_meal_count": 0,
    "food_waste_this_month": "0.00",
    "location_counts": {"Kitchen": 0, "Refrigerator": 0, "Freezer": 0, "Pantry": 0},
    "location_values": {"Kitchen": "0.00", "Refrigerator": "0.00", "Freezer": "0.00", "Pantry": "0.00"},
    "locations": [],
}


@dataclass(slots=True)
class PantryDataCoordinator:
    """Owns the cached PantryOS dashboard state used by HA entities."""

    client: Any
    data: dict[str, Any] | None = None
    available: bool = False
    last_revision: int = 0
    last_event_revision: int = 0
    last_events: list[dict[str, Any]] = field(default_factory=list)
    last_successful_update: str | None = None
    last_error: str | None = None

    async def async_refresh(self) -> dict[str, Any]:
        """Fetch a dashboard snapshot and update availability state."""
        try:
            dashboard = await self.client.async_refresh()
        except Exception as exc:
            self.available = False
            self.last_error = str(exc)
            if hasattr(self.client, "available"):
                self.client.available = False
            raise

        self.data = dashboard
        self.available = True
        self.last_error = None
        self.last_successful_update = datetime.now(UTC).isoformat()
        self.last_revision = _revision_from_payload(dashboard) or self.last_revision
        self.last_event_revision = max(self.last_event_revision, self.last_revision)
        return dashboard

    async def async_refresh_from_events(self, *, limit: int = 25) -> bool:
        """Refresh when the PantryOS event audit shows a newer revision."""
        events = await self.client.async_events(limit=limit, after_revision=self.last_event_revision)
        return await self._refresh_from_event_payload(events)

    async def async_refresh_from_event_stream(self, *, timeout_seconds: float = 25, heartbeat_seconds: float = 10) -> bool:
        """Refresh when the bounded PantryOS SSE stream yields a newer revision."""
        stream = getattr(self.client, "async_event_stream", None)
        if stream is None:
            return await self.async_refresh_from_events()
        events = await stream(
            after_revision=self.last_event_revision,
            timeout_seconds=timeout_seconds,
            heartbeat_seconds=heartbeat_seconds,
        )
        return await self._refresh_from_event_payload(events)

    async def async_listen_for_events(
        self,
        on_change: Callable[[], Any],
        *,
        timeout_seconds: float = 300,
        heartbeat_seconds: float = 15,
        reconnect_seconds: float = 0.2,
        retry_seconds: float = 5,
    ) -> None:
        """Continuously subscribe to Core events and refresh the cache on change."""
        while True:
            try:
                changed = await self.async_refresh_from_event_stream(
                    timeout_seconds=timeout_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.available = False
                self.last_error = str(exc)
                if hasattr(self.client, "available"):
                    self.client.available = False
                try:
                    changed = await self.async_refresh_from_events()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await asyncio.sleep(retry_seconds)
                    continue

            if changed:
                result = on_change()
                if isawaitable(result):
                    await result
            await asyncio.sleep(reconnect_seconds)

    async def _refresh_from_event_payload(self, events: dict[str, Any]) -> bool:
        self.last_events = _event_summaries(events)
        event_revision = _revision_from_events(events)
        if event_revision is None:
            reported_revision = _revision_from_payload(events)
            self.last_event_revision = max(self.last_event_revision, reported_revision or 0)
            if reported_revision is not None and reported_revision > self.last_revision:
                await self.async_refresh()
                return True
            return False

        self.last_event_revision = max(self.last_event_revision, event_revision)
        if event_revision <= self.last_revision:
            return False

        await self.async_refresh()
        return True

    async def async_call_and_refresh(self, operation: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        """Run a mutation and fetch the resulting dashboard snapshot."""
        result = await operation()
        result_revision = _revision_from_payload(result)
        if result_revision is not None:
            self.last_event_revision = max(self.last_event_revision, result_revision)
        await self.async_refresh()
        return result

    def summary(self) -> dict[str, Any]:
        """Return the current dashboard summary without network I/O."""
        if not isinstance(self.data, dict):
            return DEFAULT_SUMMARY
        summary = self.data.get("summary")
        if not isinstance(summary, dict):
            return DEFAULT_SUMMARY
        return summary


@dataclass(slots=True)
class PantryRuntime:
    """Runtime state stored on a Home Assistant config entry."""

    client: Any
    coordinator: PantryDataCoordinator
    instance: dict[str, Any] = field(default_factory=dict)
    unsubscribers: list[Callable[[], None]] = field(default_factory=list)
    stream_task: Any | None = None


def _event_summaries(payload: dict[str, Any], *, limit: int = 10) -> list[dict[str, Any]]:
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in items[-limit:]:
        if not isinstance(item, dict):
            continue
        event_type = item.get("event_type") or item.get("type")
        revision = item.get("revision")
        if not event_type or revision is None:
            continue
        try:
            revision_number = int(revision)
        except (TypeError, ValueError):
            continue
        summary: dict[str, Any] = {"event_type": str(event_type), "revision": revision_number}
        for key in ("id", "product_id", "lot_id", "quantity", "unit"):
            value = item.get(key)
            if value not in (None, ""):
                summary[key] = str(value)
        summaries.append(summary)
    return summaries


def _revision_from_events(payload: dict[str, Any]) -> int | None:
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return None
    revisions = [item.get("revision") for item in items if isinstance(item, dict)]
    numeric = [int(value) for value in revisions if isinstance(value, int | str) and str(value).isdigit()]
    return max(numeric) if numeric else None


def _revision_from_payload(payload: dict[str, Any]) -> int | None:
    for key in ("revision", "state_revision"):
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if isinstance(summary, dict):
        value = summary.get("state_revision")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None
