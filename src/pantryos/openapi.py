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
    "/api/v1/locations",
    "/api/v1/locations/{id}",
    "/api/v1/locations/summary",
    "/api/v1/waste/monthly",
    "/api/v1/purchases",
    "/api/v1/purchases/{id}",
    "/api/v1/products/{id}",
    "/api/v1/products/{id}/prices",
    "/api/v1/inventory/lots",
    "/api/v1/inventory/lots/{id}/consume",
    "/api/v1/inventory/lots/{id}/move",
    "/api/v1/inventory/lots/{id}/open",
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
    "/api/v1/instance": ("System", "Instance metadata", "InstanceResponse"),
    "/api/v1/dashboard": ("Dashboard", "Kitchen dashboard snapshot", "DashboardResponse"),
    "/api/v1/events": ("Events", "Server-sent event replay stream", "EventStream"),
    "/api/v1/events/{id}": ("Events", "Inventory event detail", "InventoryEvent"),
    "/api/v1/inventory/events": ("Events", "Recent inventory events", "EventListResponse"),
    "/api/v1/barcodes/{barcode}": ("Barcodes", "Resolve barcode mapping", "BarcodeLookupResponse"),
    "/api/v1/locations": ("Locations", "List active hierarchical locations", "LocationListResponse"),
    "/api/v1/locations/summary": ("Locations", "Location counts and inventory value", "LocationSummaryResponse"),
    "/api/v1/waste/monthly": ("Waste", "Current monthly food waste value", "WasteMonthlyResponse"),
    "/api/v1/purchases": ("Purchases", "Purchase history list", "PurchaseListResponse"),
    "/api/v1/purchases/{id}": ("Purchases", "Purchase detail", "PurchaseDetailResponse"),
    "/api/v1/products/{id}/prices": ("Prices", "Product price history", "ProductPriceResponse"),
    "/api/v1/shopping": ("Shopping", "Shopping demand list", "ShoppingListResponse"),
    "/api/v1/receipts/{id}/review": ("Receipts", "Receipt review snapshot", "ReceiptReviewResponse"),
    "/api/v1/cooking/sessions/{id}": ("Cooking", "Cooking session detail", "CookingSessionResponse"),
    "/api/v1/leftovers": ("Leftovers", "Active leftovers", "LeftoversResponse"),
}

POST_METHODS: dict[str, tuple[str, str, str]] = {
    "/api/v1/inventory/lots": ("Inventory", "Create inventory lot", "LotCreate"),
    "/api/v1/inventory/lots/{id}/consume": ("Inventory", "Consume an inventory lot via FEFO product consumption", "ConsumeRequest"),
    "/api/v1/inventory/lots/{id}/move": ("Inventory", "Move an inventory lot", "MoveRequest"),
    "/api/v1/inventory/lots/{id}/open": ("Inventory", "Open a lot and apply opened shelf-life policy", "LotOpenRequest"),
    "/api/v1/inventory/lots/{id}/discard": ("Inventory", "Discard a lot and record waste", "DiscardRequest"),
    "/api/v1/barcodes/mappings": ("Barcodes", "Create barcode mapping", "BarcodeMappingRequest"),
    "/api/v1/barcodes/{barcode}/add-lot": ("Barcodes", "Add lot from barcode mapping", "BarcodeAddLotRequest"),
    "/api/v1/receipts": ("Receipts", "Upload reviewed text or image receipt source", "ReceiptUploadRequest"),
    "/api/v1/receipts/{id}/extract": ("Receipts", "Extract uploaded receipt content for review", "EmptyRequest"),
    "/api/v1/receipts/{id}/commit": ("Receipts", "Commit reviewed receipt transactionally", "EmptyRequest"),
    "/api/v1/receipts/{id}/reject": ("Receipts", "Reject receipt upload", "RejectReceiptRequest"),
    "/api/v1/recipes": ("Recipes", "Create or replace recipe", "RecipeRequest"),
    "/api/v1/recipes/{recipe_name}/shopping": ("Recipes", "Add missing recipe ingredients to shopping", "EmptyRequest"),
    "/api/v1/meal-plan": ("Meal plan", "Plan a recipe for a meal slot", "MealPlanRequest"),
    "/api/v1/shopping/rebuild": ("Shopping", "Rebuild generated shopping demand", "EmptyRequest"),
    "/api/v1/shopping/manual": ("Shopping", "Create manual shopping demand", "ShoppingRequest"),
    "/api/v1/shopping/{id}/check": ("Shopping", "Mark shopping item checked", "EmptyRequest"),
    "/api/v1/shopping/{id}/uncheck": ("Shopping", "Mark shopping item unchecked", "EmptyRequest"),
    "/api/v1/shopping/complete-purchase": ("Shopping", "Complete purchase into inventory lots", "PurchaseRequest"),
    "/api/v1/shopping/promote-suggestions": ("Shopping", "Promote minimum-stock suggestions", "EmptyRequest"),
    "/api/v1/cooking/sessions": ("Cooking", "Start cooking session", "CookingStartRequest"),
    "/api/v1/cooking/sessions/{id}/complete": ("Cooking", "Complete cooking session transactionally", "CookingCompleteRequest"),
    "/api/v1/cooking/sessions/{id}/cancel": ("Cooking", "Cancel cooking session", "EmptyRequest"),
}

