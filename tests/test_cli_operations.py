from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from pantryos.core import PantryCore

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "pantryos.py"


def run_cli(*args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return json.loads(completed.stdout)


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
        assert verify == {"ok": True, "restored": False, "schema_version": 4, "verified": True}
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