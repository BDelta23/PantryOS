"""Pure PantryOS inventory and meal-planning logic."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4


def _today() -> date:
    return date.today()


def parse_date(value: str | date | None) -> date | None:
    """Parse an ISO date value."""
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value)


def normalize_name(value: str) -> str:
    """Normalize names for matching."""
    return " ".join(value.casefold().strip().split())


def normalize_location(value: str | None) -> str:
    """Normalize a hierarchical location path."""
    if not value:
        return "Unassigned"
    parts = [part.strip() for part in value.replace("\\", "/").split("/") if part.strip()]
    return "/".join(parts) if parts else "Unassigned"


def decimal_or_zero(value: int | float | str | Decimal | None) -> Decimal:
    """Convert numeric service input to Decimal."""
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal value: {value!r}") from exc


def decimal_to_json(value: Decimal) -> str:
    """Serialize Decimal values as strings to avoid float drift."""
    return format(value.normalize(), "f")


@dataclass(slots=True)
class InventoryItem:
    """A tracked food item."""

    name: str
    quantity: Decimal
    unit: str
    location: str
    id: str = field(default_factory=lambda: uuid4().hex)
    purchased: date | None = None
    expires: date | None = None
    opened: bool = False
    minimum_stock: Decimal | None = None
    barcode: str | None = None
    estimated_cost: Decimal | None = None
    tags: list[str] = field(default_factory=list)
    notes: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InventoryItem:
        return cls(
            id=str(data.get("id") or uuid4().hex),
            name=str(data["name"]).strip(),
            quantity=decimal_or_zero(data.get("quantity")),
            unit=str(data.get("unit") or "count").strip(),
            location=normalize_location(data.get("location")),
            purchased=parse_date(data.get("purchased")),
            expires=parse_date(data.get("expires")),
            opened=bool(data.get("opened", False)),
            minimum_stock=(
                decimal_or_zero(data["minimum_stock"])
                if data.get("minimum_stock") is not None
                else None
            ),
            barcode=str(data["barcode"]).strip() if data.get("barcode") else None,
            estimated_cost=(
                decimal_or_zero(data["estimated_cost"])
                if data.get("estimated_cost") is not None
                else None
            ),
            tags=[str(tag).strip() for tag in data.get("tags", []) if str(tag).strip()],
            notes=str(data["notes"]).strip() if data.get("notes") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["quantity"] = decimal_to_json(self.quantity)
        data["purchased"] = self.purchased.isoformat() if self.purchased else None
        data["expires"] = self.expires.isoformat() if self.expires else None
        data["minimum_stock"] = (
            decimal_to_json(self.minimum_stock) if self.minimum_stock is not None else None
        )
        data["estimated_cost"] = (
            decimal_to_json(self.estimated_cost) if self.estimated_cost is not None else None
        )
        return data

    @property
    def is_leftover(self) -> bool:
        return "leftover" in {tag.casefold() for tag in self.tags}

    def expires_in_days(self, today: date | None = None) -> int | None:
        if self.expires is None:
            return None
        return (self.expires - (today or _today())).days


@dataclass(slots=True)
class RecipeIngredient:
    """A recipe ingredient requirement."""

    name: str
    quantity: Decimal
    unit: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecipeIngredient:
        return cls(
            name=str(data["name"]).strip(),
            quantity=decimal_or_zero(data.get("quantity")),
            unit=str(data.get("unit") or "count").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "quantity": decimal_to_json(self.quantity),
            "unit": self.unit,
        }


@dataclass(slots=True)
class Recipe:
    """A recipe that can be matched against inventory."""

    name: str
    ingredients: list[RecipeIngredient]
    id: str = field(default_factory=lambda: uuid4().hex)
    prep_minutes: int | None = None
    instructions: str | None = None
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Recipe:
        return cls(
            id=str(data.get("id") or uuid4().hex),
            name=str(data["name"]).strip(),
            ingredients=[RecipeIngredient.from_dict(item) for item in data.get("ingredients", [])],
            prep_minutes=(int(data["prep_minutes"]) if data.get("prep_minutes") is not None else None),
            instructions=str(data["instructions"]).strip() if data.get("instructions") else None,
            tags=[str(tag).strip() for tag in data.get("tags", []) if str(tag).strip()],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "ingredients": [ingredient.to_dict() for ingredient in self.ingredients],
            "prep_minutes": self.prep_minutes,
            "instructions": self.instructions,
            "tags": self.tags,
        }


@dataclass(slots=True)
class ShoppingListItem:
    """A shopping list row."""

    name: str
    quantity: Decimal
    unit: str
    source: str = "manual"
    checked: bool = False
    id: str = field(default_factory=lambda: uuid4().hex)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShoppingListItem:
        return cls(
            id=str(data.get("id") or uuid4().hex),
            name=str(data["name"]).strip(),
            quantity=decimal_or_zero(data.get("quantity")),
            unit=str(data.get("unit") or "count").strip(),
            source=str(data.get("source") or "manual").strip(),
            checked=bool(data.get("checked", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "quantity": decimal_to_json(self.quantity),
            "unit": self.unit,
            "source": self.source,
            "checked": self.checked,
        }


@dataclass(slots=True)
class InventoryState:
    """The full PantryOS state document."""

    items: list[InventoryItem] = field(default_factory=list)
    recipes: list[Recipe] = field(default_factory=list)
    shopping_list: list[ShoppingListItem] = field(default_factory=list)
    meal_plan: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> InventoryState:
        if not data:
            return cls()
        return cls(
            items=[InventoryItem.from_dict(item) for item in data.get("items", [])],
            recipes=[Recipe.from_dict(recipe) for recipe in data.get("recipes", [])],
            shopping_list=[ShoppingListItem.from_dict(item) for item in data.get("shopping_list", [])],
            meal_plan={str(day): str(recipe) for day, recipe in data.get("meal_plan", {}).items()},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "recipes": [recipe.to_dict() for recipe in self.recipes],
            "shopping_list": [item.to_dict() for item in self.shopping_list],
            "meal_plan": self.meal_plan,
        }


class InventoryManager:
    """Mutates and summarizes an InventoryState."""

    def __init__(self, state: InventoryState | None = None) -> None:
        self.state = state or InventoryState()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> InventoryManager:
        return cls(InventoryState.from_dict(data))

    def to_dict(self) -> dict[str, Any]:
        return self.state.to_dict()

    def add_item(self, data: dict[str, Any]) -> InventoryItem:
        item = InventoryItem.from_dict(data)
        if not item.name:
            raise ValueError("Item name is required")
        if item.quantity < 0:
            raise ValueError("Quantity cannot be negative")
        self.state.items.append(item)
        return item

    def consume_item(self, item_id: str, quantity: Decimal) -> InventoryItem | None:
        item = self.find_item(item_id)
        if item is None:
            raise KeyError(f"Unknown inventory item: {item_id}")
        if quantity <= 0:
            raise ValueError("Quantity must be greater than zero")
        item.quantity -= quantity
        if item.quantity <= 0:
            if item.minimum_stock is not None:
                item.quantity = Decimal("0")
                return item
            self.state.items = [
                candidate for candidate in self.state.items if candidate.id != item_id
            ]
            return None
        return item

    def delete_item(self, item_id: str) -> None:
        before = len(self.state.items)
        self.state.items = [candidate for candidate in self.state.items if candidate.id != item_id]
        if len(self.state.items) == before:
            raise KeyError(f"Unknown inventory item: {item_id}")

    def move_item(self, item_id: str, location: str) -> InventoryItem:
        item = self.find_item(item_id)
        if item is None:
            raise KeyError(f"Unknown inventory item: {item_id}")
        item.location = normalize_location(location)
        return item

    def add_recipe(self, data: dict[str, Any]) -> Recipe:
        recipe = Recipe.from_dict(data)
        if not recipe.name:
            raise ValueError("Recipe name is required")
        if not recipe.ingredients:
            raise ValueError("Recipe must include at least one ingredient")
        self.state.recipes.append(recipe)
        return recipe

    def plan_meal(self, day: str, recipe_name: str) -> None:
        if self.find_recipe(recipe_name) is None:
            raise KeyError(f"Unknown recipe: {recipe_name}")
        self.state.meal_plan[day] = recipe_name

    def add_shopping_item(self, name: str, quantity: Decimal, unit: str, source: str = "manual") -> ShoppingListItem:
        existing = self._find_open_shopping_item(name, unit, source)
        if existing is not None:
            existing.quantity += quantity
            return existing
        item = ShoppingListItem(name=name, quantity=quantity, unit=unit, source=source)
        self.state.shopping_list.append(item)
        return item

    def add_missing_to_shopping_list(self, recipe_name: str) -> list[ShoppingListItem]:
        recipe = self.find_recipe(recipe_name)
        if recipe is None:
            raise KeyError(f"Unknown recipe: {recipe_name}")
        added: list[ShoppingListItem] = []
        for missing in self.missing_ingredients(recipe):
            added.append(self.add_shopping_item(missing["name"], missing["quantity"], missing["unit"], source=f"recipe:{recipe.name}"))
        return added

    def promote_suggested_purchases(self) -> list[ShoppingListItem]:
        added: list[ShoppingListItem] = []
        for suggestion in self.suggested_purchases():
            added.append(self.add_shopping_item(suggestion["name"], suggestion["quantity"], suggestion["unit"], source="minimum_stock"))
        return added

    def find_item(self, item_id: str) -> InventoryItem | None:
        return next((item for item in self.state.items if item.id == item_id), None)

    def find_recipe(self, recipe_name: str) -> Recipe | None:
        wanted = normalize_name(recipe_name)
        return next((recipe for recipe in self.state.recipes if normalize_name(recipe.name) == wanted), None)

    def inventory_totals(self) -> dict[tuple[str, str], Decimal]:
        totals: dict[tuple[str, str], Decimal] = {}
        for item in self.state.items:
            key = (normalize_name(item.name), item.unit.casefold())
            totals[key] = totals.get(key, Decimal("0")) + item.quantity
        return totals

    def missing_ingredients(self, recipe: Recipe) -> list[dict[str, Any]]:
        totals = self.inventory_totals()
        missing: list[dict[str, Any]] = []
        for ingredient in recipe.ingredients:
            key = (normalize_name(ingredient.name), ingredient.unit.casefold())
            available = totals.get(key, Decimal("0"))
            if available < ingredient.quantity:
                missing.append({"name": ingredient.name, "quantity": ingredient.quantity - available, "unit": ingredient.unit})
        return missing

    def possible_meals(self, max_missing: int = 0, max_minutes: int | None = None) -> list[dict[str, Any]]:
        meals: list[dict[str, Any]] = []
        for recipe in self.state.recipes:
            if max_minutes is not None and recipe.prep_minutes is not None and recipe.prep_minutes > max_minutes:
                continue
            missing = self.missing_ingredients(recipe)
            if len(missing) <= max_missing:
                meals.append(
                    {
                        "name": recipe.name,
                        "prep_minutes": recipe.prep_minutes,
                        "missing_count": len(missing),
                        "missing": [
                            {"name": item["name"], "quantity": decimal_to_json(item["quantity"]), "unit": item["unit"]}
                            for item in missing
                        ],
                    }
                )
        return sorted(meals, key=lambda meal: (meal["missing_count"], meal["prep_minutes"] or 9999, meal["name"]))

    def expiring_soon(self, days: int = 4, today: date | None = None) -> list[dict[str, Any]]:
        today = today or _today()
        rows: list[dict[str, Any]] = []
        for item in self.state.items:
            days_left = item.expires_in_days(today)
            if days_left is not None and 0 <= days_left <= days:
                rows.append(
                    {
                        "id": item.id,
                        "name": item.name,
                        "quantity": decimal_to_json(item.quantity),
                        "unit": item.unit,
                        "location": item.location,
                        "days_left": days_left,
                        "expires": item.expires.isoformat() if item.expires else None,
                    }
                )
        return sorted(rows, key=lambda row: (row["days_left"], row["name"]))

    def suggested_purchases(self) -> list[dict[str, Any]]:
        totals = self.inventory_totals()
        minimums: dict[tuple[str, str], tuple[str, str, Decimal]] = {}
        for item in self.state.items:
            if item.minimum_stock is None:
                continue
            key = (normalize_name(item.name), item.unit.casefold())
            current = minimums.get(key)
            if current is None or item.minimum_stock > current[2]:
                minimums[key] = (item.name, item.unit, item.minimum_stock)

        suggestions: list[dict[str, Any]] = []
        for key, (name, unit, minimum_stock) in minimums.items():
            current = totals.get(key, Decimal("0"))
            if current < minimum_stock:
                suggestions.append({"name": name, "quantity": minimum_stock - current, "unit": unit, "current": current, "minimum_stock": minimum_stock})
        return sorted(suggestions, key=lambda item: item["name"])

    def location_count(self, prefix: str) -> int:
        wanted = normalize_location(prefix).casefold()
        return sum(1 for item in self.state.items if item.location.casefold().startswith(wanted))

    def active_shopping_count(self) -> int:
        return sum(1 for item in self.state.shopping_list if not item.checked)

    def food_waste_estimate(self, today: date | None = None) -> Decimal:
        today = today or _today()
        total = Decimal("0")
        for item in self.state.items:
            if (
                item.expires is not None
                and item.expires < today
                and item.expires.year == today.year
                and item.expires.month == today.month
                and item.estimated_cost is not None
            ):
                total += item.estimated_cost
        return total

    def summary(self, today: date | None = None) -> dict[str, Any]:
        today = today or _today()
        expiring = self.expiring_soon(today=today)
        suggestions = self.suggested_purchases()
        meals = self.possible_meals(max_missing=0)
        return {
            "total_items": len(self.state.items),
            "expiring_soon": expiring,
            "expiring_soon_count": len(expiring),
            "shopping_list_count": self.active_shopping_count(),
            "suggested_purchases": [
                {
                    "name": item["name"],
                    "quantity": decimal_to_json(item["quantity"]),
                    "unit": item["unit"],
                    "current": decimal_to_json(item["current"]),
                    "minimum_stock": decimal_to_json(item["minimum_stock"]),
                }
                for item in suggestions
            ],
            "suggested_purchase_count": len(suggestions),
            "possible_meals": meals,
            "possible_meal_count": len(meals),
            "food_waste_this_month": decimal_to_json(self.food_waste_estimate(today)),
            "location_counts": {
                "Kitchen": self.location_count("Kitchen"),
                "Refrigerator": self.location_count("Kitchen/Refrigerator"),
                "Freezer": self.location_count("Kitchen/Freezer") + self.location_count("Garage/Chest Freezer") + self.location_count("Garage/Freezer"),
                "Pantry": self.location_count("Kitchen/Pantry"),
            },
        }

    def _find_open_shopping_item(self, name: str, unit: str, source: str) -> ShoppingListItem | None:
        wanted_name = normalize_name(name)
        wanted_unit = unit.casefold()
        return next(
            (
                item
                for item in self.state.shopping_list
                if not item.checked and normalize_name(item.name) == wanted_name and item.unit.casefold() == wanted_unit and item.source == source
            ),
            None,
        )

