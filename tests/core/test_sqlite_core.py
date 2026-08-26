from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any

import pantryos.core as core_module
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

        assert instance["schema_version"] == 4
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


def test_failed_pending_migration_restores_prior_database_and_leaves_backup() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        core.add_inventory_lot(
            {
                "name": "Migration Beans",
                "quantity": "2",
                "unit": "can",
                "location": "Kitchen/Pantry",
            }
        )
        before_summary = core.dashboard()["summary"]
        migration_dir = Path(directory) / "migrations"
        migration_dir.mkdir()
        for path in sorted(core_module.MIGRATIONS_DIR.glob("*.sql")):
            shutil.copy2(path, migration_dir / path.name)
        (migration_dir / "005_injected_failure.sql").write_text(
            """
            CREATE TABLE migration_partial_write(id INTEGER PRIMARY KEY);
            INSERT INTO missing_table_for_failure(id) VALUES (1);
            """,
            encoding="utf-8",
        )

        original_migrations_dir = core_module.MIGRATIONS_DIR
        core_module.MIGRATIONS_DIR = migration_dir
        try:
            try:
                core.migrate()
            except sqlite3.DatabaseError:
                pass
            else:
                raise AssertionError("failing migration should raise a database error")
        finally:
            core_module.MIGRATIONS_DIR = original_migrations_dir

        backups = list((Path(directory) / "backups" / "migrations").glob("pantryos-pre-migration-*.sqlite3"))
        failed_copies = list(Path(directory).glob("pantryos.sqlite3.*.failed"))
        assert len(backups) == 1
        assert len(failed_copies) == 1
        with closing(sqlite3.connect(core.db_path)) as connection:
            connection.row_factory = sqlite3.Row
            max_version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            partial_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'migration_partial_write'"
            ).fetchone()
            lot_count = connection.execute(
                "SELECT COUNT(*) FROM inventory_lots l JOIN products p ON p.id = l.product_id WHERE p.name = 'Migration Beans'"
            ).fetchone()[0]
        assert max_version == 4
        assert partial_table is None
        assert lot_count == 1
        assert core.dashboard()["summary"] == before_summary
        core.integrity_check()
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


def test_shopping_lifecycle_and_purchase_completion_are_transactional() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        with core.transaction() as connection:
            core._upsert_shopping_demand(
                connection,
                source_key="manual:coffee",
                product_id=None,
                display_name="Coffee",
                quantity="1",
                unit="count",
                source_kind="manual",
                source_id=None,
                accepted=True,
            )
            shopping_id = connection.execute(
                "SELECT id FROM shopping_demands WHERE source_key = 'manual:coffee'"
            ).fetchone()[0]

        updated = core.update_shopping_item(shopping_id, {"quantity": "2", "note": "whole bean", "store": "Market"})
        checked = core.set_shopping_checked(shopping_id, True)
        unchecked = core.set_shopping_checked(shopping_id, False)
        purchase = core.complete_purchase(
            {
                "store": "Market",
                "location": "Kitchen/Pantry",
                "items": [{"shopping_id": shopping_id, "quantity": "2", "total_cost": "14.50"}],
            }
        )

        assert updated["item"]["quantity"] == "2"
        assert updated["item"]["note"] == "whole bean"
        assert updated["item"]["store"] == "Market"
        assert checked["item"]["checked"] == 1
        assert unchecked["item"]["checked"] == 0
        assert purchase["purchase"]["store"] == "Market"
        assert purchase["lines"][0]["display_name"] == "Coffee"
        assert purchase["lots"][0]["product_name"] == "Coffee"
        assert purchase["lots"][0]["purchase_line_id"] == purchase["lines"][0]["id"]

        with closing(core.connect()) as connection:
            completed = connection.execute("SELECT status, checked FROM shopping_demands WHERE id = ?", (shopping_id,)).fetchone()
        assert dict(completed) == {"status": "completed", "checked": 1}

        with core.transaction() as connection:
            core._upsert_shopping_demand(
                connection,
                source_key="manual:tea",
                product_id=None,
                display_name="Tea",
                quantity="1",
                unit="count",
                source_kind="manual",
                source_id=None,
                accepted=True,
            )
            tea_id = connection.execute("SELECT id FROM shopping_demands WHERE source_key = 'manual:tea'").fetchone()[0]
        before_summary = core.dashboard()["summary"]
        try:
            core.complete_purchase(
                {
                    "store": "Market",
                    "items": [{"shopping_id": tea_id}, {"shopping_id": "missing"}],
                }
            )
        except Exception:
            pass
        else:
            raise AssertionError("invalid purchase completion should fail")

        assert core.dashboard()["summary"] == before_summary
        with closing(core.connect()) as connection:
            assert connection.execute("SELECT COUNT(*) FROM purchases WHERE store = 'Market'").fetchone()[0] == 1
            assert connection.execute("SELECT status FROM shopping_demands WHERE id = ?", (tea_id,)).fetchone()[0] == "active"


