"""OpenAPI contract for the PantryOS v1 HTTP API."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

OPENAPI_VERSION = "3.1.0"
API_VERSION = "v1"
API_PATHS: tuple[str, ...] = (
    "/api/v1/health/live",
    "/api/v1/health/ready",
    "/api/v1/openapi.json",
    "/api/v1/instance",
    "/api/v1/dashboard",
    "/api/v1/events",
    "/api/v1/events/{id}",
    "/api/v1/inventory/events",
    "/api/v1/barcodes/{barcode}",
    "/api/v1/locations/summary",
    "/api/v1/waste/monthly",
    "/api/v1/purchases",
    "/api/v1/purchases/{id}",
    "/api/v1/products/{id}",
    "/api/v1/products/{id}/prices",
    "/api/v1/inventory/lots",
    "/api/v1/inventory/lots/{id}/consume",
    "/api/v1/inventory/lots/{id}/move",
    "/api/v1/inventory/lots/{id}/discard",
    "/api/v1/barcodes/mappings",
    "/api/v1/barcodes/{barcode}/add-lot",
    "/api/v1/receipts",
    "/api/v1/receipts/{id}/extract",
    "/api/v1/receipts/{id}/review",
    "/api/v1/receipts/{id}/commit",
    "/api/v1/receipts/{id}/reject",
    "/api/v1/recipes",
    "/api/v1/recipes/{id}",
    "/api/v1/recipes/{recipe_name}/shopping",
    "/api/v1/meal-plan",
    "/api/v1/shopping/rebuild",
    "/api/v1/shopping",
    "/api/v1/shopping/manual",
    "/api/v1/shopping/{id}",
    "/api/v1/shopping/{id}/check",
    "/api/v1/shopping/{id}/uncheck",
    "/api/v1/shopping/complete-purchase",
    "/api/v1/shopping/promote-suggestions",
    "/api/v1/cooking/sessions",
    "/api/v1/cooking/sessions/{id}",
    "/api/v1/cooking/sessions/{id}/complete",
    "/api/v1/cooking/sessions/{id}/cancel",
    "/api/v1/leftovers",
)

READ_ONLY_METHODS: dict[str, tuple[str, str, str]] = {
    "/api/v1/health/live": ("System", "Process liveness", "Health"),
    "/api/v1/health/ready": ("System", "Database readiness", "Health"),
    "/api/v1/openapi.json": ("System", "OpenAPI document", "OpenAPI"),
    "/api/v1/instance": ("System", "Instance metadata", "ObjectEnvelope"),
    "/api/v1/dashboard": ("Dashboard", "Kitchen dashboard snapshot", "ObjectEnvelope"),
    "/api/v1/events": ("Events", "Server-sent event replay stream", "EventStream"),
    "/api/v1/events/{id}": ("Events", "Inventory event detail", "ObjectEnvelope"),
    "/api/v1/inventory/events": ("Events", "Recent inventory events", "ObjectEnvelope"),
    "/api/v1/barcodes/{barcode}": ("Barcodes", "Resolve barcode mapping", "ObjectEnvelope"),
    "/api/v1/locations/summary": ("Locations", "Location counts and inventory value", "ObjectEnvelope"),
    "/api/v1/waste/monthly": ("Waste", "Current monthly food waste value", "ObjectEnvelope"),
    "/api/v1/purchases": ("Purchases", "Purchase history list", "ObjectEnvelope"),
    "/api/v1/purchases/{id}": ("Purchases", "Purchase detail", "ObjectEnvelope"),
    "/api/v1/products/{id}/prices": ("Prices", "Product price history", "ObjectEnvelope"),
    "/api/v1/shopping": ("Shopping", "Shopping demand list", "ObjectEnvelope"),
    "/api/v1/receipts/{id}/review": ("Receipts", "Receipt review snapshot", "ObjectEnvelope"),
    "/api/v1/cooking/sessions/{id}": ("Cooking", "Cooking session detail", "ObjectEnvelope"),
    "/api/v1/leftovers": ("Leftovers", "Active leftovers", "ObjectEnvelope"),
}

POST_METHODS: dict[str, tuple[str, str, str]] = {
    "/api/v1/inventory/lots": ("Inventory", "Create inventory lot", "LotCreate"),
    "/api/v1/inventory/lots/{id}/consume": ("Inventory", "Consume an inventory lot via FEFO product consumption", "ConsumeRequest"),
    "/api/v1/inventory/lots/{id}/move": ("Inventory", "Move an inventory lot", "MoveRequest"),
    "/api/v1/inventory/lots/{id}/discard": ("Inventory", "Discard a lot and record waste", "DiscardRequest"),
    "/api/v1/barcodes/mappings": ("Barcodes", "Create barcode mapping", "BarcodeMappingRequest"),
    "/api/v1/barcodes/{barcode}/add-lot": ("Barcodes", "Add lot from barcode mapping", "ObjectEnvelope"),
    "/api/v1/receipts": ("Receipts", "Upload reviewed text receipt source", "ReceiptUploadRequest"),
    "/api/v1/receipts/{id}/extract": ("Receipts", "Extract uploaded receipt text for review", "ObjectEnvelope"),
    "/api/v1/receipts/{id}/commit": ("Receipts", "Commit reviewed receipt transactionally", "ObjectEnvelope"),
    "/api/v1/receipts/{id}/reject": ("Receipts", "Reject receipt upload", "RejectReceiptRequest"),
    "/api/v1/recipes": ("Recipes", "Create or replace recipe", "RecipeRequest"),
    "/api/v1/recipes/{recipe_name}/shopping": ("Recipes", "Add missing recipe ingredients to shopping", "ObjectEnvelope"),
    "/api/v1/meal-plan": ("Meal plan", "Plan a recipe for a meal slot", "MealPlanRequest"),
    "/api/v1/shopping/rebuild": ("Shopping", "Rebuild generated shopping demand", "ObjectEnvelope"),
    "/api/v1/shopping/manual": ("Shopping", "Create manual shopping demand", "ShoppingRequest"),
    "/api/v1/shopping/{id}/check": ("Shopping", "Mark shopping item checked", "ObjectEnvelope"),
    "/api/v1/shopping/{id}/uncheck": ("Shopping", "Mark shopping item unchecked", "ObjectEnvelope"),
    "/api/v1/shopping/complete-purchase": ("Shopping", "Complete purchase into inventory lots", "PurchaseRequest"),
    "/api/v1/shopping/promote-suggestions": ("Shopping", "Promote minimum-stock suggestions", "ObjectEnvelope"),
    "/api/v1/cooking/sessions": ("Cooking", "Start cooking session", "CookingStartRequest"),
    "/api/v1/cooking/sessions/{id}/complete": ("Cooking", "Complete cooking session transactionally", "CookingCompleteRequest"),
    "/api/v1/cooking/sessions/{id}/cancel": ("Cooking", "Cancel cooking session", "ObjectEnvelope"),
}

PATCH_METHODS: dict[str, tuple[str, str, str]] = {
    "/api/v1/products/{id}": ("Products", "Update product settings", "ProductUpdateRequest"),
    "/api/v1/receipts/{id}/review": ("Receipts", "Update editable receipt review", "ReceiptReviewRequest"),
    "/api/v1/recipes/{id}": ("Recipes", "Update recipe", "RecipeRequest"),
    "/api/v1/shopping/{id}": ("Shopping", "Update shopping item", "ShoppingUpdateRequest"),
}

DELETE_METHODS: dict[str, tuple[str, str]] = {
    "/api/v1/recipes/{id}": ("Recipes", "Retire recipe"),
    "/api/v1/shopping/{id}": ("Shopping", "Remove shopping item"),
}

PUBLIC_PATHS = {"/api/v1/health/live", "/api/v1/health/ready"}
SSE_PATHS = {"/api/v1/events"}
CREATED_PATHS = {
    "/api/v1/inventory/lots",
    "/api/v1/barcodes/mappings",
    "/api/v1/barcodes/{barcode}/add-lot",
    "/api/v1/receipts",
    "/api/v1/receipts/{id}/commit",
    "/api/v1/recipes",
    "/api/v1/shopping/manual",
    "/api/v1/shopping/complete-purchase",
    "/api/v1/cooking/sessions",
}


def openapi_document() -> dict[str, Any]:
    """Return the current PantryOS v1 OpenAPI document."""

    paths: dict[str, Any] = {}
    for path, (tag, summary, schema_name) in READ_ONLY_METHODS.items():
        paths.setdefault(path, {})["get"] = _operation(tag, summary, response_schema=schema_name, path=path)
    for path, (tag, summary, schema_name) in POST_METHODS.items():
        paths.setdefault(path, {})["post"] = _operation(tag, summary, request_schema=schema_name, path=path)
    for path, (tag, summary, schema_name) in PATCH_METHODS.items():
        paths.setdefault(path, {})["patch"] = _operation(tag, summary, request_schema=schema_name, path=path)
    for path, (tag, summary) in DELETE_METHODS.items():
        paths.setdefault(path, {})["delete"] = _operation(tag, summary, path=path)

    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": "PantryOS Core API",
            "version": API_VERSION,
            "description": "Authenticated local-first PantryOS Core API. Browser session endpoints are intentionally not complete yet.",
        },
        "servers": [{"url": "http://127.0.0.1:8765", "description": "Local PantryOS Core"}],
        "security": [{"bearerAuth": []}],
        "paths": dict(sorted(paths.items())),
        "components": _components(),
    }


def _operation(
    tag: str,
    summary: str,
    *,
    path: str,
    request_schema: str | None = None,
    response_schema: str = "ObjectEnvelope",
) -> dict[str, Any]:
    operation: dict[str, Any] = {
        "tags": [tag],
        "summary": summary,
        "operationId": _operation_id(summary),
        "responses": _responses(response_schema, sse=path in SSE_PATHS, created=path in CREATED_PATHS),
    }
    if path in PUBLIC_PATHS:
        operation["security"] = []
    if request_schema is not None and request_schema != "ObjectEnvelope":
        operation["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{request_schema}"},
                }
            },
        }
    operation["parameters"] = _parameters_for(path)
    return operation


def _operation_id(summary: str) -> str:
    words = "".join(char if char.isalnum() else " " for char in summary).split()
    if not words:
        return "pantryOperation"
    return words[0].casefold() + "".join(word[:1].upper() + word[1:] for word in words[1:])


def _parameters_for(path: str) -> list[dict[str, Any]]:
    parameters = []
    if "{id}" in path:
        parameters.append(_path_parameter("id", "Resource identifier."))
    if "{barcode}" in path:
        parameters.append(_path_parameter("barcode", "Barcode value."))
    if "{recipe_name}" in path:
        parameters.append(_path_parameter("recipe_name", "Recipe name."))
    if path == "/api/v1/inventory/events":
        parameters.extend(
            [
                {"name": "limit", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1, "maximum": 100}},
                {"name": "after_revision", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 0}},
            ]
        )
    return parameters


def _path_parameter(name: str, description: str) -> dict[str, Any]:
    return {"name": name, "in": "path", "required": True, "description": description, "schema": {"type": "string"}}


def _responses(schema_name: str, *, sse: bool = False, created: bool = False) -> dict[str, Any]:
    if sse:
        success = {"description": "SSE hello, replay, and heartbeat stream.", "content": {"text/event-stream": {"schema": {"type": "string"}}}}
    else:
        success = {"description": "Successful response.", "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}}}
    responses = {"201" if created else "200": success}
    responses.update(_problem_responses())
    return responses


def _problem_responses() -> dict[str, Any]:
    problem = {"description": "Stable PantryOS problem response.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Problem"}}}}
    return {status: deepcopy(problem) for status in ("400", "401", "404", "409", "413", "415", "429", "503")}


def _components() -> dict[str, Any]:
    return {
        "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
        "schemas": {
            "Problem": {
                "type": "object",
                "required": ["type", "title", "status", "code", "detail", "errors", "request_id"],
                "properties": {
                    "type": {"type": "string", "format": "uri"},
                    "title": {"type": "string"},
                    "status": {"type": "integer"},
                    "code": {"type": "string"},
                    "detail": {"type": "string"},
                    "errors": {"type": "array", "items": {"type": "object"}},
                    "request_id": {"type": "string"},
                },
                "additionalProperties": True,
            },
            "Health": {"type": "object", "required": ["status"], "properties": {"status": {"type": "string"}}},
            "OpenAPI": {"type": "object"},
            "ObjectEnvelope": {"type": "object", "additionalProperties": True},
            "LotCreate": _object_schema(["name", "quantity"], name="string", quantity="string", unit="string", location="string", expires="string", minimum_stock="string", estimated_cost="string", barcode="string"),
            "ConsumeRequest": _object_schema(["quantity"], quantity="string", reason="string"),
            "MoveRequest": _object_schema(["location"], location="string"),
            "DiscardRequest": _object_schema(["reason"], reason="string"),
            "BarcodeMappingRequest": _object_schema(["barcode", "name"], barcode="string", name="string", package_quantity="string", package_unit="string", brand="string", size_text="string"),
            "ReceiptUploadRequest": _object_schema(["filename", "mime_type", "text"], filename="string", mime_type="string", text="string", content="string"),
            "RejectReceiptRequest": _object_schema([], reason="string"),
            "ReceiptReviewRequest": {"type": "object", "additionalProperties": True},
            "ProductUpdateRequest": _object_schema([], category="string", default_unit="string", minimum_stock_quantity="string", minimum_stock_unit="string", preferred_location="string", default_shelf_life_days="integer", opened_shelf_life_days="integer"),
            "RecipeRequest": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}, "prep_minutes": {"type": "integer"}, "instructions": {"type": "string"}, "ingredients": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}, "additionalProperties": True},
            "MealPlanRequest": _object_schema(["day", "recipe_name"], day="string", recipe_name="string"),
            "ShoppingRequest": _object_schema(["name", "quantity"], name="string", quantity="string", unit="string", note="string", store="string", source_key="string"),
            "ShoppingUpdateRequest": _object_schema([], quantity="string", unit="string", note="string", store="string", checked="boolean"),
            "PurchaseRequest": {"type": "object", "required": ["items"], "properties": {"store": {"type": "string"}, "location": {"type": "string"}, "items": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}, "additionalProperties": True},
            "CookingStartRequest": _object_schema([], recipe_id="string", recipe_name="string", planned_servings="string", meal_plan_entry_id="string", notes="string"),
            "CookingCompleteRequest": {"type": "object", "properties": {"allocations": {"type": "array", "items": {"type": "object", "additionalProperties": True}}, "leftovers": {"type": "array", "items": {"type": "object", "additionalProperties": True}}, "actual_servings": {"type": "string"}}, "additionalProperties": True},
        },
    }


def _object_schema(required: list[str], **properties: str) -> dict[str, Any]:
    type_map = {"string": {"type": "string"}, "integer": {"type": "integer"}, "boolean": {"type": "boolean"}}
    return {
        "type": "object",
        "required": required,
        "properties": {name: type_map[kind] for name, kind in properties.items()},
        "additionalProperties": True,
    }