from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from pantryos.core import PantryCore

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "pantryos.py"


def cli_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PANTRYOS_DATA_DIR", None)
    env.pop("PANTRYOS_BACKUP_DIR", None)
    if overrides:
        env.update(overrides)
    return env


def run_cli(*args: str, env: dict[str, str] | None = None) -> dict:
    completed = subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=cli_env(env),
    )
    return json.loads(completed.stdout)


def run_cli_failed(*args: str, env: dict[str, str] | None = None) -> dict:
    completed = subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=cli_env(env),
    )
    assert completed.returncode == 1
    return json.loads(completed.stderr)


def test_pantryos_cli_doctor_backup_restore_and_legacy_dry_run() -> None:
    with TemporaryDirectory() as directory:
        source_db = Path(directory) / "source.sqlite3"
        restored_db = Path(directory) / "restored.sqlite3"
        backup_path = Path(directory) / "backup.sqlite3"
        legacy_path = Path(directory) / "legacy.json"
        backup_dir = Path(directory) / "legacy-backups"

        core = PantryCore(source_db)
        core.migrate()
        core.add_inventory_lot(
            {
                "name": "CLI Apples",
                "quantity": "3",
                "unit": "count",
                "location": "Kitchen/Pantry",
                "estimated_cost": "4.50",
            }
        )
        legacy_path.write_text(
            json.dumps(
                {
                    "items": [{"name": "Legacy CLI Beans", "quantity": "2", "unit": "can", "location": "Kitchen/Pantry"}],
                    "recipes": [{"name": "Legacy Soup", "ingredients": [{"name": "Legacy CLI Beans", "quantity": "1", "unit": "can"}]}],
                    "shopping_list": [{"name": "Legacy Rice", "quantity": "1", "unit": "bag"}],
                    "meal_plan": {"Tonight": "Legacy Soup"},
                }
            ),
            encoding="utf-8",
        )

        doctor = run_cli("--db", str(source_db), "doctor")
        backup = run_cli("--db", str(source_db), "backup", "--output", str(backup_path))
        verify = run_cli("--db", str(restored_db), "restore", "--input", str(backup_path), "--verify-only")
        restore = run_cli("--db", str(restored_db), "restore", "--input", str(backup_path), "--verify")
        restored_doctor = run_cli("--db", str(restored_db), "doctor")
        dry_run = run_cli("--db", str(Path(directory) / "dry-run.sqlite3"), "import-legacy", "--path", str(legacy_path), "--dry-run")
        imported = run_cli("--db", str(Path(directory) / "legacy.sqlite3"), "import-legacy", "--path", str(legacy_path), "--backup-dir", str(backup_dir))

        manifest = json.loads(Path(backup["manifest"]).read_text(encoding="utf-8"))
        assert doctor["ok"] is True
        assert doctor["schema_version"] == 4
        assert doctor["counts"]["lots"] == 1
        assert Path(backup["backup"]).exists()
        assert manifest["sha256"] == backup["sha256"]
        assert verify == {"ok": True, "format": "sqlite", "restored": False, "schema_version": 4, "verified": True}
        assert restore["ok"] is True
        assert restore["verified"] is True
        assert restored_doctor["counts"] == doctor["counts"]
        assert dry_run["dry_run"] is True
        assert dry_run["imported"] is False
        assert dry_run["item_count"] == 1
        assert not (Path(directory) / "dry-run.sqlite3").exists()
        assert imported["imported"] is True
        assert imported["recipe_count"] == 1
        assert Path(imported["backup_path"]).exists()