PATCH_METHODS: dict[str, tuple[str, str, str]] = {
    "/api/v1/products/{id}": ("Products", "Update product settings", "ProductUpdateRequest"),
    "/api/v1/locations/{id}": ("Locations", "Update location name, parent, and metadata", "LocationUpdateRequest"),
    "/api/v1/receipts/{id}/review": ("Receipts", "Update editable receipt review", "ReceiptReviewRequest"),
    "/api/v1/recipes/{id}": ("Recipes", "Update recipe", "RecipeRequest"),
    "/api/v1/shopping/{id}": ("Shopping", "Update shopping item", "ShoppingUpdateRequest"),
}

DELETE_METHODS: dict[str, tuple[str, str]] = {
    "/api/v1/recipes/{id}": ("Recipes", "Retire recipe"),
    "/api/v1/shopping/{id}": ("Shopping", "Remove shopping item"),
}

RESPONSE_SCHEMAS: dict[tuple[str, str], str] = {
    ("post", "/api/v1/inventory/lots"): "InventoryLotResponse",
    ("post", "/api/v1/inventory/lots/{id}/consume"): "ConsumeResponse",
    ("post", "/api/v1/inventory/lots/{id}/move"): "InventoryLotResponse",
    ("post", "/api/v1/inventory/lots/{id}/open"): "LotOpenResponse",
    ("post", "/api/v1/inventory/lots/{id}/discard"): "DiscardResponse",
    ("post", "/api/v1/barcodes/mappings"): "BarcodeMappingResponse",
    ("post", "/api/v1/barcodes/{barcode}/add-lot"): "InventoryLotResponse",
    ("post", "/api/v1/receipts"): "ReceiptUploadResponse",
    ("post", "/api/v1/receipts/{id}/extract"): "ReceiptReviewResponse",
    ("post", "/api/v1/receipts/{id}/commit"): "ReceiptCommitResponse",
    ("post", "/api/v1/receipts/{id}/reject"): "ReceiptUploadResponse",
    ("post", "/api/v1/recipes"): "RecipeResponse",
    ("post", "/api/v1/recipes/{recipe_name}/shopping"): "ShoppingListResponse",
    ("post", "/api/v1/meal-plan"): "MealPlanResponse",
    ("post", "/api/v1/shopping/rebuild"): "ShoppingListResponse",
    ("post", "/api/v1/shopping/manual"): "ShoppingItemResponse",
    ("post", "/api/v1/shopping/{id}/check"): "ShoppingItemResponse",
    ("post", "/api/v1/shopping/{id}/uncheck"): "ShoppingItemResponse",
    ("post", "/api/v1/shopping/complete-purchase"): "PurchaseCompleteResponse",
    ("post", "/api/v1/shopping/promote-suggestions"): "ShoppingListResponse",
    ("post", "/api/v1/cooking/sessions"): "CookingSessionResponse",
    ("post", "/api/v1/cooking/sessions/{id}/complete"): "CookingCompleteResponse",
    ("post", "/api/v1/cooking/sessions/{id}/cancel"): "CookingSessionResponse",
    ("patch", "/api/v1/products/{id}"): "ProductResponse",
    ("patch", "/api/v1/locations/{id}"): "LocationResponse",
    ("patch", "/api/v1/receipts/{id}/review"): "ReceiptReviewResponse",
    ("patch", "/api/v1/recipes/{id}"): "RecipeResponse",
    ("patch", "/api/v1/shopping/{id}"): "ShoppingItemResponse",
    ("delete", "/api/v1/recipes/{id}"): "DeleteResponse",
    ("delete", "/api/v1/shopping/{id}"): "DeleteResponse",
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
        paths.setdefault(path, {})["post"] = _operation(
            tag,
            summary,
            request_schema=schema_name,
            response_schema=RESPONSE_SCHEMAS[("post", path)],
            path=path,
        )
    for path, (tag, summary, schema_name) in PATCH_METHODS.items():
        paths.setdefault(path, {})["patch"] = _operation(
            tag,
            summary,
            request_schema=schema_name,
            response_schema=RESPONSE_SCHEMAS[("patch", path)],
            path=path,
        )
    for path, (tag, summary) in DELETE_METHODS.items():
        paths.setdefault(path, {})["delete"] = _operation(
            tag,
            summary,
            response_schema=RESPONSE_SCHEMAS[("delete", path)],
            path=path,
        )

    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": "PantryOS Core API",
            "version": API_VERSION,
            "description": "Authenticated local-first PantryOS Core API. Browser session endpoints are intentionally not part of this bearer-token API contract.",
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
    if request_schema is not None and request_schema != "EmptyRequest":
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
    if path == "/api/v1/events":
        parameters.extend(
            [
                {"name": "after_revision", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 0}},
                {"name": "timeout", "in": "query", "required": False, "schema": {"type": "number", "minimum": 0, "maximum": 300}},
                {"name": "heartbeat", "in": "query", "required": False, "schema": {"type": "number", "minimum": 0.1}},
                {"name": "Last-Event-ID", "in": "header", "required": False, "schema": {"type": "integer", "minimum": 0}},
            ]
        )
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
        success = {
            "description": "Bounded SSE hello, replay, live revision polling, and heartbeat stream.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    else:
        success = {
            "description": "Successful response.",
            "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}},
        }
    responses = {"201" if created else "200": success}
    responses.update(_problem_responses())
    return responses


def _problem_responses() -> dict[str, Any]:
    problem = {
        "description": "Stable PantryOS problem response.",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Problem"}}},
    }
    return {status: deepcopy(problem) for status in ("400", "401", "404", "409", "413", "415", "429", "503")}


def _components() -> dict[str, Any]:
    schemas: dict[str, Any] = {
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
            "LotCreate": _object_schema(
                ["name", "quantity"],
                name="string",
                quantity="string",
                unit="string",
                location="string",
                expires="string",
                minimum_stock="string",
                estimated_cost="string",
                barcode="string",
            ),
            "ConsumeRequest": _object_schema(["quantity"], quantity="string", reason="string"),
            "MoveRequest": _object_schema(["location"], location="string"),
            "LotOpenRequest": _object_schema([], opened_at="string"),
            "DiscardRequest": _object_schema(["reason"], reason="string"),
            "BarcodeMappingRequest": _object_schema(
                ["barcode", "name"],
                barcode="string",
                name="string",
                package_quantity="string",
                package_unit="string",
                brand="string",
                size_text="string",
            ),
            "ReceiptUploadRequest": _object_schema(
                ["filename", "mime_type"], filename="string", mime_type="string", text="string", content="string", content_base64="string"
            ),
            "RejectReceiptRequest": _object_schema([], reason="string"),
            "ReceiptReviewRequest": {"type": "object", "additionalProperties": True},
            "ProductUpdateRequest": _object_schema(
                [],
                category="string",
                default_unit="string",
                minimum_stock_quantity="string",
                minimum_stock_unit="string",
                preferred_location="string",
                default_shelf_life_days="integer",
                opened_shelf_life_days="integer",
            ),
            "LocationUpdateRequest": _object_schema(
                [], name="string", parent_id="string", parent_path="string", type="string", temperature_entity_id="string"
            ),
            "RecipeRequest": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "prep_minutes": {"type": "integer"},
                    "instructions": {"type": "string"},
                    "ingredients": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                },
                "additionalProperties": True,
            },
            "MealPlanRequest": _object_schema(["day", "recipe_name"], day="string", recipe_name="string"),
            "ShoppingRequest": _object_schema(
                ["name", "quantity"], name="string", quantity="string", unit="string", note="string", store="string", source_key="string"
            ),
            "ShoppingUpdateRequest": _object_schema([], quantity="string", unit="string", note="string", store="string", checked="boolean"),
            "PurchaseRequest": {
                "type": "object",
                "required": ["items"],
                "properties": {
                    "store": {"type": "string"},
                    "location": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                },
                "additionalProperties": True,
            },
            "CookingStartRequest": _object_schema(
                [], recipe_id="string", recipe_name="string", planned_servings="string", meal_plan_entry_id="string", notes="string"
            ),
            "CookingCompleteRequest": {
                "type": "object",
                "properties": {
                    "allocations": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                    "leftovers": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                    "actual_servings": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
    }
    schemas["schemas"].update(_response_schemas())
    return schemas


def _schema(required: list[str], **properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": True,
    }


def _array_ref(schema_name: str) -> dict[str, Any]:
    return {"type": "array", "items": {"$ref": f"#/components/schemas/{schema_name}"}}


def _object_ref(schema_name: str) -> dict[str, Any]:
    return {"$ref": f"#/components/schemas/{schema_name}"}


def _response_schemas() -> dict[str, Any]:
    string = {"type": "string"}
    decimal = {"type": "string", "pattern": r"^-?\d+(\.\d+)?$"}
    integer = {"type": "integer"}
    boolean = {"type": "boolean"}
    revision = {"type": "integer", "minimum": 0}
    date = {"type": "string", "format": "date"}
    date_time = {"type": "string", "format": "date-time"}
    return {
        "InstanceResponse": _schema(
            ["instance_id", "schema_version", "state_revision", "capabilities"],
            instance_id=string,
            schema_version=integer,
            state_revision=revision,
            capabilities={"type": "array", "items": string},
        ),
        "DashboardSummary": _schema(
            ["total_items", "state_revision"],
            total_items=integer,
            expiring_soon=integer,
            shopping_list_count=integer,
            leftover_count=integer,
            state_revision=revision,
            suggested_purchase_count=integer,
            possible_meals=integer,
            food_waste_this_month=decimal,
            location_counts={"type": "object", "additionalProperties": integer},
            location_values={"type": "object", "additionalProperties": decimal},
        ),
        "DashboardResponse": _schema(
            ["revision", "summary"],
            revision=revision,
            summary=_object_ref("DashboardSummary"),
            items=_array_ref("InventoryLot"),
            leftovers=_array_ref("InventoryLot"),
            recipes=_array_ref("Recipe"),
            meal_plan={"type": "object", "additionalProperties": string},
            shopping=_array_ref("ShoppingItem"),
            core=_object_ref("CoreSnapshot"),
        ),
        "CoreSnapshot": _schema(
            [],
            products=_array_ref("Product"),
            locations=_array_ref("Location"),
            events=_array_ref("InventoryEvent"),
            lots=_array_ref("InventoryLot"),
        ),
        "Product": _schema(
            ["id", "name"],
            id=string,
            name=string,
            category=string,
            default_unit=string,
            minimum_stock_quantity=decimal,
            minimum_stock_unit=string,
            preferred_location=string,
            default_shelf_life_days=integer,
            opened_shelf_life_days=integer,
        ),
        "Location": _schema(
            ["id", "name", "path", "type"], id=string, name=string, path=string, parent_id=string, type=string, temperature_entity_id=string
        ),
        "InventoryLot": _schema(
            ["id", "product_id", "name", "quantity", "unit", "location", "status"],
            id=string,
            product_id=string,
            name=string,
            quantity=decimal,
            unit=string,
            location=string,
            location_id=string,
            purchased=date,
            expires=date,
            opened=boolean,
            opened_at=date_time,
            status=string,
            estimated_cost=decimal,
            barcode=string,
            version=integer,
        ),
        "InventoryEvent": _schema(
            ["id", "revision"],
            id=string,
            type=string,
            event_type=string,
            revision=revision,
            created_at=date_time,
            product_id=string,
            lot_id=string,
            quantity=decimal,
            unit=string,
            reason=string,
            source=string,
            data={"type": "object", "additionalProperties": True},
        ),
        "EventListResponse": _schema(["items", "revision", "limit"], items=_array_ref("InventoryEvent"), revision=revision, limit=integer),
        "BarcodeMapping": _schema(
            ["barcode", "product_id", "name"],
            barcode=string,
            product_id=string,
            name=string,
            package_quantity=decimal,
            package_unit=string,
            brand=string,
            size_text=string,
        ),
        "BarcodeLookupResponse": _schema(
            ["barcode", "matched"], barcode=string, matched=boolean, mapping=_object_ref("BarcodeMapping"), product=_object_ref("Product")
        ),
        "LocationListResponse": _schema(["items", "revision"], items=_array_ref("Location"), revision=revision),
        "LocationResponse": _schema(["location", "revision"], location=_object_ref("Location"), revision=revision),
        "LocationSummaryResponse": _schema(
            ["items", "revision"],
            items={"type": "array", "items": _schema(["location", "count", "value"], location=string, count=integer, value=decimal)},
            revision=revision,
        ),
        "WasteMonthlyResponse": _schema(
            ["food_waste_this_month", "currency", "revision"], food_waste_this_month=decimal, currency=string, revision=revision
        ),
        "Purchase": _schema(["id", "store", "purchased_at"], id=string, store=string, purchased_at=date_time, total=decimal),
        "PurchaseLine": _schema(
            ["id", "purchase_id", "display_name", "quantity", "unit"],
            id=string,
            purchase_id=string,
            product_id=string,
            display_name=string,
            quantity=decimal,
            unit=string,
            total_cost=decimal,
            comparable_unit=string,
            unit_price=decimal,
        ),
        "PurchaseListResponse": _schema(["items", "revision"], items=_array_ref("Purchase"), revision=revision),
        "PurchaseDetailResponse": _schema(
            ["purchase", "lines", "prices", "revision"],
            purchase=_object_ref("Purchase"),
            lines=_array_ref("PurchaseLine"),
            prices=_array_ref("PricePoint"),
            revision=revision,
        ),
        "PricePoint": _schema(
            ["unit_price", "comparable_unit"],
            unit_price=decimal,
            comparable_unit=string,
            purchased_at=date_time,
            store=string,
            quantity=decimal,
            unit=string,
        ),
        "PriceAnalysis": _schema(
            ["baseline_policy"],
            baseline_policy=string,
            latest={"type": "object", "additionalProperties": True},
            baseline={"type": "object", "additionalProperties": True},
            samples={"type": "array", "items": {"type": "object", "additionalProperties": True}},
        ),
        "ProductPriceResponse": _schema(
            ["product", "prices", "analysis", "revision"],
            product=_object_ref("Product"),
            prices=_array_ref("PricePoint"),
            analysis=_object_ref("PriceAnalysis"),
            revision=revision,
        ),
        "ShoppingItem": _schema(
            ["id", "name", "quantity", "unit", "checked", "source"],
            id=string,
            product_id=string,
            name=string,
            quantity=decimal,
            unit=string,
            checked=boolean,
            note=string,
            store=string,
            source=string,
            source_kind=string,
            source_key=string,
        ),
        "ShoppingListResponse": _schema(["items", "revision"], items=_array_ref("ShoppingItem"), revision=revision),
        "ShoppingItemResponse": _schema(["item", "revision"], item=_object_ref("ShoppingItem"), revision=revision),
        "Receipt": _schema(
            ["id", "filename", "mime_type", "status"],
            id=string,
            filename=string,
            mime_type=string,
            status=string,
            created_at=date_time,
            extracted_at=date_time,
        ),
        "ReceiptLineReview": _schema(
            ["name", "quantity", "unit"], name=string, quantity=decimal, unit=string, total_cost=decimal, barcode=string
        ),
        "ReceiptReview": _schema(["items"], store=string, purchased_at=date_time, location=string, items=_array_ref("ReceiptLineReview")),
        "ReceiptUploadResponse": _schema(["receipt", "revision"], receipt=_object_ref("Receipt"), revision=revision),
        "ReceiptReviewResponse": _schema(
            ["receipt", "review", "revision"], receipt=_object_ref("Receipt"), review=_object_ref("ReceiptReview"), revision=revision
        ),
        "ReceiptCommitResponse": _schema(
            ["receipt", "purchase", "lines", "lots", "duplicate", "revision"],
            receipt=_object_ref("Receipt"),
            purchase=_object_ref("Purchase"),
            lines=_array_ref("PurchaseLine"),
            lots=_array_ref("InventoryLot"),
            prices=_array_ref("PricePoint"),
            duplicate=boolean,
            revision=revision,
        ),
        "RecipeIngredient": _schema(["name", "quantity", "unit"], name=string, product_id=string, quantity=decimal, unit=string),
        "Recipe": _schema(
            ["id", "name", "ingredients"],
            id=string,
            name=string,
            prep_minutes=integer,
            instructions=string,
            active=boolean,
            ingredients=_array_ref("RecipeIngredient"),
        ),
        "RecipeResponse": _schema(["recipe", "revision"], recipe=_object_ref("Recipe"), revision=revision),
        "MealPlanResponse": _schema(["ok", "revision"], ok=boolean, revision=revision),
        "InventoryLotResponse": _schema(["item", "revision"], item=_object_ref("InventoryLot"), revision=revision),
        "ConsumeAllocation": _schema(["lot_id", "quantity", "unit"], lot_id=string, quantity=decimal, unit=string),
        "ConsumeResponse": _schema(["allocations", "revision"], allocations=_array_ref("ConsumeAllocation"), revision=revision),
        "LotOpenResponse": _schema(["item", "opened", "revision"], item=_object_ref("InventoryLot"), opened=boolean, revision=revision),
        "DiscardResponse": _schema(
            ["item", "discarded_value", "revision"], item=_object_ref("InventoryLot"), discarded_value=decimal, revision=revision
        ),
        "BarcodeMappingResponse": _schema(["mapping", "revision"], mapping=_object_ref("BarcodeMapping"), revision=revision),
        "PurchaseCompleteResponse": _schema(
            ["purchase", "lines", "lots", "revision"],
            purchase=_object_ref("Purchase"),
            lines=_array_ref("PurchaseLine"),
            lots=_array_ref("InventoryLot"),
            revision=revision,
        ),
        "CookingSession": _schema(
            ["id", "status"],
            id=string,
            recipe_id=string,
            recipe_name=string,
            status=string,
            planned_servings=decimal,
            actual_servings=decimal,
            started_at=date_time,
            completed_at=date_time,
            notes=string,
        ),
        "CookingSessionResponse": _schema(["session", "revision"], session=_object_ref("CookingSession"), revision=revision),
        "CookingCompleteResponse": _schema(
            ["session", "allocations", "leftovers", "revision"],
            session=_object_ref("CookingSession"),
            allocations=_array_ref("ConsumeAllocation"),
            leftovers=_array_ref("InventoryLot"),
            revision=revision,
        ),
        "LeftoversResponse": _schema(["items", "revision"], items=_array_ref("InventoryLot"), revision=revision),
        "ProductResponse": _schema(["product", "revision"], product=_object_ref("Product"), revision=revision),
        "DeleteResponse": _schema(["ok", "revision"], ok=boolean, revision=revision),
        "BarcodeAddLotRequest": _object_schema(
            [], quantity="string", location="string", expires="string", purchased="string", estimated_cost="string"
        ),
        "EmptyRequest": _schema([]),
    }


def _object_schema(required: list[str], **properties: str) -> dict[str, Any]:
    type_map = {"string": {"type": "string"}, "integer": {"type": "integer"}, "boolean": {"type": "boolean"}}
    return {
        "type": "object",
        "required": required,
        "properties": {name: type_map[kind] for name, kind in properties.items()},
        "additionalProperties": True,
    }
