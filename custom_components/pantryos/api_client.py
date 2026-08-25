"""Async PantryOS Core API client for Home Assistant."""

from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class PantryAPIError(Exception):
    """PantryOS API request failed."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None) -> None:
        self.status = status
        self.code = code
        super().__init__(message)


class PantryAPIAuthError(PantryAPIError):
    """PantryOS API rejected authentication."""


class PantryAPIClient:
    """Small HTTP client around the PantryOS Core API."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._dashboard: dict[str, Any] | None = None
        self.available = False

    async def async_instance(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/instance", authenticated=True)

    async def async_dashboard(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/dashboard", authenticated=True)

    async def async_refresh(self) -> dict[str, Any]:
        try:
            self._dashboard = await self.async_dashboard()
            self.available = True
            return self._dashboard
        except PantryAPIError:
            self.available = False
            raise

    async def async_add_item(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/inventory/lots", data=data, authenticated=True)

    async def async_consume_item(self, item_id: str, quantity: str, *, reason: str | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {"quantity": quantity}
        if reason:
            data["reason"] = reason
        path = f"/api/v1/inventory/lots/{quote(item_id, safe='')}/consume"
        return await self._request("POST", path, data=data, authenticated=True)

    async def async_discard_item(self, item_id: str, *, reason: str) -> dict[str, Any]:
        path = f"/api/v1/inventory/lots/{quote(item_id, safe='')}/discard"
        return await self._request("POST", path, data={"reason": reason}, authenticated=True)

    async def async_move_item(self, item_id: str, location: str) -> dict[str, Any]:
        path = f"/api/v1/inventory/lots/{quote(item_id, safe='')}/move"
        return await self._request("POST", path, data={"location": location}, authenticated=True)

    async def async_add_recipe(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/recipes", data=data, authenticated=True)

    async def async_plan_meal(self, day: str, recipe_name: str) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/meal-plan", data={"day": day, "recipe_name": recipe_name}, authenticated=True)

    async def async_add_shopping_item(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/shopping/manual", data=data, authenticated=True)

    async def async_add_missing_to_shopping_list(self, recipe_name: str) -> dict[str, Any]:
        path = f"/api/v1/recipes/{quote(recipe_name, safe='')}/shopping"
        return await self._request("POST", path, data={}, authenticated=True)

    async def async_promote_suggested_purchases(self) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/shopping/promote-suggestions", data={}, authenticated=True)

    def summary(self) -> dict[str, Any]:
        if self._dashboard is None:
            return {
                "total_items": 0,
                "expiring_soon": [],
                "expiring_soon_count": 0,
                "shopping_list_count": 0,
                "suggested_purchases": [],
                "suggested_purchase_count": 0,
                "possible_meals": [],
                "possible_meal_count": 0,
                "food_waste_this_month": "0",
                "location_counts": {"Kitchen": 0, "Refrigerator": 0, "Freezer": 0, "Pantry": 0},
            }
        return self._dashboard["summary"]

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        authenticated: bool,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        request = partial(self._request_sync, method, path, data=data, authenticated=authenticated)
        return await loop.run_in_executor(None, request)

    def _request_sync(self, method: str, path: str, *, data: dict[str, Any] | None, authenticated: bool) -> dict[str, Any]:
        body = None if data is None else json.dumps(data).encode("utf-8")
        request = Request(f"{self.base_url}{path}", data=body, method=method)
        request.add_header("Accept", "application/json")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if authenticated:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            message, code = self._problem_from_http_error(exc)
            if exc.code in (401, 403):
                raise PantryAPIAuthError(message, status=exc.code, code=code) from exc
            raise PantryAPIError(message, status=exc.code, code=code) from exc
        except URLError as exc:
            raise PantryAPIError(str(exc.reason)) from exc
        except TimeoutError as exc:
            raise PantryAPIError("Request timed out") from exc
        except json.JSONDecodeError as exc:
            raise PantryAPIError("PantryOS returned invalid JSON") from exc

    @staticmethod
    def _problem_from_http_error(exc: HTTPError) -> tuple[str, str | None]:
        try:
            problem = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return f"PantryOS API returned HTTP {exc.code}", None
        detail = str(problem.get("detail") or problem.get("title") or f"HTTP {exc.code}")
        code = problem.get("code")
        return detail, str(code) if code is not None else None