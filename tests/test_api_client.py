import asyncio
import os
import sys
import threading
from contextlib import contextmanager
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory

_SERVER_PATH = Path(__file__).resolve().parents[1] / "app" / "server.py"
_SPEC = spec_from_file_location("pantryos_server_for_client_tests", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
server_module = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = server_module
_SPEC.loader.exec_module(server_module)

_CLIENT_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "pantryos" / "api_client.py"
_CLIENT_SPEC = spec_from_file_location("pantryos_api_client", _CLIENT_PATH)
assert _CLIENT_SPEC is not None and _CLIENT_SPEC.loader is not None
client_module = module_from_spec(_CLIENT_SPEC)
sys.modules[_CLIENT_SPEC.name] = client_module
_CLIENT_SPEC.loader.exec_module(client_module)
PantryAPIAuthError = client_module.PantryAPIAuthError
PantryAPIClient = client_module.PantryAPIClient


@contextmanager
def api_token(value: str):
    original = os.environ.get("PANTRYOS_API_TOKEN")
    os.environ["PANTRYOS_API_TOKEN"] = value
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("PANTRYOS_API_TOKEN", None)
        else:
            os.environ["PANTRYOS_API_TOKEN"] = original


@contextmanager
def running_server():
    with TemporaryDirectory() as directory, api_token("test-token"):
        data_path = Path(directory) / "pantryos.sqlite3"
        httpd = server_module.make_server("127.0.0.1", 0, data_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}"
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def test_api_client_reads_snapshot_and_mutates_inventory() -> None:
    async def scenario(base_url: str) -> None:
        client = PantryAPIClient(base_url, "test-token")

        instance = await client.async_instance()
        initial = await client.async_refresh()
        created = await client.async_add_item(
            {"name": "HA Butter", "quantity": "1", "unit": "lb", "location": "Kitchen/Refrigerator"}
        )
        consumed = await client.async_consume_item(created["item"]["id"], "0.25", reason="client test")
        shopping = await client.async_add_shopping_item({"name": "Oats", "quantity": "1", "unit": "count"})
        refreshed = await client.async_refresh()

        assert instance["schema_version"] == 4
        initial_total = initial["summary"]["total_items"]
        assert created["item"]["name"] == "HA Butter"
        assert consumed["allocations"][0]["quantity"] == "0.25"
        assert shopping["item"]["name"] == "Oats"
        assert refreshed["summary"]["total_items"] == initial_total + 1
        assert client.available is True
        assert client.summary()["total_items"] == initial_total + 1

    with running_server() as base_url:
        asyncio.run(scenario(base_url))


def test_api_client_maps_auth_failures() -> None:
    async def scenario(base_url: str) -> None:
        client = PantryAPIClient(base_url, "wrong-token")
        try:
            await client.async_instance()
        except PantryAPIAuthError as exc:
            assert exc.status == 401
            assert exc.code == "unauthorized"
            return
        raise AssertionError("Expected an auth failure")

    with running_server() as base_url:
        asyncio.run(scenario(base_url))
def test_api_client_rebuilds_meal_plan_shopping_idempotently() -> None:
    async def scenario(base_url: str) -> None:
        client = PantryAPIClient(base_url, "test-token")
        await client.async_add_item(
            {"name": "Plan Flour", "quantity": "8", "unit": "oz", "location": "Kitchen/Pantry"}
        )
        await client.async_add_recipe(
            {
                "name": "Plan Biscuits",
                "ingredients": [
                    {"name": "Plan Flour", "quantity": "16", "unit": "oz"},
                    {"name": "Baking Powder", "quantity": "1", "unit": "tbsp"},
                ],
            }
        )
        await client.async_plan_meal("Lunch", "Plan Biscuits")
        await client.async_plan_meal("Dinner", "Plan Biscuits")

        first = await client.async_rebuild_shopping()
        second = await client.async_rebuild_shopping()

        first_items = {item["name"]: item for item in first["items"] if item["source"].startswith("meal_plan:")}
        second_items = {item["name"]: item for item in second["items"] if item["source"].startswith("meal_plan:")}
        assert first_items == second_items
        assert first_items["Plan Flour"]["quantity"] == "24"
        assert first_items["Baking Powder"]["quantity"] == "2"
        assert len(first_items) == 2

    with running_server() as base_url:
        asyncio.run(scenario(base_url))
def test_api_client_manages_shopping_lifecycle_and_purchase_completion() -> None:
    async def scenario(base_url: str) -> None:
        client = PantryAPIClient(base_url, "test-token")
        added = await client.async_add_shopping_item({"name": "Pears", "quantity": "3", "unit": "count"})
        shopping_id = added["item"]["id"]

        updated = await client.async_update_shopping_item(shopping_id, {"quantity": "4", "note": "ripe", "store": "Market"})
        checked = await client.async_check_shopping_item(shopping_id)
        uncheck = await client.async_uncheck_shopping_item(shopping_id)
        checked_again = await client.async_check_shopping_item(shopping_id)
        purchase = await client.async_complete_purchase(
            {
                "store": "Market",
                "location": "Kitchen/Fruit Bowl",
                "items": [{"shopping_id": shopping_id, "quantity": "4", "total_cost": "5.25"}],
            }
        )
        await client.async_refresh()

        assert updated["item"]["quantity"] == "4"
        assert updated["item"]["note"] == "ripe"
        assert checked["item"]["checked"] is True
        assert uncheck["item"]["checked"] is False
        assert checked_again["item"]["checked"] is True
        assert purchase["purchase"]["store"] == "Market"
        assert purchase["lines"][0]["display_name"] == "Pears"
        assert purchase["lots"][0]["name"] == "Pears"
        assert any(item["name"] == "Pears" for item in client._dashboard["items"])

        removed = await client.async_add_shopping_item({"name": "Napkins", "quantity": "1", "unit": "count"})
        result = await client.async_remove_shopping_item(removed["item"]["id"])
        assert result["ok"] is True

    with running_server() as base_url:
        asyncio.run(scenario(base_url))
def test_api_client_reports_waste_and_location_values() -> None:
    async def scenario(base_url: str) -> None:
        client = PantryAPIClient(base_url, "test-token")
        before = await client.async_refresh()
        created = await client.async_add_item(
            {
                "name": "Client Waste Apples",
                "quantity": "2",
                "unit": "count",
                "location": "Kitchen/Pantry",
                "estimated_cost": "6.00",
            }
        )
        with_value = await client.async_refresh()
        discarded = await client.async_discard_item(created["item"]["id"], reason="spoiled")
        after_discard = await client.async_refresh()

        assert Decimal(with_value["summary"]["location_values"]["Kitchen"]) == Decimal(before["summary"]["location_values"]["Kitchen"]) + Decimal("6.00")
        assert Decimal(with_value["summary"]["location_values"]["Pantry"]) == Decimal(before["summary"]["location_values"]["Pantry"]) + Decimal("6.00")
        assert discarded["discarded_value"] == "6.00"
        assert after_discard["summary"]["food_waste_this_month"] == "6.00"
        assert Decimal(after_discard["summary"]["location_values"]["Pantry"]) == Decimal(before["summary"]["location_values"]["Pantry"])

    with running_server() as base_url:
        asyncio.run(scenario(base_url))

def test_api_client_completes_cooking_session_with_leftover() -> None:
    async def scenario(base_url: str) -> None:
        client = PantryAPIClient(base_url, "test-token")
        lot = await client.async_add_item(
            {"name": "Client Sauce", "quantity": "2", "unit": "cup", "location": "Kitchen/Refrigerator"}
        )
        await client.async_add_recipe({"name": "Client Pasta", "ingredients": []})
        started = await client.async_start_cooking_session({"recipe_name": "Client Pasta", "planned_servings": "2"})
        snapshot_after_start = await client.async_refresh()
        sauce_after_start = next(item for item in snapshot_after_start["items"] if item["id"] == lot["item"]["id"])
        completed = await client.async_complete_cooking_session(
            started["session"]["id"],
            {
                "allocations": [{"lot_id": lot["item"]["id"], "quantity": "1", "unit": "cup"}],
                "leftovers": [
                    {"name": "Client Pasta Leftovers", "quantity": "1", "unit": "serving", "location": "Kitchen/Refrigerator"}
                ],
            },
        )
        snapshot_after_complete = await client.async_refresh()
        sauce_after_complete = next(item for item in snapshot_after_complete["items"] if item["id"] == lot["item"]["id"])

        assert started["session"]["status"] == "cooking"
        assert sauce_after_start["quantity"] == "2"
        assert completed["session"]["status"] == "completed"
        assert completed["leftovers"][0]["name"] == "Client Pasta Leftovers"
        assert sauce_after_complete["quantity"] == "1"
        assert any(item["name"] == "Client Pasta Leftovers" for item in snapshot_after_complete["leftovers"])

    with running_server() as base_url:
        asyncio.run(scenario(base_url))

def test_api_client_receipt_purchase_price_and_leftover_routes() -> None:
    async def scenario(base_url: str) -> None:
        client = PantryAPIClient(base_url, "test-token")
        leftover = await client.async_add_item(
            {"name": "Client Leftover Beans", "quantity": "2", "unit": "serving", "location": "Kitchen/Refrigerator", "tags": ["leftover"]}
        )
        leftovers = await client.async_leftovers()
        uploaded = await client.async_upload_receipt(
            {
                "filename": "ha-receipt.txt",
                "mime_type": "text/plain",
                "text": "Store: HA Receipt Market\nDate: 2026-08-25\nHA Receipt Rice,2,count,5.50,444555666777\nTotal: 5.50\n",
            }
        )
        extracted = await client.async_extract_receipt(uploaded["receipt"]["id"])
        review = extracted["review"]
        review["location"] = "Kitchen/Pantry"
        updated = await client.async_update_receipt_review(uploaded["receipt"]["id"], review)
        committed = await client.async_commit_receipt(uploaded["receipt"]["id"])
        duplicate = await client.async_commit_receipt(uploaded["receipt"]["id"])
        purchases = await client.async_purchases()
        purchase = await client.async_purchase(committed["purchase"]["id"])
        prices = await client.async_product_prices(purchase["lines"][0]["product_id"])
        rejected_upload = await client.async_upload_receipt(
            {
                "filename": "reject-me.txt",
                "mime_type": "text/plain",
                "text": "Store: Reject Market\nReject Crackers,1,count,1.25\nTotal: 1.25\n",
            }
        )
        rejected = await client.async_reject_receipt(rejected_upload["receipt"]["id"], reason="bad scan")

        assert leftover["item"]["name"] == "Client Leftover Beans"
        assert any(item["name"] == "Client Leftover Beans" for item in leftovers["items"])
        assert uploaded["receipt"]["status"] == "uploaded"
        assert updated["review"]["location"] == "Kitchen/Pantry"
        assert committed["purchase"]["store"] == "HA Receipt Market"
        assert committed["lots"][0]["name"] == "HA Receipt Rice"
        assert duplicate["duplicate"] is True
        assert any(item["id"] == committed["purchase"]["id"] for item in purchases["items"])
        assert purchase["prices"][0]["unit_price"] == "2.75"
        assert prices["prices"][0]["comparable_unit"] == "count"
        assert rejected["receipt"]["status"] == "rejected"

    with running_server() as base_url:
        asyncio.run(scenario(base_url))