def test_shopping_items_can_be_removed_or_suppressed_without_deleting_history() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        with core.transaction() as connection:
            core._upsert_shopping_demand(
                connection,
                source_key="manual:rice",
                product_id=None,
                display_name="Rice",
                quantity="1",
                unit="count",
                source_kind="manual",
                source_id=None,
                accepted=True,
            )
            rice_id = connection.execute("SELECT id FROM shopping_demands WHERE source_key = 'manual:rice'").fetchone()[0]
            core._upsert_shopping_demand(
                connection,
                source_key="meal_plan:beans",
                product_id=None,
                display_name="Beans",
                quantity="1",
                unit="count",
                source_kind="meal_plan",
                source_id="active_plan",
                accepted=True,
            )
            beans_id = connection.execute("SELECT id FROM shopping_demands WHERE source_key = 'meal_plan:beans'").fetchone()[0]

        core.remove_shopping_item(rice_id)
        core.update_shopping_item(beans_id, {"status": "suppressed"})

        rows = {row["display_name"]: row for row in core.shopping_items()}
        assert rows["Rice"]["status"] == "removed"
        assert rows["Beans"]["status"] == "suppressed"
        assert rows["Beans"]["accepted"] == 0
def test_barcode_mapping_resolves_adds_lot_and_persists() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        mapping = core.save_barcode_mapping(
            {
                "barcode": "012345678905",
                "name": "Barcode Beans",
                "package_quantity": "15",
                "package_unit": "oz",
                "brand": "Local",
            }
        )
        resolved = core.resolve_barcode("012345678905")
        added = core.add_lot_from_barcode(
            "012345678905",
            {"location": "Kitchen/Pantry", "estimated_cost": "1.99"},
        )
        reopened = PantryCore(core.db_path)
        reopened_resolved = reopened.resolve_barcode("012345678905")

        assert mapping["mapping"]["product_name"] == "Barcode Beans"
        assert resolved["matched"] is True
        assert resolved["mapping"]["package_quantity"] == "15"
        assert added["lot"]["product_name"] == "Barcode Beans"
        assert added["lot"]["quantity"] == "15"
        assert added["lot"]["unit"] == "oz"
        assert reopened_resolved == resolved

        try:
            core.save_barcode_mapping({"barcode": "012345678905", "name": "Other Beans"})
        except ValidationError as exc:
            assert "already mapped" in str(exc)
        else:
            raise AssertionError("duplicate barcode mapping should fail")


def test_receipt_review_commit_is_idempotent_and_records_price_history() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        receipt_text = """Store: Receipt Market
Date: 2026-08-24
Receipt Milk,1,gallon,3.99
Receipt Eggs,12,count,4.50,12345
Total: 8.49
"""

        uploaded = core.upload_receipt({"filename": "receipt.txt", "mime_type": "text/plain", "text": receipt_text})
        assert uploaded["receipt"]["status"] == "uploaded"
        assert uploaded["receipt"]["original_filename"] == "receipt.txt"
        assert "storage_path" not in uploaded["receipt"]
        with closing(core.connect()) as connection:
            storage_path = Path(connection.execute("SELECT storage_path FROM receipt_uploads WHERE id = ?", (uploaded["receipt"]["id"],)).fetchone()[0])
        assert storage_path.exists()
        assert storage_path.parent == Path(directory) / "receipts"
        assert core.dashboard()["summary"]["active_lot_count"] == 0

        extracted = core.extract_receipt(uploaded["receipt"]["id"])
        review = extracted["review"]
        assert extracted["receipt"]["status"] == "review"
        assert review["store"] == "Receipt Market"
        assert len(review["items"]) == 2
        assert core.dashboard()["summary"]["active_lot_count"] == 0

        review["location"] = "Kitchen/Refrigerator"
        updated = core.update_receipt_review(uploaded["receipt"]["id"], review)
        committed = core.commit_receipt(uploaded["receipt"]["id"])
        duplicate = core.commit_receipt(uploaded["receipt"]["id"])
        extracted_again = core.extract_receipt(uploaded["receipt"]["id"])

        assert updated["receipt"]["status"] == "review"
        assert committed["duplicate"] is False
        assert committed["receipt"]["status"] == "committed"
        assert committed["purchase"]["source"] == "receipt"
        assert [lot["product_name"] for lot in committed["lots"]] == ["Receipt Milk", "Receipt Eggs"]
        assert {lot["location_path"] for lot in committed["lots"]} == {"Kitchen/Refrigerator"}
        assert duplicate["duplicate"] is True
        assert len(duplicate["lines"]) == 2
        assert extracted_again["receipt"]["status"] == "committed"
        assert count_rows(core, "purchases") == 1
        assert count_rows(core, "inventory_lots") == 2
        assert count_rows(core, "price_history") == 2

        purchase = core.purchase(committed["purchase"]["id"])
        milk_line = next(line for line in purchase["lines"] if line["display_name"] == "Receipt Milk")
        eggs_line = next(line for line in purchase["lines"] if line["display_name"] == "Receipt Eggs")
        milk_prices = core.product_prices(milk_line["product_id"])["prices"]
        eggs_prices = core.product_prices(eggs_line["product_id"])["prices"]
        assert milk_prices[0]["comparable_unit"] == "fl oz"
        assert Decimal(milk_prices[0]["comparable_quantity"]) == Decimal("128")
        assert milk_prices[0]["unit_price"] == "0.03"
        assert "Baseline initialized" in milk_prices[0]["explanation"]
        assert eggs_prices[0]["comparable_unit"] == "count"
        assert eggs_prices[0]["unit_price"] == "0.38"
        assert core.resolve_barcode("12345")["matched"] is True


