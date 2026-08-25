from datetime import date
from decimal import Decimal
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_INVENTORY_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "pantryos" / "inventory.py"
_SPEC = spec_from_file_location("pantryos_inventory", _INVENTORY_PATH)
assert _SPEC is not None and _SPEC.loader is not None
inventory = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = inventory
_SPEC.loader.exec_module(inventory)

InventoryManager = inventory.InventoryManager
InventoryState = inventory.InventoryState


def test_expiring_soon_orders_by_days_left() -> None:
    manager = InventoryManager()
    manager.add_item(
        {
            "name": "Spinach",
            "quantity": 1,
            "unit": "bag",
            "location": "Kitchen/Refrigerator/Crisper",
            "expires": "2026-08-28",
        }
    )
    manager.add_item(
        {
            "name": "Chicken",
            "quantity": 2,
            "unit": "lb",
            "location": "Garage/Chest Freezer",
            "expires": "2026-08-27",
        }
    )

    rows = manager.expiring_soon(today=date(2026, 8, 25))

    assert [row["name"] for row in rows] == ["Chicken", "Spinach"]
    assert [row["days_left"] for row in rows] == [2, 3]


def test_recipe_matching_allows_missing_threshold() -> None:
    manager = InventoryManager()
    manager.add_item(
        {
            "name": "Chicken Breast",
            "quantity": 1,
            "unit": "lb",
            "location": "Kitchen/Refrigerator",
        }
    )
    manager.add_item(
        {
            "name": "Pasta",
            "quantity": 16,
            "unit": "oz",
            "location": "Kitchen/Pantry",
        }
    )
    manager.add_recipe(
        {
            "name": "Chicken Alfredo",
            "prep_minutes": 25,
            "ingredients": [
                {"name": "Chicken Breast", "quantity": 1, "unit": "lb"},
                {"name": "Pasta", "quantity": 16, "unit": "oz"},
                {"name": "Heavy Cream", "quantity": 1, "unit": "cup"},
            ],
        }
    )

    assert manager.possible_meals(max_missing=0) == []
    meals = manager.possible_meals(max_missing=1)

    assert meals[0]["name"] == "Chicken Alfredo"
    assert meals[0]["missing"] == [
        {"name": "Heavy Cream", "quantity": "1", "unit": "cup"}
    ]


def test_add_missing_to_shopping_list_merges_recipe_rows() -> None:
    manager = InventoryManager()
    manager.add_recipe(
        {
            "name": "Omelette",
            "ingredients": [
                {"name": "Eggs", "quantity": 3, "unit": "count"},
                {"name": "Butter", "quantity": 1, "unit": "tbsp"},
            ],
        }
    )

    manager.add_missing_to_shopping_list("omelette")
    manager.add_missing_to_shopping_list("Omelette")

    assert len(manager.state.shopping_list) == 2
    eggs = next(item for item in manager.state.shopping_list if item.name == "Eggs")
    assert eggs.quantity == Decimal("6")
    assert eggs.source == "recipe:Omelette"


def test_suggested_purchases_are_separate_from_shopping_list() -> None:
    manager = InventoryManager()
    manager.add_item(
        {
            "name": "Milk",
            "quantity": "0.25",
            "unit": "gallon",
            "location": "Kitchen/Refrigerator",
            "minimum_stock": 1,
        }
    )

    suggestions = manager.suggested_purchases()

    assert len(suggestions) == 1
    assert suggestions[0]["quantity"] == Decimal("0.75")
    assert manager.active_shopping_count() == 0

    manager.promote_suggested_purchases()

    assert manager.active_shopping_count() == 1


def test_consume_removes_empty_leftover_item() -> None:
    manager = InventoryManager()
    item = manager.add_item(
        {
            "name": "Taco Meat",
            "quantity": 3,
            "unit": "serving",
            "location": "Kitchen/Refrigerator",
            "tags": ["leftover"],
        }
    )

    result = manager.consume_item(item.id, Decimal("3"))

    assert result is None
    assert manager.state.items == []


def test_state_round_trip_preserves_dates_and_decimals() -> None:
    manager = InventoryManager()
    manager.add_item(
        {
            "name": "Chicken Breast",
            "quantity": "3.5",
            "unit": "lb",
            "location": "Garage/Chest Freezer",
            "purchased": "2026-08-21",
            "expires": "2026-09-02",
            "estimated_cost": "12.50",
        }
    )

    clone = InventoryManager(InventoryState.from_dict(manager.to_dict()))
    item = clone.state.items[0]

    assert item.quantity == Decimal("3.5")
    assert item.purchased == date(2026, 8, 21)
    assert item.expires == date(2026, 9, 2)
    assert item.estimated_cost == Decimal("12.50")




def test_consumed_minimum_stock_item_remains_suggestible() -> None:
    manager = InventoryManager()
    item = manager.add_item(
        {
            "name": "Eggs",
            "quantity": 2,
            "unit": "count",
            "location": "Kitchen/Refrigerator",
            "minimum_stock": 12,
        }
    )

    result = manager.consume_item(item.id, Decimal("2"))

    assert result is item
    assert item.quantity == Decimal("0")
    assert manager.suggested_purchases()[0]["quantity"] == Decimal("12")


def test_food_waste_estimate_counts_current_month_only() -> None:
    manager = InventoryManager()
    manager.add_item(
        {
            "name": "Old Milk",
            "quantity": 1,
            "unit": "gallon",
            "location": "Kitchen/Refrigerator",
            "expires": "2026-08-20",
            "estimated_cost": "3.50",
        }
    )
    manager.add_item(
        {
            "name": "Ancient Flour",
            "quantity": 1,
            "unit": "bag",
            "location": "Kitchen/Pantry",
            "expires": "2026-07-20",
            "estimated_cost": "5.00",
        }
    )

    assert manager.food_waste_estimate(today=date(2026, 8, 25)) == Decimal("3.50")
