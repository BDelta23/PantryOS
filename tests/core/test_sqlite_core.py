from __future__ import annotations

import json
import tempfile
from contextlib import closing
import threading
from decimal import Decimal
from pathlib import Path

from pantryos.core import PantryCore
from pantryos.errors import InsufficientInventoryError, ValidationError


def make_core(directory: str) -> PantryCore:
    core = PantryCore(Path(directory) / "pantryos.sqlite3")
    core.migrate()
    return core


def count_rows(core: PantryCore, table: str) -> int:
    with closing(core.connect()) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_fresh_database_migrates_and_has_instance_metadata() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        instance = core.instance()

        assert instance["schema_version"] == 1
        assert instance["state_revision"] == 0
        assert instance["instance_id"].startswith("inst_")
        core.integrity_check()


def test_legacy_import_is_backed_up_and_idempotent() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "legacy.json"
        source.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "id": "legacy-eggs",
                            "name": "Eggs",
                            "quantity": "4",
                            "unit": "count",
                            "location": "Kitchen/Refrigerator/Door",
                            "minimum_stock": "12",
                        }
                    ],
                    "recipes": [
                        {
                            "name": "Omelette",
                            "ingredients": [
                                {"name": "Eggs", "quantity": "3", "unit": "count"},
                                {"name": "Butter", "quantity": "1", "unit": "tbsp"},
                            ],
                        }
                    ],
                    "shopping_list": [
                        {"name": "Milk", "quantity": "1", "unit": "gallon", "source": "manual"}
                    ],
                    "meal_plan": {"Tonight": "Omelette"},
                }
            ),
            encoding="utf-8",
        )
        core = make_core(directory)

        first = core.import_legacy_json(source)
        snapshot_after_first = core.dashboard()
        second = core.import_legacy_json(source)
        snapshot_after_second = core.dashboard()

        assert first.imported is True
        assert second.imported is False
        assert Path(first.backup_path).exists()
        assert count_rows(core, "products") == 1
        assert count_rows(core, "inventory_lots") == 1
        assert count_rows(core, "recipes") == 1
        assert count_rows(core, "recipe_ingredients") == 2
        assert count_rows(core, "meal_plan_entries") == 1
        assert snapshot_after_second["summary"] == snapshot_after_first["summary"]


def test_twenty_concurrent_mutations_do_not_lose_successful_writes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        errors: list[str] = []

        def worker(index: int) -> None:
            try:
                local_core = PantryCore(core.db_path)
                local_core.add_inventory_lot(
                    {
                        "name": f"Item {index}",
                        "quantity": "1",
                        "unit": "count",
                        "location": "Kitchen/Pantry",
                    },
                    source="concurrency_test",
                )
            except Exception as exc:  # noqa: BLE001 - test captures all worker failures
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert count_rows(core, "products") == 20
        assert count_rows(core, "inventory_lots") == 20
        assert count_rows(core, "inventory_events") == 20
        assert core.instance()["state_revision"] == 20


def test_consume_product_uses_fefo_and_rejects_over_consumption() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        later = core.add_inventory_lot(
            {
                "name": "Chicken Breast",
                "quantity": "1",
                "unit": "lb",
                "location": "Garage/Freezer",
                "expires": "2026-09-10",
            }
        )["lot"]
        earlier = core.add_inventory_lot(
            {
                "name": "Chicken Breast",
                "quantity": "8",
                "unit": "oz",
                "location": "Kitchen/Refrigerator",
                "expires": "2026-08-27",
            }
        )["lot"]

        result = core.consume_product(product_name="Chicken Breast", quantity="1", unit="lb")

        assert result["allocations"] == [
            {"lot_id": earlier["id"], "quantity": "8", "unit": "oz"},
            {"lot_id": later["id"], "quantity": "0.5", "unit": "lb"},
        ]
        try:
            core.consume_product(product_name="Chicken Breast", quantity="2", unit="lb")
        except InsufficientInventoryError as exc:
            assert exc.available == "0.5"
        else:
            raise AssertionError("over-consumption should fail")


def test_incompatible_unit_conversion_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        core.add_inventory_lot(
            {
                "name": "Flour",
                "quantity": "1",
                "unit": "lb",
                "location": "Kitchen/Pantry",
            }
        )

        try:
            core.consume_product(product_name="Flour", quantity="1", unit="cup")
        except ValidationError as exc:
            assert "incompatible dimensions" in str(exc)
        else:
            raise AssertionError("mass-to-volume conversion should fail")


def test_transaction_rolls_back_lot_and_event_on_failure() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        try:
            with core.transaction() as connection:
                product_id = core.ensure_product(connection, name="Milk", default_unit="gallon")
                location_id = core.ensure_location_path(connection, "Kitchen/Refrigerator")
                core._insert_lot(connection, product_id, location_id, {"name": "Milk", "quantity": "1", "unit": "gallon"})
                core._append_event(connection, "ADD", product_id=product_id, source="rollback_test")
                raise RuntimeError("injected failure")
        except RuntimeError:
            pass

        assert count_rows(core, "products") == 0
        assert count_rows(core, "inventory_lots") == 0
        assert count_rows(core, "inventory_events") == 0
        assert core.instance()["state_revision"] == 0


def test_backup_restore_round_trips_core_state() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        core.add_inventory_lot(
            {
                "name": "Milk",
                "quantity": "1",
                "unit": "gallon",
                "location": "Kitchen/Refrigerator",
            }
        )
        backup_path = core.backup(Path(directory) / "backup.sqlite3")
        restored = PantryCore(Path(directory) / "restored.sqlite3")
        restored.restore(backup_path)

        assert restored.dashboard()["summary"] == core.dashboard()["summary"]
        restored.integrity_check()