def test_price_history_uses_recent_median_compatible_unit_anomaly() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)

        def commit_receipt(purchased_at: str, total_cost: str) -> dict[str, Any]:
            uploaded = core.upload_receipt(
                {
                    "filename": f"price-{purchased_at}.txt",
                    "mime_type": "text/plain",
                    "text": (
                        "Store: Price Market\n"
                        f"Date: {purchased_at}\n"
                        f"Anomaly Apples,10,count,{total_cost}\n"
                        f"Total: {total_cost}\n"
                    ),
                }
            )
            core.extract_receipt(uploaded["receipt"]["id"])
            return core.commit_receipt(uploaded["receipt"]["id"])

        first = commit_receipt("2026-08-21", "10.00")
        product_id = first["lines"][0]["product_id"]
        commit_receipt("2026-08-22", "12.00")
        commit_receipt("2026-08-23", "11.00")
        commit_receipt("2026-08-24", "20.00")

        prices = core.product_prices(product_id)
        latest = prices["prices"][0]
        analysis = prices["analysis"]

        assert analysis["baseline_policy"] == "recent_median_compatible_unit"
        assert analysis["evidence_window"] == "up to 5 prior purchases with the same comparable unit"
        assert analysis["latest"]["status"] == "high"
        assert analysis["latest"]["baseline_sample_count"] == 3
        assert analysis["latest"]["baseline_unit_price"] == "1.10"
        assert analysis["latest"]["anomaly_ratio"] == "1.82"
        assert latest["unit_price"] == "2.00"
        assert latest["baseline_unit_price"] == "1.10"
        assert latest["anomaly_ratio"] == "1.82"
        assert "recent median baseline" in latest["explanation"]
        assert "3 compatible prior purchases" in latest["explanation"]


def test_receipt_upload_rejects_unsupported_type_and_large_text() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        try:
            core.upload_receipt({"filename": "receipt.png", "mime_type": "image/png", "text": "not supported"})
        except ValidationError as exc:
            assert "Unsupported receipt type" in str(exc)
        else:
            raise AssertionError("image receipt upload should fail until OCR exists")

        try:
            core.upload_receipt({"filename": "large.txt", "mime_type": "text/plain", "text": "x" * 64001})
        except ValidationError as exc:
            assert "exceeds 64000 bytes" in str(exc)
        else:
            raise AssertionError("oversized receipt upload should fail")


def test_discard_records_monthly_waste_and_location_values() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        apples = core.add_inventory_lot(
            {
                "name": "Waste Apples",
                "quantity": "10",
                "unit": "count",
                "location": "Kitchen/Refrigerator",
                "estimated_cost": "5.00",
            }
        )["lot"]
        core.add_inventory_lot(
            {
                "name": "Stored Rice",
                "quantity": "2",
                "unit": "count",
                "location": "Kitchen/Pantry",
                "estimated_cost": "2.00",
            }
        )
        core.consume_product(product_name="Waste Apples", quantity="4", unit="count")

        before_discard = core.dashboard()["summary"]
        discarded = core.discard_lot(apples["id"], reason="spoiled")

        with closing(core.connect()) as connection:
            metadata_json = connection.execute(
                "SELECT metadata_json FROM inventory_events WHERE event_type = 'DISCARD' ORDER BY revision DESC LIMIT 1"
            ).fetchone()[0]
            connection.execute("UPDATE inventory_lots SET total_cost = NULL WHERE id = ?", (apples["id"],))
            connection.commit()
        after_discard = core.dashboard()["summary"]
        waste_metadata = json.loads(metadata_json)

        assert before_discard["location_counts"]["Refrigerator"] == 1
        assert before_discard["location_values"]["Refrigerator"] == "3.00"
        assert before_discard["location_values"]["Pantry"] == "2.00"
        assert discarded["discarded_value"] == "3.00"
        assert waste_metadata["waste_value"] == "3.00"
        assert after_discard["food_waste_this_month"] == "3.00"
        assert after_discard["location_counts"]["Refrigerator"] == 0
        assert after_discard["location_values"]["Refrigerator"] == "0.00"
        assert after_discard["location_counts"]["Pantry"] == 1
        assert after_discard["locations"] == [
            {
                "location_id": after_discard["locations"][0]["location_id"],
                "path": "Kitchen/Pantry",
                "active_lot_count": 1,
                "inventory_value": "2.00",
                "currency": "USD",
            }
        ]