def test_pantryos_cli_archive_backup_restores_receipt_upload_files() -> None:
    with TemporaryDirectory() as directory:
        source_db = Path(directory) / "source.sqlite3"
        restored_db = Path(directory) / "restored.sqlite3"
        archive_path = Path(directory) / "backup.zip"

        core = PantryCore(source_db)
        upload = core.upload_receipt(
            {
                "filename": "market.txt",
                "mime_type": "text/plain",
                "text": "Store: Market\nDate: 2026-08-25\nApples, 2, count, 3.00\nTotal: 3.00",
            }
        )
        receipt_id = upload["receipt"]["id"]
        with closing(sqlite3.connect(source_db)) as connection:
            row = connection.execute(
                "SELECT storage_path FROM receipt_uploads WHERE id = ?",
                (receipt_id,),
            ).fetchone()
        assert row is not None
        original_storage_path = Path(row[0])
        assert original_storage_path.exists()

        backup = run_cli("--db", str(source_db), "backup", "--output", str(archive_path))
        verify = run_cli("--db", str(restored_db), "restore", "--input", str(archive_path), "--verify-only")
        restore = run_cli("--db", str(restored_db), "restore", "--input", str(archive_path), "--verify")

        assert backup["format"] == "archive"
        assert backup["receipt_upload_count"] == 1
        assert verify == {
            "ok": True,
            "format": "archive",
            "restored": False,
            "schema_version": 4,
            "verified": True,
            "receipt_upload_count": 1,
        }
        assert restore["ok"] is True
        assert restore["format"] == "archive"
        assert restore["receipt_upload_count"] == 1

        with ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            assert manifest["database"]["path"] == "pantryos.sqlite3"
            assert manifest["receipt_uploads"][0]["id"] == receipt_id
            receipt_member = manifest["receipt_uploads"][0]["path"]
            assert receipt_member.startswith("receipts/")
            assert archive.read(receipt_member) == original_storage_path.read_bytes()

        with closing(sqlite3.connect(restored_db)) as connection:
            row = connection.execute(
                "SELECT storage_path FROM receipt_uploads WHERE id = ?",
                (receipt_id,),
            ).fetchone()
        assert row is not None
        restored_storage_path = Path(row[0])
        assert restored_storage_path.exists()
        assert restored_storage_path.parent == restored_db.parent / "receipts"
        assert restored_storage_path.read_text(encoding="utf-8").startswith("Store: Market")

        restored_core = PantryCore(restored_db)
        extracted = restored_core.extract_receipt(receipt_id)
        assert extracted["review"]["store"] == "Market"


def test_pantryos_cli_purges_old_receipt_uploads() -> None:
    with TemporaryDirectory() as directory:
        db_path = Path(directory) / "source.sqlite3"
        core = PantryCore(db_path)
        uploaded = core.upload_receipt(
            {
                "filename": "old.txt",
                "mime_type": "text/plain",
                "text": "Store: CLI Market\nDate: 2026-08-24\nCLI Beans,1,count,1.00\nTotal: 1.00",
            }
        )
        receipt_id = uploaded["receipt"]["id"]
        core.reject_receipt(receipt_id, reason="bad scan")
        with closing(sqlite3.connect(db_path)) as connection:
            connection.execute("UPDATE receipt_uploads SET updated_at = ? WHERE id = ?", ("2000-01-01T00:00:00Z", receipt_id))
            storage_path = Path(connection.execute("SELECT storage_path FROM receipt_uploads WHERE id = ?", (receipt_id,)).fetchone()[0])
            connection.commit()
        assert storage_path.exists()

        dry_run = run_cli("--db", str(db_path), "purge-receipts", "--older-than-days", "1", "--dry-run")
        purged = run_cli("--db", str(db_path), "purge-receipts", "--older-than-days", "1")

        assert dry_run["dry_run"] is True
        assert dry_run["eligible_count"] == 1
        assert purged["purged_count"] == 1
        assert purged["deleted_files"] == 1
        assert not storage_path.exists()
        with closing(sqlite3.connect(db_path)) as connection:
            status = connection.execute("SELECT status FROM receipt_uploads WHERE id = ?", (receipt_id,)).fetchone()[0]
        assert status == "purged"


def test_pantryos_cli_enforces_container_data_and_backup_allowlists() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        data_dir = root / "data"
        backup_dir = data_dir / "backups"
        data_dir.mkdir()
        backup_dir.mkdir()
        env = {"PANTRYOS_DATA_DIR": str(data_dir), "PANTRYOS_BACKUP_DIR": str(backup_dir)}
        source_db = data_dir / "source.sqlite3"
        inside_backup = backup_dir / "allowed.sqlite3"
        outside_db = root / "outside.sqlite3"
        outside_backup = root / "outside.sqlite3"

        core = PantryCore(source_db)
        core.add_inventory_lot({"name": "Policy Apples", "quantity": "1", "unit": "count", "location": "Kitchen"})

        backup = run_cli("--db", str(source_db), "backup", "--output", str(inside_backup), env=env)
        db_error = run_cli_failed("--db", str(outside_db), "doctor", env=env)
        backup_error = run_cli_failed("--db", str(source_db), "backup", "--output", str(outside_backup), env=env)

        assert backup["ok"] is True
        assert Path(backup["backup"]) == inside_backup
        assert db_error["error"] == "ValidationError"
        assert "Database path must be inside" in db_error["detail"]
        assert backup_error["error"] == "ValidationError"
        assert "Backup output path must be inside" in backup_error["detail"]