def test_cooking_session_start_complete_leftover_and_rollback() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        lot = core.add_inventory_lot(
            {"name": "Soup Base", "quantity": "2", "unit": "cup", "location": "Kitchen/Refrigerator"}
        )["lot"]
        with core.transaction() as connection:
            core._upsert_recipe(connection, {"name": "Soup Night", "ingredients": []})

        started = core.start_cooking_session({"recipe_name": "Soup Night", "planned_servings": "2"})
        after_start = core.dashboard()
        started_lot = next(row for row in after_start["lots"] if row["id"] == lot["id"])
        assert started["session"]["status"] == "cooking"
        assert started_lot["quantity"] == "2"

        completed = core.complete_cooking_session(
            started["session"]["id"],
            {
                "actual_servings": "2",
                "allocations": [{"lot_id": lot["id"], "quantity": "1", "unit": "cup"}],
                "leftovers": [
                    {
                        "name": "Soup Night Leftovers",
                        "quantity": "2",
                        "unit": "serving",
                        "location": "Kitchen/Refrigerator",
                        "use_by": "2026-08-30",
                    }
                ],
            },
        )
        after_complete = core.dashboard()
        consumed_lot = next(row for row in after_complete["lots"] if row["id"] == lot["id"])
        leftover = completed["leftovers"][0]

        assert completed["session"]["status"] == "completed"
        assert completed["allocations"] == [{"lot_id": lot["id"], "quantity": "1", "unit": "cup"}]
        assert consumed_lot["quantity"] == "1"
        assert leftover["lot_type"] == "leftover"
        assert leftover["cooking_session_id"] == started["session"]["id"]
        assert any(event["event_type"] == "cooking.completed" for event in after_complete["events"])
        assert any(event["event_type"] == "LEFTOVER_CREATE" for event in after_complete["events"])

        second_lot = core.add_inventory_lot(
            {"name": "Sauce", "quantity": "1", "unit": "cup", "location": "Kitchen/Refrigerator"}
        )["lot"]
        failed_session = core.start_cooking_session({"recipe_name": "Soup Night"})["session"]
        before_failed = core.dashboard()
        try:
            core.complete_cooking_session(
                failed_session["id"],
                {
                    "allocations": [{"lot_id": second_lot["id"], "quantity": "5", "unit": "cup"}],
                    "leftovers": [{"name": "Should Not Exist", "quantity": "1", "unit": "serving"}],
                },
            )
        except InsufficientInventoryError:
            pass
        else:
            raise AssertionError("over-allocated cooking completion should fail")

        after_failed = core.dashboard()
        unchanged_lot = next(row for row in after_failed["lots"] if row["id"] == second_lot["id"])
        session_after_failure = core.cooking_session(failed_session["id"])
        assert unchanged_lot["quantity"] == "1"
        assert session_after_failure["status"] == "cooking"
        assert after_failed["summary"] == before_failed["summary"]
        assert not any(row["product_name"] == "Should Not Exist" for row in after_failed["lots"])


def test_cooking_session_cancel_does_not_consume_inventory() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = make_core(directory)
        lot = core.add_inventory_lot(
            {"name": "Rice", "quantity": "3", "unit": "cup", "location": "Kitchen/Pantry"}
        )["lot"]
        with core.transaction() as connection:
            core._upsert_recipe(connection, {"name": "Rice Bowls", "ingredients": []})
        session = core.start_cooking_session({"recipe_name": "Rice Bowls"})["session"]
        cancelled = core.cancel_cooking_session(session["id"], {"reason": "changed plans"})
        snapshot = core.dashboard()
        rice_lot = next(row for row in snapshot["lots"] if row["id"] == lot["id"])

        assert cancelled["session"]["status"] == "cancelled"
        assert rice_lot["quantity"] == "3"
        assert any(event["event_type"] == "cooking.cancelled" for event in snapshot["events"])