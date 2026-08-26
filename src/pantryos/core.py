"""SQLite-backed PantryOS Core service."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing, contextmanager
from dataclasses import dataclass
from decimal import Decimal
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterator

from .errors import InsufficientInventoryError, NotFoundError, ValidationError
from .units import UNITS, convert, decimal_text, require_non_negative, require_positive, unit_code

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
CURRENT_SCHEMA_VERSION = 4
MAX_RECEIPT_UPLOAD_BYTES = 64_000
SUPPORTED_RECEIPT_MIME_TYPES = {"text/plain": ".txt", "text/csv": ".csv"}
BACKUP_ARCHIVE_DB = "pantryos.sqlite3"
BACKUP_ARCHIVE_MANIFEST = "manifest.json"
BACKUP_ARCHIVE_RECEIPTS = "receipts"


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def normalize_name(value: str) -> str:
    return " ".join(value.casefold().strip().split())


def normalize_location_parts(path: str | None) -> list[str]:
    if not path:
        return ["Unassigned"]
    parts = [part.strip() for part in path.replace("\\", "/").split("/") if part.strip()]
    return parts or ["Unassigned"]


@dataclass(frozen=True, slots=True)
class ImportResult:
    imported: bool
    content_hash: str
    backup_path: str
    item_count: int
    recipe_count: int
    shopping_count: int
    meal_plan_count: int


class PantryCore:
    """Transactional PantryOS Core backed by SQLite."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
        return connection

    def migrate(self) -> None:
        migration_paths = sorted(MIGRATIONS_DIR.glob("*.sql"))
        had_existing_database = self.db_path.exists() and self.db_path.stat().st_size > 0
        with closing(self.connect()) as connection:
            applied = self._applied_migration_versions(connection)
        pending_paths = [path for path in migration_paths if int(path.stem.split("_", 1)[0]) not in applied]
        recovery_backup = self._create_pre_migration_backup() if pending_paths and had_existing_database else None
        try:
            with closing(self.connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))"
                    )
                    applied = {
                        row["version"]
                        for row in connection.execute("SELECT version FROM schema_migrations")
                    }
                    for path in migration_paths:
                        version = int(path.stem.split("_", 1)[0])
                        if version in applied:
                            continue
                        self._execute_migration_script(connection, path.read_text(encoding="utf-8"))
                        connection.execute(
                            "INSERT INTO schema_migrations(version) VALUES (?)",
                            (version,),
                        )
                    self._ensure_metadata(connection)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except Exception:
            if recovery_backup is not None:
                failed_copy = self.db_path.with_suffix(self.db_path.suffix + f".{utc_now().replace(':', '-')}.failed")
                if self.db_path.exists():
                    shutil.copy2(self.db_path, failed_copy)
                self._replace_database_file(recovery_backup, self.db_path)
            raise

    def _applied_migration_versions(self, connection: sqlite3.Connection) -> set[int]:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if exists is None:
            return set()
        return {row["version"] for row in connection.execute("SELECT version FROM schema_migrations")}

    def _execute_migration_script(self, connection: sqlite3.Connection, script: str) -> None:
        statement_lines: list[str] = []
        for line in script.splitlines():
            statement_lines.append(line)
            statement = "\n".join(statement_lines).strip()
            if not statement or not sqlite3.complete_statement(statement):
                continue
            connection.execute(statement)
            statement_lines = []
        trailing = "\n".join(statement_lines).strip()
        if trailing:
            raise ValidationError("Migration script ended with an incomplete SQL statement")

    def _create_pre_migration_backup(self) -> Path:
        backup_dir = self.db_path.parent / "backups" / "migrations"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = utc_now().replace(":", "-")
        backup_path = backup_dir / f"{self.db_path.stem}-pre-migration-{timestamp}.sqlite3"
        with closing(sqlite3.connect(self.db_path)) as source, closing(sqlite3.connect(backup_path)) as destination:
            source.backup(destination)
        return backup_path

    def _replace_database_file(self, source: Path, target: Path) -> None:
        for sidecar in self._sqlite_sidecars(target):
            sidecar.unlink(missing_ok=True)
        shutil.copy2(source, target)
        for sidecar in self._sqlite_sidecars(target):
            sidecar.unlink(missing_ok=True)

    def _sqlite_sidecars(self, database: Path) -> list[Path]:
        return [
            database.with_name(database.name + suffix)
            for suffix in ("-wal", "-shm", "-journal")
        ]
    def integrity_check(self) -> None:
        with closing(self.connect()) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise ValidationError(f"SQLite integrity check failed: {result}")
            connection.execute("BEGIN")
            connection.execute("SELECT COUNT(*) FROM products").fetchone()
            connection.rollback()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def instance(self) -> dict[str, Any]:
        self.migrate()
        with closing(self.connect()) as connection:
            return {
                "instance_id": self._metadata(connection, "instance_id"),
                "api_version": "v1",
                "schema_version": CURRENT_SCHEMA_VERSION,
                "state_revision": int(self._metadata(connection, "state_revision")),
                "capabilities": [
                    "sqlite_core",
                    "legacy_import",
                    "inventory_lots",
                    "events",
                    "shopping_lifecycle",
                    "purchase_ledger",
                    "cooking_sessions",
                    "leftovers",
                    "waste_metrics",
                    "location_value",
                    "barcode_mapping",
                    "receipt_review",
                    "price_history",
                ],
            }

    def add_inventory_lot(self, data: dict[str, Any], *, source: str = "api") -> dict[str, Any]:
        self.migrate()
        with self.transaction() as connection:
            product_id = self.ensure_product(
                connection,
                name=str(data["name"]),
                default_unit=str(data.get("unit") or "count"),
                minimum_stock_quantity=data.get("minimum_stock"),
                minimum_stock_unit=data.get("unit"),
                barcode=data.get("barcode"),
            )
            location_id = self.ensure_location_path(connection, data.get("location"))
            lot_id = self._insert_lot(connection, product_id, location_id, data)
            revision = self._append_event(
                connection,
                "ADD",
                product_id=product_id,
                lot_id=lot_id,
                quantity=str(data.get("quantity", "0")),
                unit=str(data.get("unit") or "count"),
                to_location_id=location_id,
                source=source,
            )
            return {"lot": self.get_lot(connection, lot_id), "revision": revision}

    def consume_product(
        self,
        *,
        product_id: str | None = None,
        product_name: str | None = None,
        quantity: str,
        unit: str,
        source: str = "api",
        reason: str | None = None,
    ) -> dict[str, Any]:
        self.migrate()
        amount = require_positive(quantity)
        wanted_unit = unit_code(unit)
        with self.transaction() as connection:
            product = self._find_product(connection, product_id, product_name)
            lots = self._usable_lots(connection, product["id"])
            available = sum(
                convert(require_non_negative(lot["quantity"]), lot["unit"], wanted_unit)
                for lot in lots
            )
            if available < amount:
                raise InsufficientInventoryError(decimal_text(amount), decimal_text(available), wanted_unit)

            remaining = amount
            allocations: list[dict[str, str]] = []
            revision = int(self._metadata(connection, "state_revision"))
            for lot in lots:
                if remaining <= 0:
                    break
                lot_quantity = require_non_negative(lot["quantity"])
                lot_available = convert(lot_quantity, lot["unit"], wanted_unit)
                take_wanted = min(remaining, lot_available)
                take_lot_unit = convert(take_wanted, wanted_unit, lot["unit"])
                new_quantity = lot_quantity - take_lot_unit
                status = "closed" if new_quantity == 0 else lot["status"]
                connection.execute(
                    "UPDATE inventory_lots SET quantity = ?, status = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                    (decimal_text(new_quantity), status, utc_now(), lot["id"]),
                )
                revision = self._append_event(
                    connection,
                    "CONSUME",
                    product_id=product["id"],
                    lot_id=lot["id"],
                    quantity=decimal_text(take_lot_unit),
                    unit=lot["unit"],
                    reason=reason,
                    source=source,
                )
                allocations.append({"lot_id": lot["id"], "quantity": decimal_text(take_lot_unit), "unit": lot["unit"]})
                remaining -= take_wanted
            return {"allocations": allocations, "revision": revision}

    def discard_lot(self, lot_id: str, *, reason: str = "api discard", source: str = "api") -> dict[str, Any]:
        self.migrate()
        with self.transaction() as connection:
            lot = connection.execute("SELECT * FROM inventory_lots WHERE id = ?", (lot_id,)).fetchone()
            if lot is None:
                raise NotFoundError(f"Inventory lot not found: {lot_id}")
            if lot["status"] != "active":
                raise ValidationError(f"Inventory lot is not active: {lot_id}")
            discarded_quantity = require_non_negative(lot["quantity"])
            discarded_value = self._lot_value_for_quantity(connection, lot, discarded_quantity)
            connection.execute(
                "UPDATE inventory_lots SET quantity = '0', status = 'discarded', updated_at = ?, version = version + 1 WHERE id = ?",
                (utc_now(), lot_id),
            )
            revision = self._append_event(
                connection,
                "DISCARD",
                product_id=lot["product_id"],
                lot_id=lot_id,
                quantity=decimal_text(discarded_quantity),
                unit=lot["unit"],
                reason=reason,
                source=source,
                metadata={
                    "currency": lot["currency"],
                    "waste_value": self._money_text(discarded_value),
                    "location_id": lot["location_id"],
                },
            )
        return {
            "ok": True,
            "revision": revision,
            "discarded_value": self._money_text(discarded_value),
            "currency": lot["currency"],
        }

    def update_recipe(self, recipe_id: str, data: dict[str, Any], *, source: str = "api") -> dict[str, Any]:
        self.migrate()
        ingredients = data.get("ingredients", [])
        if not isinstance(ingredients, list):
            raise ValidationError("Recipe ingredients must be a list")
        with self.transaction() as connection:
            existing = connection.execute("SELECT * FROM recipes WHERE id = ? AND active = 1", (recipe_id,)).fetchone()
            if existing is None:
                raise NotFoundError(f"Recipe not found: {recipe_id}")
            name = str(data.get("name") or "").strip()
            if not name:
                raise ValidationError("Recipe name is required")
            normalized = normalize_name(name)
            conflict = connection.execute(
                "SELECT id FROM recipes WHERE normalized_name = ? AND id != ?",
                (normalized, recipe_id),
            ).fetchone()
            if conflict is not None:
                raise ValidationError(f"Recipe name already exists: {name}")
            yield_servings = decimal_text(require_positive(data.get("yield_servings", existing["yield_servings"]), "yield_servings"))
            tags_json = json.dumps(data.get("tags", json.loads(existing["tags_json"] or "[]")), sort_keys=True)
            connection.execute(
                """
                UPDATE recipes
                SET name = ?, normalized_name = ?, yield_servings = ?, prep_minutes = ?,
                    instructions = ?, tags_json = ?, updated_at = ?, version = version + 1
                WHERE id = ?
                """,
                (
                    name,
                    normalized,
                    yield_servings,
                    data.get("prep_minutes"),
                    data.get("instructions"),
                    tags_json,
                    utc_now(),
                    recipe_id,
                ),
            )
            connection.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
            for position, ingredient in enumerate(ingredients):
                product = connection.execute(
                    "SELECT id FROM products WHERE normalized_name = ? AND active = 1",
                    (normalize_name(ingredient["name"]),),
                ).fetchone()
                self._insert_recipe_ingredient(connection, recipe_id, ingredient, product["id"] if product else None, position)
            revision = self._append_event(connection, "recipe.changed", source=source, metadata={"recipe_id": recipe_id})
            return {"recipe": self._recipe_snapshot(connection, recipe_id), "revision": revision}

    def delete_recipe(self, recipe_id: str, *, source: str = "api") -> dict[str, Any]:
        self.migrate()
        with self.transaction() as connection:
            recipe = connection.execute("SELECT * FROM recipes WHERE id = ? AND active = 1", (recipe_id,)).fetchone()
            if recipe is None:
                raise NotFoundError(f"Recipe not found: {recipe_id}")
            active_plan = connection.execute(
                "SELECT id FROM meal_plan_entries WHERE recipe_id = ? AND status IN ('planned', 'cooking') LIMIT 1",
                (recipe_id,),
            ).fetchone()
            if active_plan is not None:
                raise ValidationError("Recipe is used by an active meal plan")
            connection.execute(
                "UPDATE recipes SET active = 0, updated_at = ?, version = version + 1 WHERE id = ?",
                (utc_now(), recipe_id),
            )
            revision = self._append_event(connection, "recipe.deleted", source=source, metadata={"recipe_id": recipe_id})
        return {"ok": True, "recipe_id": recipe_id, "revision": revision}

    def backup(self, output_path: Path | str) -> Path:
        self.migrate()
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
        return target

    def backup_archive(self, output_path: Path | str) -> dict[str, Any]:
        self.migrate()
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_db = target.with_name(f".{target.name}.{uuid.uuid4().hex}.sqlite3")
        try:
            self.backup(temp_db)
            instance = self.instance()
            receipt_entries = self._backup_receipt_entries(temp_db)
            db_entry = {
                "path": BACKUP_ARCHIVE_DB,
                "sha256": self._sha256_file(temp_db),
                "size_bytes": temp_db.stat().st_size,
            }
            manifest = {
                "format": "pantryos-backup-archive",
                "format_version": 1,
                "created_at": utc_now(),
                "schema_version": instance["schema_version"],
                "state_revision": instance["state_revision"],
                "database": db_entry,
                "receipt_uploads": [
                    {
                        "id": entry["id"],
                        "content_hash": entry["content_hash"],
                        "original_filename": entry["original_filename"],
                        "mime_type": entry["mime_type"],
                        "path": entry["archive_path"],
                        "sha256": entry["sha256"],
                        "size_bytes": entry["size_bytes"],
                    }
                    for entry in receipt_entries
                ],
            }
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(temp_db, BACKUP_ARCHIVE_DB)
                for entry in receipt_entries:
                    archive.write(entry["source_path"], entry["archive_path"])
                archive.writestr(BACKUP_ARCHIVE_MANIFEST, json.dumps(manifest, indent=2, sort_keys=True))
        finally:
            if temp_db.exists():
                temp_db.unlink()
        return {
            "path": str(target),
            "sha256": self._sha256_file(target),
            "schema_version": instance["schema_version"],
            "state_revision": instance["state_revision"],
            "receipt_upload_count": len(receipt_entries),
        }

    def restore(self, backup_path: Path | str) -> None:
        source = Path(backup_path)
        if not source.exists():
            raise NotFoundError(f"Backup not found: {source}")
        rollback = self.db_path.with_suffix(self.db_path.suffix + f".{utc_now().replace(':', '-')}.bak")
        if self.db_path.exists():
            shutil.copy2(self.db_path, rollback)
        temp = self.db_path.with_suffix(".restore.tmp")
        shutil.copy2(source, temp)
        restored = PantryCore(temp)
        restored.migrate()
        restored.integrity_check()
        self._replace_database_file(temp, self.db_path)
        temp.unlink(missing_ok=True)

    def verify_backup_archive(self, backup_path: Path | str) -> dict[str, Any]:
        source = Path(backup_path)
        if not source.exists():
            raise NotFoundError(f"Backup not found: {source}")
        with tempfile.TemporaryDirectory(prefix="pantryos-verify-") as directory:
            temp_root = Path(directory)
            manifest, temp_db, receipts_dir = self._extract_backup_archive(source, temp_root)
            restored = PantryCore(temp_db)
            restored.migrate()
            self._rewrite_restored_receipt_paths(temp_db, manifest, temp_root / "restored-receipts")
            restored.integrity_check()
            self._verify_extracted_receipts(manifest, receipts_dir)
            instance = restored.instance()
        return {
            "schema_version": instance["schema_version"],
            "state_revision": instance["state_revision"],
            "receipt_upload_count": len(manifest["receipt_uploads"]),
        }

    def restore_archive(self, backup_path: Path | str) -> None:
        source = Path(backup_path)
        if not source.exists():
            raise NotFoundError(f"Backup not found: {source}")
        timestamp = utc_now().replace(":", "-")
        receipt_dir = self.db_path.parent / "receipts"
        db_rollback = self.db_path.with_suffix(self.db_path.suffix + f".{timestamp}.bak")
        receipt_rollback = self.db_path.parent / f"receipts.{timestamp}.{uuid.uuid4().hex}.bak"
        staging_receipts = self.db_path.parent / f".receipts.restore.{uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory(prefix="pantryos-restore-", dir=self.db_path.parent) as directory:
            temp_root = Path(directory)
            manifest, temp_db, extracted_receipts = self._extract_backup_archive(source, temp_root)
            restored = PantryCore(temp_db)
            restored.migrate()
            self._rewrite_restored_receipt_paths(temp_db, manifest, receipt_dir)
            restored.integrity_check()
            self._verify_extracted_receipts(manifest, extracted_receipts)

            staging_receipts.mkdir(parents=True, exist_ok=True)
            for receipt in manifest["receipt_uploads"]:
                archive_path = str(receipt["path"])
                source_file = extracted_receipts / Path(archive_path).name
                shutil.copy2(source_file, staging_receipts / source_file.name)

            if self.db_path.exists():
                shutil.copy2(self.db_path, db_rollback)
            if receipt_dir.exists():
                shutil.copytree(receipt_dir, receipt_rollback)
            try:
                if receipt_dir.exists():
                    shutil.rmtree(receipt_dir)
                shutil.move(str(staging_receipts), receipt_dir)
                self._replace_database_file(temp_db, self.db_path)
            except Exception:
                if db_rollback.exists():
                    self._replace_database_file(db_rollback, self.db_path)
                if receipt_rollback.exists():
                    if receipt_dir.exists():
                        shutil.rmtree(receipt_dir)
                    shutil.copytree(receipt_rollback, receipt_dir)
                raise
            finally:
                if staging_receipts.exists():
                    shutil.rmtree(staging_receipts)

    def _backup_receipt_entries(self, backup_db: Path) -> list[dict[str, Any]]:
        data_root = self.db_path.parent.resolve()
        with closing(sqlite3.connect(backup_db)) as connection:
            connection.row_factory = sqlite3.Row
            rows = list(
                connection.execute(
                    """
                    SELECT id, content_hash, original_filename, mime_type, storage_path
                    FROM receipt_uploads
                    ORDER BY id
                    """
                )
            )
        entries: list[dict[str, Any]] = []
        archive_paths: set[str] = set()
        for row in rows:
            source_path = Path(str(row["storage_path"]))
            source_path = source_path.resolve() if source_path.is_absolute() else (data_root / source_path).resolve()
            if not source_path.exists():
                raise ValidationError(f"Receipt upload file is missing: {row['id']}")
            try:
                source_path.relative_to(data_root)
            except ValueError as exc:
                raise ValidationError(f"Receipt upload file is outside the PantryOS data directory: {row['id']}") from exc
            suffix = source_path.suffix or SUPPORTED_RECEIPT_MIME_TYPES.get(str(row["mime_type"]), ".txt")
            archive_path = f"{BACKUP_ARCHIVE_RECEIPTS}/{row['content_hash']}{suffix}"
            if archive_path in archive_paths:
                raise ValidationError(f"Receipt upload archive path collision: {row['id']}")
            archive_paths.add(archive_path)
            entries.append(
                {
                    "id": row["id"],
                    "content_hash": row["content_hash"],
                    "original_filename": row["original_filename"],
                    "mime_type": row["mime_type"],
                    "source_path": source_path,
                    "archive_path": archive_path,
                    "sha256": self._sha256_file(source_path),
                    "size_bytes": source_path.stat().st_size,
                }
            )
        return entries

    def _extract_backup_archive(self, source: Path, temp_root: Path) -> tuple[dict[str, Any], Path, Path]:
        try:
            with zipfile.ZipFile(source) as archive:
                manifest = json.loads(archive.read(BACKUP_ARCHIVE_MANIFEST).decode("utf-8"))
                if manifest.get("format") != "pantryos-backup-archive" or manifest.get("format_version") != 1:
                    raise ValidationError("Unsupported PantryOS backup archive format")
                database = manifest.get("database")
                receipts = manifest.get("receipt_uploads")
                if not isinstance(database, dict) or not isinstance(receipts, list):
                    raise ValidationError("PantryOS backup archive manifest is incomplete")
                db_member = str(database.get("path") or "")
                self._validate_archive_member(db_member)
                temp_db = temp_root / BACKUP_ARCHIVE_DB
                temp_db.write_bytes(archive.read(db_member))
                if database.get("sha256") != self._sha256_file(temp_db):
                    raise ValidationError("PantryOS backup database checksum does not match manifest")
                if int(database.get("size_bytes") or -1) != temp_db.stat().st_size:
                    raise ValidationError("PantryOS backup database size does not match manifest")
                receipts_dir = temp_root / BACKUP_ARCHIVE_RECEIPTS
                receipts_dir.mkdir(parents=True, exist_ok=True)
                for receipt in receipts:
                    if not isinstance(receipt, dict):
                        raise ValidationError("PantryOS backup archive contains an invalid receipt entry")
                    archive_path = str(receipt.get("path") or "")
                    self._validate_archive_member(archive_path, expected_prefix=f"{BACKUP_ARCHIVE_RECEIPTS}/")
                    target = receipts_dir / Path(archive_path).name
                    target.write_bytes(archive.read(archive_path))
                    if receipt.get("sha256") != self._sha256_file(target):
                        raise ValidationError(f"Receipt upload checksum does not match manifest: {receipt.get('id')}")
                    if int(receipt.get("size_bytes") or -1) != target.stat().st_size:
                        raise ValidationError(f"Receipt upload size does not match manifest: {receipt.get('id')}")
        except KeyError as exc:
            raise ValidationError("PantryOS backup archive is missing a required file") from exc
        except zipfile.BadZipFile as exc:
            raise ValidationError("PantryOS backup archive is not a valid zip file") from exc
        return manifest, temp_db, receipts_dir

    def _rewrite_restored_receipt_paths(self, db_path: Path, manifest: dict[str, Any], receipt_dir: Path) -> None:
        receipts = manifest["receipt_uploads"]
        receipt_dir.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(db_path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = {
                row["id"]: row
                for row in connection.execute("SELECT id, content_hash FROM receipt_uploads")
            }
            if len(rows) != len(receipts):
                raise ValidationError("PantryOS backup archive does not include every receipt upload")
            connection.execute("BEGIN IMMEDIATE")
            try:
                for receipt in receipts:
                    receipt_id = str(receipt.get("id") or "")
                    row = rows.get(receipt_id)
                    if row is None or row["content_hash"] != receipt.get("content_hash"):
                        raise ValidationError(f"PantryOS backup archive receipt metadata mismatch: {receipt_id}")
                    archive_path = str(receipt["path"])
                    restored_path = receipt_dir / Path(archive_path).name
                    connection.execute(
                        "UPDATE receipt_uploads SET storage_path = ?, updated_at = ? WHERE id = ?",
                        (str(restored_path), utc_now(), receipt_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _verify_extracted_receipts(self, manifest: dict[str, Any], receipts_dir: Path) -> None:
        for receipt in manifest["receipt_uploads"]:
            archive_path = str(receipt["path"])
            target = receipts_dir / Path(archive_path).name
            if not target.exists():
                raise ValidationError(f"Receipt upload file is missing from backup archive: {receipt.get('id')}")
            if receipt.get("sha256") != self._sha256_file(target):
                raise ValidationError(f"Receipt upload checksum does not match manifest: {receipt.get('id')}")

    def _validate_archive_member(self, value: str, *, expected_prefix: str | None = None) -> None:
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts:
            raise ValidationError("PantryOS backup archive contains an unsafe path")
        if expected_prefix is not None and not value.startswith(expected_prefix):
            raise ValidationError("PantryOS backup archive contains an unexpected path")

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def inspect_legacy_json(self, path: Path | str) -> dict[str, Any]:
        source_path = Path(path)
        payload = source_path.read_bytes()
        content_hash = hashlib.sha256(payload).hexdigest()
        data = json.loads(payload.decode("utf-8"))
        self._validate_legacy_document(data)
        return {
            "content_hash": content_hash,
            "item_count": len(data.get("items", [])),
            "recipe_count": len(data.get("recipes", [])),
            "shopping_count": len(data.get("shopping_list", [])),
            "meal_plan_count": len(data.get("meal_plan", {})),
        }
    def import_legacy_json(self, path: Path | str, backup_dir: Path | str | None = None) -> ImportResult:
        self.migrate()
        source_path = Path(path)
        payload = source_path.read_bytes()
        content_hash = hashlib.sha256(payload).hexdigest()
        data = json.loads(payload.decode("utf-8"))
        self._validate_legacy_document(data)
        backup_root = Path(backup_dir) if backup_dir is not None else self.db_path.parent / "backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_path = backup_root / f"legacy-{content_hash[:16]}.json"
        if not backup_path.exists():
            shutil.copy2(source_path, backup_path)

        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM legacy_imports WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            if existing is not None:
                return ImportResult(
                    imported=False,
                    content_hash=content_hash,
                    backup_path=existing["backup_path"],
                    item_count=existing["item_count"],
                    recipe_count=existing["recipe_count"],
                    shopping_count=existing["shopping_count"],
                    meal_plan_count=existing["meal_plan_count"],
                )

            product_by_name: dict[str, str] = {}
            for item in data.get("items", []):
                product_id = self.ensure_product(
                    connection,
                    name=item["name"],
                    default_unit=item.get("unit") or "count",
                    minimum_stock_quantity=item.get("minimum_stock"),
                    minimum_stock_unit=item.get("unit"),
                    barcode=item.get("barcode"),
                )
                product_by_name[normalize_name(item["name"])] = product_id
                location_id = self.ensure_location_path(connection, item.get("location"))
                quantity = require_non_negative(item.get("quantity", "0"))
                if quantity <= 0:
                    continue
                legacy_id = item.get("id")
                exists = None
                if legacy_id:
                    exists = connection.execute(
                        "SELECT id FROM inventory_lots WHERE source_legacy_id = ?",
                        (legacy_id,),
                    ).fetchone()
                if exists is not None:
                    continue
                lot_id = self._insert_lot(
                    connection,
                    product_id,
                    location_id,
                    item,
                    source_legacy_id=legacy_id,
                )
                self._append_event(
                    connection,
                    "IMPORT",
                    product_id=product_id,
                    lot_id=lot_id,
                    quantity=str(item.get("quantity", "0")),
                    unit=str(item.get("unit") or "count"),
                    to_location_id=location_id,
                    source="legacy_json",
                )

            for recipe in data.get("recipes", []):
                recipe_id = self._upsert_recipe(connection, recipe)
                connection.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
                for position, ingredient in enumerate(recipe.get("ingredients", [])):
                    product_id = product_by_name.get(normalize_name(ingredient["name"]))
                    self._insert_recipe_ingredient(connection, recipe_id, ingredient, product_id, position)

            for item in data.get("shopping_list", []):
                source = item.get("source") or "manual"
                source_key = f"legacy:{source}:{normalize_name(item['name'])}:{unit_code(item.get('unit'))}"
                self._upsert_shopping_demand(
                    connection,
                    source_key=source_key,
                    product_id=product_by_name.get(normalize_name(item["name"])),
                    display_name=item["name"],
                    quantity=str(item.get("quantity", "1")),
                    unit=str(item.get("unit") or "count"),
                    source_kind="legacy",
                    source_id=source,
                    accepted=True,
                    checked=bool(item.get("checked", False)),
                )

            for label, recipe_name in data.get("meal_plan", {}).items():
                recipe = connection.execute(
                    "SELECT id FROM recipes WHERE normalized_name = ?",
                    (normalize_name(recipe_name),),
                ).fetchone()
                if recipe is None:
                    continue
                plan_date = date.today().isoformat() if label == "Tonight" else str(label)
                self._upsert_meal_plan(
                    connection,
                    plan_date=plan_date,
                    meal_type=str(label),
                    recipe_id=recipe["id"],
                    servings="1",
                )

            connection.execute(
                """
                INSERT INTO legacy_imports(
                  id, content_hash, source_path, backup_path, item_count,
                  recipe_count, shopping_count, meal_plan_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("import"),
                    content_hash,
                    str(source_path),
                    str(backup_path),
                    len(data.get("items", [])),
                    len(data.get("recipes", [])),
                    len(data.get("shopping_list", [])),
                    len(data.get("meal_plan", {})),
                ),
            )
            self._append_event(
                connection,
                "IMPORT",
                source="legacy_json",
                reason="legacy import completed",
                metadata={"content_hash": content_hash},
            )

        return ImportResult(
            imported=True,
            content_hash=content_hash,
            backup_path=str(backup_path),
            item_count=len(data.get("items", [])),
            recipe_count=len(data.get("recipes", [])),
            shopping_count=len(data.get("shopping_list", [])),
            meal_plan_count=len(data.get("meal_plan", {})),
        )

    def dashboard(self) -> dict[str, Any]:
        self.migrate()
        with closing(self.connect()) as connection:
            return self._dashboard(connection)

    def upload_receipt(self, data: dict[str, Any], *, source: str = "api") -> dict[str, Any]:
        self.migrate()
        mime_type = str(data.get("mime_type") or "text/plain").casefold().strip()
        storage_suffix = SUPPORTED_RECEIPT_MIME_TYPES.get(mime_type)
        if storage_suffix is None:
            raise ValidationError("Unsupported receipt type; supported types are text/plain and text/csv")
        filename = str(data.get("filename") or f"receipt{storage_suffix}").strip()
        if not filename:
            raise ValidationError("Receipt filename is required")
        if filename in {".", ".."} or "/" in filename or "\\" in filename:
            raise ValidationError("Receipt filename must not include a path")
        suffix = Path(filename).suffix.casefold()
        if suffix and suffix != storage_suffix:
            raise ValidationError("Receipt filename extension does not match MIME type")
        raw_text = data.get("text") if "text" in data else data.get("content")
        if not isinstance(raw_text, str):
            raise ValidationError("Receipt content must be text")
        payload = raw_text.encode("utf-8")
        if not payload:
            raise ValidationError("Receipt content is required")
        if "\x00" in raw_text:
            raise ValidationError("Receipt content must be text")
        if len(payload) > MAX_RECEIPT_UPLOAD_BYTES:
            raise ValidationError(f"Receipt upload exceeds {MAX_RECEIPT_UPLOAD_BYTES} bytes")
        if mime_type == "text/csv" and "," not in raw_text:
            raise ValidationError("CSV receipt content must contain comma-separated rows")
        content_hash = hashlib.sha256(payload).hexdigest()
        receipt_dir = (self.db_path.parent / "receipts").resolve()
        receipt_dir.mkdir(parents=True, exist_ok=True)
        storage_path = receipt_dir / f"{content_hash}{storage_suffix}"
        storage_path.write_bytes(payload)
        with self.transaction() as connection:
            existing = connection.execute("SELECT * FROM receipt_uploads WHERE content_hash = ?", (content_hash,)).fetchone()
            if existing is not None:
                return {"receipt": self._receipt_snapshot(existing), "duplicate": True, "revision": int(self._metadata(connection, "state_revision"))}
            receipt_id = new_id("receipt")
            connection.execute(
                """
                INSERT INTO receipt_uploads(id, content_hash, original_filename, mime_type, storage_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (receipt_id, content_hash, filename, mime_type, str(storage_path)),
            )
            revision = self._append_event(
                connection,
                "receipt.uploaded",
                source=source,
                metadata={"receipt_id": receipt_id, "mime_type": mime_type, "size_bytes": len(payload)},
            )
            receipt = self._receipt_row(connection, receipt_id)
        return {"receipt": self._receipt_snapshot(receipt), "duplicate": False, "revision": revision}

    def extract_receipt(self, receipt_id: str, *, source: str = "api") -> dict[str, Any]:
        self.migrate()
        with self.transaction() as connection:
            receipt = self._receipt_row(connection, receipt_id)
            if receipt["status"] == "committed":
                return {
                    "receipt": self._receipt_snapshot(receipt),
                    "review": json.loads(receipt["review_json"] or "{}"),
                    "revision": int(self._metadata(connection, "state_revision")),
                }
            if receipt["status"] == "rejected":
                raise ValidationError("Rejected receipt cannot be extracted")
            text = Path(receipt["storage_path"]).read_text(encoding="utf-8")
            extracted = self._extract_receipt_text(text)
            connection.execute(
                """
                UPDATE receipt_uploads
                SET status = 'review', store = ?, purchased_at = ?, total = ?, currency = ?,
                    extracted_json = ?, review_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    extracted.get("store"),
                    extracted.get("purchased_at"),
                    extracted.get("total"),
                    extracted.get("currency", "USD"),
                    json.dumps(extracted, sort_keys=True),
                    json.dumps(extracted, sort_keys=True),
                    utc_now(),
                    receipt_id,
                ),
            )
            revision = self._append_event(
                connection,
                "receipt.review_ready",
                source=source,
                metadata={"receipt_id": receipt_id, "line_count": len(extracted["items"])},
            )
            updated = self._receipt_row(connection, receipt_id)
        return {"receipt": self._receipt_snapshot(updated), "review": extracted, "revision": revision}

    def receipt_review(self, receipt_id: str) -> dict[str, Any]:
        self.migrate()
        with closing(self.connect()) as connection:
            return self._receipt_snapshot(self._receipt_row(connection, receipt_id))

    def update_receipt_review(self, receipt_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self.migrate()
        review = self._validate_receipt_review(data)
        with self.transaction() as connection:
            receipt = self._receipt_row(connection, receipt_id)
            if receipt["status"] == "committed":
                raise ValidationError("Committed receipt review cannot be changed")
            if receipt["status"] == "rejected":
                raise ValidationError("Rejected receipt review cannot be changed")
            connection.execute(
                """
                UPDATE receipt_uploads
                SET status = 'review', store = ?, purchased_at = ?, total = ?, currency = ?,
                    review_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    review.get("store"),
                    review.get("purchased_at"),
                    review.get("total"),
                    review.get("currency", "USD"),
                    json.dumps(review, sort_keys=True),
                    utc_now(),
                    receipt_id,
                ),
            )
            revision = self._append_event(connection, "receipt.review_updated", source="api", metadata={"receipt_id": receipt_id})
            updated = self._receipt_row(connection, receipt_id)
        return {"receipt": self._receipt_snapshot(updated), "review": review, "revision": revision}

    def commit_receipt(self, receipt_id: str, data: dict[str, Any] | None = None, *, source: str = "api") -> dict[str, Any]:
        self.migrate()
        with self.transaction() as connection:
            receipt = self._receipt_row(connection, receipt_id)
            if receipt["committed_purchase_id"]:
                snapshot = self._purchase_snapshot(connection, receipt["committed_purchase_id"])
                return {**snapshot, "receipt": self._receipt_snapshot(receipt), "duplicate": True, "revision": int(self._metadata(connection, "state_revision"))}
            if receipt["status"] == "rejected":
                raise ValidationError("Rejected receipt cannot be committed")
            review_source = data.get("review") if data else None
            if review_source is None:
                review_source = json.loads(receipt["review_json"] or "{}")
            review = self._validate_receipt_review(review_source)
            purchase_id = new_id("purchase")
            purchased_at = str(review.get("purchased_at") or date.today().isoformat())
            currency = str(review.get("currency") or "USD")
            total_text = None if review.get("total") in (None, "") else decimal_text(require_non_negative(review["total"], "total"))
            connection.execute(
                """
                INSERT INTO purchases(id, store, purchased_at, total, currency, source, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (purchase_id, review.get("store"), purchased_at, total_text, currency, "receipt", f"receipt:{receipt_id}"),
            )
            purchase_lines: list[dict[str, Any]] = []
            created_lots: list[dict[str, Any]] = []
            for item in review["items"]:
                unit = unit_code(str(item.get("unit") or "count"))
                quantity = decimal_text(require_positive(item.get("quantity", "1")))
                line_total_text = decimal_text(require_non_negative(item["total_cost"], "total_cost"))
                product_id = self.ensure_product(connection, name=str(item["name"]), default_unit=unit, barcode=item.get("barcode"))
                line_id = new_id("pline")
                connection.execute(
                    """
                    INSERT INTO purchase_lines(
                      id, purchase_id, product_id, display_name, quantity, unit, total_cost, currency
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (line_id, purchase_id, product_id, item["name"], quantity, unit, line_total_text, currency),
                )
                location_id = self.ensure_location_path(connection, str(item.get("location") or review.get("location") or "Unassigned"))
                lot_id = self._insert_lot(
                    connection,
                    product_id,
                    location_id,
                    {
                        "name": item["name"],
                        "quantity": quantity,
                        "unit": unit,
                        "purchased": purchased_at,
                        "expires": item.get("expires") or item.get("expires_at"),
                        "estimated_cost": line_total_text,
                        "notes": item.get("notes"),
                    },
                    purchase_line_id=line_id,
                )
                line = dict(connection.execute("SELECT * FROM purchase_lines WHERE id = ?", (line_id,)).fetchone())
                self._record_price_history(connection, line, store=review.get("store"), purchased_at=purchased_at)
                purchase_lines.append(line)
                created_lots.append(self.get_lot(connection, lot_id))
            connection.execute(
                """
                UPDATE receipt_uploads
                SET status = 'committed', committed_purchase_id = ?, store = ?, purchased_at = ?,
                    total = ?, currency = ?, review_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (purchase_id, review.get("store"), purchased_at, total_text, currency, json.dumps(review, sort_keys=True), utc_now(), receipt_id),
            )
            revision = self._append_event(
                connection,
                "receipt.committed",
                source=source,
                metadata={"receipt_id": receipt_id, "purchase_id": purchase_id, "line_count": len(purchase_lines)},
            )
            updated_receipt = self._receipt_row(connection, receipt_id)
            purchase = dict(connection.execute("SELECT * FROM purchases WHERE id = ?", (purchase_id,)).fetchone())
        return {"purchase": purchase, "lines": purchase_lines, "lots": created_lots, "receipt": self._receipt_snapshot(updated_receipt), "duplicate": False, "revision": revision}

    def reject_receipt(self, receipt_id: str, *, reason: str = "rejected", source: str = "api") -> dict[str, Any]:
        self.migrate()
        with self.transaction() as connection:
            receipt = self._receipt_row(connection, receipt_id)
            if receipt["status"] == "committed":
                raise ValidationError("Committed receipt cannot be rejected")
            connection.execute("UPDATE receipt_uploads SET status = 'rejected', updated_at = ? WHERE id = ?", (utc_now(), receipt_id))
            revision = self._append_event(connection, "receipt.rejected", reason=reason, source=source, metadata={"receipt_id": receipt_id})
            updated = self._receipt_row(connection, receipt_id)
        return {"receipt": self._receipt_snapshot(updated), "revision": revision}

    def purchases(self) -> list[dict[str, Any]]:
        self.migrate()
        with closing(self.connect()) as connection:
            return [self._purchase_snapshot(connection, row["id"])["purchase"] for row in connection.execute("SELECT id FROM purchases ORDER BY purchased_at DESC, created_at DESC")]

    def purchase(self, purchase_id: str) -> dict[str, Any]:
        self.migrate()
        with closing(self.connect()) as connection:
            return self._purchase_snapshot(connection, purchase_id)

    def update_product(self, product_id: str, data: dict[str, Any], *, source: str = "api") -> dict[str, Any]:
        self.migrate()
        with self.transaction() as connection:
            existing = connection.execute("SELECT * FROM products WHERE id = ? AND active = 1", (product_id,)).fetchone()
            if existing is None:
                raise NotFoundError(f"Product not found: {product_id}")

            category = existing["category"]
            default_unit = existing["default_unit"]
            minimum_stock_quantity = existing["minimum_stock_quantity"]
            minimum_stock_unit = existing["minimum_stock_unit"]
            preferred_location_id = existing["preferred_location_id"]
            default_shelf_life_days = existing["default_shelf_life_days"]
            opened_shelf_life_days = existing["opened_shelf_life_days"]

            def nullable_text(key: str, current: str | None) -> str | None:
                if key not in data:
                    return current
                value = data[key]
                if value is None:
                    return None
                text = str(value).strip()
                return text or None

            def nullable_days(key: str, current: int | None) -> int | None:
                if key not in data:
                    return current
                value = data[key]
                if value is None or str(value).strip() == "":
                    return None
                try:
                    days = int(value)
                except (TypeError, ValueError) as exc:
                    raise ValidationError(f"{key} must be an integer") from exc
                if days < 0:
                    raise ValidationError(f"{key} must be non-negative")
                return days

            category = nullable_text("category", category)
            if "default_unit" in data:
                default_unit_text = str(data["default_unit"]).strip()
                if not default_unit_text:
                    raise ValidationError("default_unit is required")
                default_unit = unit_code(default_unit_text)
            minimum_key = "minimum_stock_quantity" if "minimum_stock_quantity" in data else "minimum_stock"
            if minimum_key in data:
                minimum_value = data[minimum_key]
                if minimum_value is None or str(minimum_value).strip() == "":
                    minimum_stock_quantity = None
                    minimum_stock_unit = None
                else:
                    minimum_stock_quantity = decimal_text(require_non_negative(minimum_value, "minimum_stock_quantity"))
                    minimum_stock_unit = unit_code(str(data.get("minimum_stock_unit") or minimum_stock_unit or default_unit))
            elif "minimum_stock_unit" in data and minimum_stock_quantity is not None:
                minimum_stock_unit = unit_code(str(data.get("minimum_stock_unit") or default_unit))
            preferred_location = nullable_text("preferred_location", None)
            if preferred_location is not None:
                preferred_location_id = self.ensure_location_path(connection, preferred_location)
            elif "preferred_location" in data or data.get("preferred_location_id") is None and "preferred_location_id" in data:
                preferred_location_id = None
            elif "preferred_location_id" in data:
                location_id = str(data["preferred_location_id"]).strip()
                location = connection.execute("SELECT id FROM locations WHERE id = ? AND active = 1", (location_id,)).fetchone()
                if location is None:
                    raise NotFoundError(f"Location not found: {location_id}")
                preferred_location_id = location["id"]
            default_shelf_life_days = nullable_days("default_shelf_life_days", default_shelf_life_days)
            opened_shelf_life_days = nullable_days("opened_shelf_life_days", opened_shelf_life_days)

            connection.execute(
                """
                UPDATE products
                SET category = ?, default_unit = ?, minimum_stock_quantity = ?, minimum_stock_unit = ?,
                    preferred_location_id = ?, default_shelf_life_days = ?, opened_shelf_life_days = ?,
                    updated_at = ?, version = version + 1
                WHERE id = ?
                """,
                (
                    category,
                    default_unit,
                    minimum_stock_quantity,
                    minimum_stock_unit,
                    preferred_location_id,
                    default_shelf_life_days,
                    opened_shelf_life_days,
                    utc_now(),
                    product_id,
                ),
            )
            revision = self._append_event(
                connection,
                "product.changed",
                product_id=product_id,
                source=source,
                metadata={"fields": sorted(data.keys())},
            )
            product = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            return {"product": dict(product), "revision": revision}
    def product_prices(self, product_id: str) -> dict[str, Any]:
        self.migrate()
        with closing(self.connect()) as connection:
            product = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            if product is None:
                raise NotFoundError(f"Product not found: {product_id}")
            rows = [dict(row) for row in connection.execute("SELECT * FROM price_history WHERE product_id = ? ORDER BY purchased_at DESC, created_at DESC", (product_id,))]
        return {"product": dict(product), "prices": rows, "analysis": self._price_history_analysis(rows)}

    def events(self, *, limit: int = 25, after_revision: int | None = None) -> dict[str, Any]:
        self.migrate()
        bounded_limit = max(1, min(int(limit), 100))
        with closing(self.connect()) as connection:
            if after_revision is None:
                fetched = connection.execute(
                    "SELECT * FROM inventory_events ORDER BY revision DESC LIMIT ?",
                    (bounded_limit,),
                ).fetchall()
                fetched = list(reversed(fetched))
            else:
                fetched = connection.execute(
                    "SELECT * FROM inventory_events WHERE revision > ? ORDER BY revision ASC LIMIT ?",
                    (int(after_revision), bounded_limit),
                ).fetchall()
            rows = [self._event_snapshot(row) for row in fetched]
            revision = int(self._metadata(connection, "state_revision"))
        return {"items": rows, "revision": revision, "limit": bounded_limit}

    def event(self, event_id: str) -> dict[str, Any]:
        self.migrate()
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT * FROM inventory_events WHERE id = ?", (event_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"Event not found: {event_id}")
            return self._event_snapshot(row)
    def resolve_barcode(self, barcode: str) -> dict[str, Any]:
        self.migrate()
        barcode_text = self._normalize_barcode(barcode)
        with closing(self.connect()) as connection:
            mapping = self._barcode_mapping_row(connection, barcode_text)
            if mapping is None:
                return {"matched": False, "barcode": barcode_text}
            return {"matched": True, "barcode": barcode_text, "mapping": self._barcode_mapping_snapshot(mapping)}

    def save_barcode_mapping(self, data: dict[str, Any], *, source: str = "api") -> dict[str, Any]:
        self.migrate()
        barcode_text = self._normalize_barcode(str(data["barcode"]))
        with self.transaction() as connection:
            product_id = data.get("product_id")
            if product_id is not None:
                product = connection.execute("SELECT * FROM products WHERE id = ?", (str(product_id),)).fetchone()
                if product is None:
                    raise NotFoundError(f"Product not found: {product_id}")
                product_id = product["id"]
                default_unit = product["default_unit"]
            else:
                default_unit = str(data.get("default_unit") or data.get("package_unit") or "count")
                product_id = self.ensure_product(connection, name=str(data["name"]), default_unit=default_unit)
            package_quantity = data.get("package_quantity", data.get("quantity", "1"))
            package_unit = unit_code(str(data.get("package_unit") or data.get("unit") or default_unit))
            existing = self._barcode_mapping_row(connection, barcode_text)
            if existing is not None and existing["product_id"] != product_id:
                raise ValidationError("Barcode is already mapped to another product")
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO product_barcodes(
                      id, barcode, product_id, package_quantity, package_unit,
                      brand, size_text, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("barcode"),
                        barcode_text,
                        product_id,
                        decimal_text(require_positive(package_quantity, "package_quantity")),
                        package_unit,
                        data.get("brand"),
                        data.get("size_text"),
                        source,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE product_barcodes
                    SET package_quantity = ?, package_unit = ?, brand = ?, size_text = ?, source = ?
                    WHERE barcode = ?
                    """,
                    (
                        decimal_text(require_positive(package_quantity, "package_quantity")),
                        package_unit,
                        data.get("brand"),
                        data.get("size_text"),
                        source,
                        barcode_text,
                    ),
                )
            revision = self._append_event(
                connection,
                "barcode.mapped",
                product_id=product_id,
                source=source,
                metadata={"barcode": barcode_text},
            )
            mapping = self._barcode_mapping_row(connection, barcode_text)
            assert mapping is not None
        return {"mapping": self._barcode_mapping_snapshot(mapping), "revision": revision}

    def add_lot_from_barcode(self, barcode: str, data: dict[str, Any], *, source: str = "api") -> dict[str, Any]:
        self.migrate()
        barcode_text = self._normalize_barcode(barcode)
        with self.transaction() as connection:
            mapping = self._barcode_mapping_row(connection, barcode_text)
            if mapping is None:
                raise NotFoundError(f"Unknown barcode: {barcode_text}")
            quantity = data.get("quantity", mapping["package_quantity"] or "1")
            unit = data.get("unit", mapping["package_unit"] or mapping["default_unit"])
            location_id = self.ensure_location_path(connection, data.get("location"))
            lot_id = self._insert_lot(
                connection,
                mapping["product_id"],
                location_id,
                {
                    "name": mapping["product_name"],
                    "quantity": quantity,
                    "unit": unit,
                    "purchased": data.get("purchased") or data.get("acquired_at"),
                    "expires": data.get("expires") or data.get("expires_at"),
                    "estimated_cost": data.get("estimated_cost"),
                    "notes": data.get("notes"),
                },
            )
            revision = self._append_event(
                connection,
                "ADD",
                product_id=mapping["product_id"],
                lot_id=lot_id,
                quantity=str(quantity),
                unit=str(unit),
                to_location_id=location_id,
                source=source,
                metadata={"barcode": barcode_text, "barcode_mapping_id": mapping["id"]},
            )
            lot = self.get_lot(connection, lot_id)
            mapping_snapshot = self._barcode_mapping_snapshot(mapping)
        return {"lot": lot, "mapping": mapping_snapshot, "revision": revision}

    def start_cooking_session(self, data: dict[str, Any]) -> dict[str, Any]:
        self.migrate()
        with self.transaction() as connection:
            recipe = self._find_recipe(connection, data.get("recipe_id"), data.get("recipe_name"))
            planned_servings = decimal_text(require_positive(data.get("planned_servings", data.get("servings", "1")), "servings"))
            meal_plan_entry_id = data.get("meal_plan_entry_id")
            if meal_plan_entry_id is not None:
                meal_plan = connection.execute("SELECT id FROM meal_plan_entries WHERE id = ?", (meal_plan_entry_id,)).fetchone()
                if meal_plan is None:
                    raise NotFoundError(f"Unknown meal plan entry: {meal_plan_entry_id}")
            session_id = new_id("cook")
            connection.execute(
                """
                INSERT INTO cooking_sessions(
                  id, recipe_id, meal_plan_entry_id, planned_servings,
                  ha_correlation_id, notes
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, recipe["id"], meal_plan_entry_id, planned_servings, data.get("ha_correlation_id"), data.get("notes")),
            )
            if meal_plan_entry_id is not None:
                connection.execute(
                    "UPDATE meal_plan_entries SET status = 'cooking', updated_at = ?, version = version + 1 WHERE id = ?",
                    (utc_now(), meal_plan_entry_id),
                )
            revision = self._append_event(
                connection,
                "cooking.started",
                source="api",
                metadata={"cooking_session_id": session_id, "recipe_id": recipe["id"]},
            )
            session = self._cooking_session_snapshot(connection, session_id)
        return {"session": session, "revision": revision}

    def complete_cooking_session(self, session_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self.migrate()
        with self.transaction() as connection:
            session = self._cooking_session_row(connection, session_id)
            if session["status"] != "cooking":
                raise ValidationError("Cooking session is not active")
            allocations = data.get("allocations")
            if not isinstance(allocations, list) or not allocations:
                raise ValidationError("Cooking completion requires confirmed allocations")
            applied_allocations: list[dict[str, str]] = []
            for allocation in allocations:
                if not isinstance(allocation, dict):
                    raise ValidationError("Cooking allocations must be objects")
                lot_id = str(allocation["lot_id"])
                amount = require_positive(allocation["quantity"])
                requested_unit = unit_code(str(allocation.get("unit") or "count"))
                lot = connection.execute("SELECT * FROM inventory_lots WHERE id = ?", (lot_id,)).fetchone()
                if lot is None:
                    raise NotFoundError(f"Unknown inventory lot: {lot_id}")
                if lot["status"] != "active":
                    raise ValidationError(f"Inventory lot is not active: {lot_id}")
                take_lot_unit = convert(amount, requested_unit, lot["unit"])
                available = require_non_negative(lot["quantity"])
                if take_lot_unit > available:
                    raise InsufficientInventoryError(decimal_text(take_lot_unit), decimal_text(available), lot["unit"])
                remaining = available - take_lot_unit
                status = "closed" if remaining == 0 else "active"
                connection.execute(
                    "UPDATE inventory_lots SET quantity = ?, status = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                    (decimal_text(remaining), status, utc_now(), lot_id),
                )
                self._append_event(
                    connection,
                    "COOK",
                    product_id=lot["product_id"],
                    lot_id=lot_id,
                    quantity=decimal_text(take_lot_unit),
                    unit=lot["unit"],
                    reason="cooking allocation",
                    source="api",
                    metadata={"cooking_session_id": session_id},
                )
                applied_allocations.append({"lot_id": lot_id, "quantity": decimal_text(take_lot_unit), "unit": lot["unit"]})

            leftover_lots: list[dict[str, Any]] = []
            for leftover in data.get("leftovers", []):
                if not isinstance(leftover, dict):
                    raise ValidationError("Leftovers must be objects")
                product_id = self.ensure_product(
                    connection,
                    name=str(leftover["name"]),
                    default_unit=str(leftover.get("unit") or "serving"),
                )
                location_id = self.ensure_location_path(connection, str(leftover.get("location") or "Unassigned"))
                lot_id = self._insert_lot(
                    connection,
                    product_id,
                    location_id,
                    {
                        "name": leftover["name"],
                        "quantity": leftover.get("quantity", "1"),
                        "unit": leftover.get("unit") or "serving",
                        "purchased": leftover.get("made_at") or utc_now(),
                        "expires": leftover.get("use_by") or leftover.get("expires_at"),
                        "tags": ["leftover"],
                        "notes": leftover.get("notes"),
                    },
                    cooking_session_id=session_id,
                )
                self._append_event(
                    connection,
                    "LEFTOVER_CREATE",
                    product_id=product_id,
                    lot_id=lot_id,
                    quantity=str(leftover.get("quantity", "1")),
                    unit=str(leftover.get("unit") or "serving"),
                    reason="cooking leftover",
                    source="api",
                    metadata={"cooking_session_id": session_id},
                )
                leftover_lots.append(self.get_lot(connection, lot_id))

            actual_servings = data.get("actual_servings", session["planned_servings"])
            completed_at = utc_now()
            connection.execute(
                """
                UPDATE cooking_sessions
                SET status = 'completed', actual_servings = ?, completed_at = ?,
                    allocations_json = ?, version = version + 1
                WHERE id = ?
                """,
                (decimal_text(require_positive(actual_servings, "actual_servings")), completed_at, json.dumps(applied_allocations), session_id),
            )
            if session["meal_plan_entry_id"]:
                connection.execute(
                    "UPDATE meal_plan_entries SET status = 'completed', updated_at = ?, version = version + 1 WHERE id = ?",
                    (completed_at, session["meal_plan_entry_id"]),
                )
            revision = self._append_event(
                connection,
                "cooking.completed",
                source="api",
                metadata={"cooking_session_id": session_id, "leftover_lot_ids": [lot["id"] for lot in leftover_lots]},
            )
            session_snapshot = self._cooking_session_snapshot(connection, session_id)
        return {"session": session_snapshot, "allocations": applied_allocations, "leftovers": leftover_lots, "revision": revision}

    def cancel_cooking_session(self, session_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        self.migrate()
        with self.transaction() as connection:
            session = self._cooking_session_row(connection, session_id)
            if session["status"] != "cooking":
                raise ValidationError("Cooking session is not active")
            cancelled_at = utc_now()
            connection.execute(
                "UPDATE cooking_sessions SET status = 'cancelled', cancelled_at = ?, notes = COALESCE(?, notes), version = version + 1 WHERE id = ?",
                (cancelled_at, (data or {}).get("reason"), session_id),
            )
            if session["meal_plan_entry_id"]:
                connection.execute(
                    "UPDATE meal_plan_entries SET status = 'planned', updated_at = ?, version = version + 1 WHERE id = ?",
                    (cancelled_at, session["meal_plan_entry_id"]),
                )
            revision = self._append_event(
                connection,
                "cooking.cancelled",
                reason=(data or {}).get("reason"),
                source="api",
                metadata={"cooking_session_id": session_id},
            )
            snapshot = self._cooking_session_snapshot(connection, session_id)
        return {"session": snapshot, "revision": revision}

    def cooking_session(self, session_id: str) -> dict[str, Any]:
        self.migrate()
        with closing(self.connect()) as connection:
            return self._cooking_session_snapshot(connection, session_id)
    def shopping_items(self) -> list[dict[str, Any]]:
        self.migrate()
        with closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM shopping_demands ORDER BY display_name, unit")]

    def update_shopping_item(self, demand_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self.migrate()
        with self.transaction() as connection:
            self._shopping_row(connection, demand_id)
            updates: list[str] = []
            values: list[Any] = []
            if "name" in data:
                name = str(data["name"]).strip()
                if not name:
                    raise ValidationError("Shopping item name is required")
                updates.append("display_name = ?")
                values.append(name)
            if "quantity" in data:
                updates.append("quantity = ?")
                values.append(decimal_text(require_positive(data["quantity"])))
            if "unit" in data:
                updates.append("unit = ?")
                values.append(unit_code(str(data["unit"])))
            if "note" in data:
                updates.append("note = ?")
                values.append(data["note"])
            if "store" in data:
                updates.append("store = ?")
                values.append(data["store"])
            if "status" in data:
                status = str(data["status"])
                if status not in {"active", "suppressed", "removed"}:
                    raise ValidationError("Shopping status must be active, suppressed, or removed")
                updates.append("status = ?")
                values.append(status)
                if status == "suppressed":
                    updates.append("accepted = 0")
            if not updates:
                row = self._shopping_row(connection, demand_id)
                return {"item": dict(row), "revision": int(self._metadata(connection, "state_revision"))}
            updates.append("recalculated_at = ?")
            values.append(utc_now())
            values.append(demand_id)
            connection.execute(f"UPDATE shopping_demands SET {', '.join(updates)} WHERE id = ?", values)
            row = self._shopping_row(connection, demand_id)
            revision = self._append_event(
                connection,
                "shopping.updated",
                product_id=row["product_id"],
                quantity=row["quantity"],
                unit=row["unit"],
                reason="shopping item updated",
                source="api",
                metadata={"shopping_demand_id": demand_id},
            )
        return {"item": dict(row), "revision": revision}

    def set_shopping_checked(self, demand_id: str, checked: bool) -> dict[str, Any]:
        self.migrate()
        with self.transaction() as connection:
            self._shopping_row(connection, demand_id)
            connection.execute(
                "UPDATE shopping_demands SET checked = ?, recalculated_at = ? WHERE id = ?",
                (1 if checked else 0, utc_now(), demand_id),
            )
            row = self._shopping_row(connection, demand_id)
            revision = self._append_event(
                connection,
                "shopping.checked" if checked else "shopping.unchecked",
                product_id=row["product_id"],
                quantity=row["quantity"],
                unit=row["unit"],
                source="api",
                metadata={"shopping_demand_id": demand_id},
            )
        return {"item": dict(row), "revision": revision}

    def remove_shopping_item(self, demand_id: str, *, status: str = "removed") -> dict[str, Any]:
        self.migrate()
        if status not in {"removed", "suppressed"}:
            raise ValidationError("Removed shopping status must be removed or suppressed")
        with self.transaction() as connection:
            row = self._shopping_row(connection, demand_id)
            connection.execute(
                "UPDATE shopping_demands SET status = ?, accepted = 0, checked = 0, recalculated_at = ? WHERE id = ?",
                (status, utc_now(), demand_id),
            )
            revision = self._append_event(
                connection,
                "shopping.removed" if status == "removed" else "shopping.suppressed",
                product_id=row["product_id"],
                quantity=row["quantity"],
                unit=row["unit"],
                source="api",
                metadata={"shopping_demand_id": demand_id},
            )
        return {"ok": True, "revision": revision}

    def complete_purchase(self, data: dict[str, Any]) -> dict[str, Any]:
        self.migrate()
        with self.transaction() as connection:
            items = data.get("items")
            if items is None:
                rows = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT * FROM shopping_demands WHERE status = 'active' AND checked = 1 ORDER BY display_name, unit"
                    )
                ]
                items = [{"shopping_id": row["id"]} for row in rows]
            if not isinstance(items, list) or not items:
                raise ValidationError("Purchase completion requires at least one item")
            purchase_id = new_id("purchase")
            purchased_at = str(data.get("purchased_at") or date.today().isoformat())
            total = data.get("total")
            total_text = None if total in (None, "") else decimal_text(require_non_negative(total, "total"))
            currency = str(data.get("currency") or "USD")
            connection.execute(
                """
                INSERT INTO purchases(id, store, purchased_at, total, currency, source, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (purchase_id, data.get("store"), purchased_at, total_text, currency, data.get("source") or "api", data.get("notes")),
            )
            created_lots: list[dict[str, Any]] = []
            purchase_lines: list[dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    raise ValidationError("Purchase items must be objects")
                demand = self._shopping_row(connection, str(item["shopping_id"]))
                quantity = decimal_text(require_positive(item.get("quantity", demand["quantity"])))
                unit = unit_code(str(item.get("unit") or demand["unit"]))
                line_total = item.get("total_cost")
                line_total_text = None if line_total in (None, "") else decimal_text(require_non_negative(line_total, "total_cost"))
                product_id = demand["product_id"] or self.ensure_product(connection, name=demand["display_name"], default_unit=unit)
                line_id = new_id("pline")
                connection.execute(
                    """
                    INSERT INTO purchase_lines(
                      id, purchase_id, shopping_demand_id, product_id, display_name,
                      quantity, unit, total_cost, currency
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        line_id,
                        purchase_id,
                        demand["id"],
                        product_id,
                        demand["display_name"],
                        quantity,
                        unit,
                        line_total_text,
                        currency,
                    ),
                )
                location_id = self.ensure_location_path(connection, str(item.get("location") or data.get("location") or "Unassigned"))
                lot_id = self._insert_lot(
                    connection,
                    product_id,
                    location_id,
                    {
                        "name": demand["display_name"],
                        "quantity": quantity,
                        "unit": unit,
                        "purchased": purchased_at,
                        "estimated_cost": line_total_text,
                        "notes": item.get("notes"),
                    },
                    purchase_line_id=line_id,
                )
                connection.execute(
                    "UPDATE shopping_demands SET checked = 1, status = 'completed', recalculated_at = ? WHERE id = ?",
                    (utc_now(), demand["id"]),
                )
                line = dict(connection.execute("SELECT * FROM purchase_lines WHERE id = ?", (line_id,)).fetchone())
                self._record_price_history(connection, line, store=data.get("store"), purchased_at=purchased_at)
                purchase_lines.append(line)
                created_lots.append(self.get_lot(connection, lot_id))
            revision = self._append_event(
                connection,
                "purchase.completed",
                reason="shopping purchase completed",
                source="api",
                metadata={"purchase_id": purchase_id, "shopping_demand_ids": [item["shopping_demand_id"] for item in purchase_lines]},
            )
            purchase = dict(connection.execute("SELECT * FROM purchases WHERE id = ?", (purchase_id,)).fetchone())
        return {"purchase": purchase, "lines": purchase_lines, "lots": created_lots, "revision": revision}
    def rebuild_shopping_demand(self) -> dict[str, Any]:
        """Rebuild idempotent generated shopping demand from active meal plans."""
        self.migrate()
        today = date.today()
        changed_keys: set[str] = set()
        generated: dict[str, dict[str, Any]] = {}
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT m.id AS meal_plan_id, m.servings, r.name AS recipe_name,
                       r.yield_servings, i.product_id, i.display_text, i.quantity, i.unit
                FROM meal_plan_entries m
                JOIN recipes r ON r.id = m.recipe_id
                JOIN recipe_ingredients i ON i.recipe_id = r.id
                WHERE m.status = 'planned'
                ORDER BY m.plan_date, m.meal_type, i.position
                """
            ).fetchall()
            for row in rows:
                unit = unit_code(row["unit"])
                servings = require_positive(row["servings"], "servings")
                yield_servings = require_positive(row["yield_servings"], "yield_servings")
                required = require_positive(row["quantity"]) * servings / yield_servings
                if row["product_id"]:
                    identity = f"product:{row['product_id']}:{unit}"
                else:
                    identity = f"text:{normalize_name(row['display_text'])}:{unit}"
                entry = generated.setdefault(
                    identity,
                    {
                        "product_id": row["product_id"],
                        "display_name": row["display_text"],
                        "quantity": Decimal("0"),
                        "unit": unit,
                        "sources": [],
                    },
                )
                entry["quantity"] += required
                entry["sources"].append({"meal_plan_id": row["meal_plan_id"], "recipe_name": row["recipe_name"]})

            existing_generated = {
                row["source_key"]
                for row in connection.execute("SELECT source_key FROM shopping_demands WHERE source_kind = 'meal_plan'")
            }
            active_keys: set[str] = set()
            for identity, entry in generated.items():
                source_key = f"meal_plan:{identity}"
                needed = entry["quantity"]
                if entry["product_id"]:
                    available = self._usable_product_quantity(connection, entry["product_id"], entry["unit"], today)
                    needed -= available
                if needed <= 0:
                    continue
                active_keys.add(source_key)
                self._upsert_shopping_demand(
                    connection,
                    source_key=source_key,
                    product_id=entry["product_id"],
                    display_name=entry["display_name"],
                    quantity=decimal_text(needed),
                    unit=entry["unit"],
                    source_kind="meal_plan",
                    source_id="active_plan",
                    accepted=True,
                )
                changed_keys.add(source_key)

            stale_keys = existing_generated - active_keys
            if stale_keys:
                connection.executemany(
                    "UPDATE shopping_demands SET status = 'inactive', recalculated_at = ? WHERE source_key = ?",
                    [(utc_now(), source_key) for source_key in stale_keys],
                )
                changed_keys.update(stale_keys)
            if changed_keys:
                revision = self._append_event(connection, "shopping.rebuilt", source="meal_plan")
            else:
                revision = int(self._metadata(connection, "state_revision"))
            rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM shopping_demands WHERE source_kind = 'meal_plan' ORDER BY display_name, unit"
                )
            ]
        return {"items": rows, "changed_source_keys": sorted(changed_keys), "revision": revision}

    def _usable_product_quantity(
        self,
        connection: sqlite3.Connection,
        product_id: str,
        unit: str,
        today: date,
    ) -> Decimal:
        total = Decimal("0")
        for lot in connection.execute(
            """
            SELECT quantity, unit, expires_at
            FROM inventory_lots
            WHERE product_id = ? AND status = 'active' AND CAST(quantity AS REAL) > 0
            """,
            (product_id,),
        ):
            if lot["expires_at"] and date.fromisoformat(lot["expires_at"][:10]) < today:
                continue
            total += convert(require_non_negative(lot["quantity"]), lot["unit"], unit)
        return total
    def ensure_product(
        self,
        connection: sqlite3.Connection,
        *,
        name: str,
        default_unit: str,
        minimum_stock_quantity: Any = None,
        minimum_stock_unit: Any = None,
        barcode: Any = None,
    ) -> str:
        display_name = str(name).strip()
        if not display_name:
            raise ValidationError("Product name is required")
        default_unit_code = unit_code(default_unit)
        normalized = normalize_name(display_name)
        existing = connection.execute(
            "SELECT id FROM products WHERE normalized_name = ?",
            (normalized,),
        ).fetchone()
        minimum_quantity_text = None
        minimum_unit_code = None
        if minimum_stock_quantity is not None:
            minimum_quantity_text = decimal_text(require_non_negative(minimum_stock_quantity, "minimum_stock"))
            minimum_unit_code = unit_code(str(minimum_stock_unit or default_unit_code))
        if existing is None:
            product_id = new_id("prod")
            connection.execute(
                """
                INSERT INTO products(
                  id, name, normalized_name, default_unit,
                  minimum_stock_quantity, minimum_stock_unit
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (product_id, display_name, normalized, default_unit_code, minimum_quantity_text, minimum_unit_code),
            )
        else:
            product_id = existing["id"]
            if minimum_quantity_text is not None:
                connection.execute(
                    """
                    UPDATE products
                    SET minimum_stock_quantity = ?, minimum_stock_unit = ?,
                        updated_at = ?, version = version + 1
                    WHERE id = ?
                    """,
                    (minimum_quantity_text, minimum_unit_code, utc_now(), product_id),
                )
        if barcode:
            barcode_text = str(barcode).strip()
            if barcode_text:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO product_barcodes(
                      id, barcode, product_id, package_quantity, package_unit
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (new_id("barcode"), barcode_text, product_id, None, None),
                )
        return product_id

    def ensure_location_path(self, connection: sqlite3.Connection, path: str | None) -> str:
        parent_id = None
        location_id = None
        for position, part in enumerate(normalize_location_parts(path)):
            normalized = normalize_name(part)
            if parent_id is None:
                existing = connection.execute(
                    "SELECT id FROM locations WHERE parent_id IS NULL AND normalized_name = ?",
                    (normalized,),
                ).fetchone()
            else:
                existing = connection.execute(
                    "SELECT id FROM locations WHERE parent_id = ? AND normalized_name = ?",
                    (parent_id, normalized),
                ).fetchone()
            if existing is None:
                location_id = new_id("loc")
                connection.execute(
                    "INSERT INTO locations(id, parent_id, name, normalized_name, type) VALUES (?, ?, ?, ?, ?)",
                    (location_id, parent_id, part, normalized, self._infer_location_type(part, position)),
                )
            else:
                location_id = existing["id"]
            parent_id = location_id
        assert location_id is not None
        return location_id

    def get_lot(self, connection: sqlite3.Connection, lot_id: str) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT l.*, p.name AS product_name, p.default_unit, p.minimum_stock_quantity, p.minimum_stock_unit, loc.name AS location_name
            FROM inventory_lots l
            JOIN products p ON p.id = l.product_id
            JOIN locations loc ON loc.id = l.location_id
            WHERE l.id = ?
            """,
            (lot_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Inventory lot not found: {lot_id}")
        lot = dict(row)
        lot["location_path"] = self._location_paths(connection).get(lot["location_id"], lot["location_name"])
        return lot

    def _insert_lot(
        self,
        connection: sqlite3.Connection,
        product_id: str,
        location_id: str,
        data: dict[str, Any],
        *,
        source_legacy_id: str | None = None,
        purchase_line_id: str | None = None,
        cooking_session_id: str | None = None,
    ) -> str:
        quantity = require_non_negative(data.get("quantity", "0"))
        unit = unit_code(str(data.get("unit") or "count"))
        total_cost = data.get("estimated_cost")
        total_cost_text = None
        if total_cost not in (None, ""):
            total_cost_text = decimal_text(require_non_negative(total_cost, "estimated_cost"))
        lot_id = new_id("lot")
        tags = {str(tag).casefold() for tag in data.get("tags", [])}
        lot_type = "leftover" if "leftover" in tags else "grocery"
        connection.execute(
            """
            INSERT INTO inventory_lots(
              id, product_id, quantity, unit, location_id, acquired_at,
              expires_at, opened_at, lot_type, purchase_line_id, cooking_session_id, total_cost, notes, source_legacy_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lot_id,
                product_id,
                decimal_text(quantity),
                unit,
                location_id,
                data.get("purchased") or data.get("acquired_at"),
                data.get("expires") or data.get("expires_at"),
                data.get("opened_at"),
                lot_type,
                purchase_line_id,
                cooking_session_id,
                total_cost_text,
                data.get("notes"),
                source_legacy_id,
            ),
        )
        return lot_id

    def _append_event(
        self,
        connection: sqlite3.Connection,
        event_type: str,
        *,
        product_id: str | None = None,
        lot_id: str | None = None,
        quantity: str | None = None,
        unit: str | None = None,
        from_location_id: str | None = None,
        to_location_id: str | None = None,
        reason: str | None = None,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        current = int(self._metadata(connection, "state_revision"))
        revision = current + 1
        connection.execute("UPDATE app_metadata SET value = ? WHERE key = 'state_revision'", (str(revision),))
        connection.execute(
            """
            INSERT INTO inventory_events(
              id, revision, event_type, product_id, lot_id, quantity, unit,
              from_location_id, to_location_id, reason, source, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("evt"),
                revision,
                event_type,
                product_id,
                lot_id,
                quantity,
                unit_code(unit) if unit else None,
                from_location_id,
                to_location_id,
                reason,
                source,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
        return revision

    def _upsert_recipe(self, connection: sqlite3.Connection, data: dict[str, Any]) -> str:
        name = str(data["name"]).strip()
        if not name:
            raise ValidationError("Recipe name is required")
        normalized = normalize_name(name)
        existing = connection.execute("SELECT id FROM recipes WHERE normalized_name = ?", (normalized,)).fetchone()
        yield_servings = decimal_text(require_positive(data.get("yield_servings", "1"), "yield_servings"))
        tags_json = json.dumps(data.get("tags", []), sort_keys=True)
        if existing is None:
            recipe_id = new_id("recipe")
            connection.execute(
                """
                INSERT INTO recipes(id, name, normalized_name, yield_servings,
                                    prep_minutes, instructions, tags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (recipe_id, name, normalized, yield_servings, data.get("prep_minutes"), data.get("instructions"), tags_json),
            )
        else:
            recipe_id = existing["id"]
            connection.execute(
                """
                UPDATE recipes
                SET name = ?, yield_servings = ?, prep_minutes = ?, instructions = ?,
                    tags_json = ?, updated_at = ?, version = version + 1
                WHERE id = ?
                """,
                (name, yield_servings, data.get("prep_minutes"), data.get("instructions"), tags_json, utc_now(), recipe_id),
            )
        self._append_event(connection, "recipe.changed", source="legacy_json")
        return recipe_id

    def _insert_recipe_ingredient(
        self,
        connection: sqlite3.Connection,
        recipe_id: str,
        data: dict[str, Any],
        product_id: str | None,
        position: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO recipe_ingredients(id, recipe_id, product_id, display_text, quantity, unit, position)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("ingredient"),
                recipe_id,
                product_id,
                data["name"],
                decimal_text(require_positive(data.get("quantity", "1"))),
                unit_code(str(data.get("unit") or "count")),
                position,
            ),
        )

    def _event_snapshot(self, row: sqlite3.Row) -> dict[str, Any]:
        data = json.loads(row["metadata_json"] or "{}")
        for key in ("product_id", "lot_id", "quantity", "unit", "from_location_id", "to_location_id"):
            if row[key] is not None:
                data[key] = row[key]
        return {
            "id": row["id"],
            "revision": row["revision"],
            "type": row["event_type"],
            "event_type": row["event_type"],
            "occurred_at": row["occurred_at"],
            "source": row["source"],
            "reason": row["reason"],
            "data": data,
        }
    def _receipt_row(self, connection: sqlite3.Connection, receipt_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM receipt_uploads WHERE id = ?", (receipt_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Receipt not found: {receipt_id}")
        return row

    def _receipt_snapshot(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "content_hash": row["content_hash"],
            "original_filename": row["original_filename"],
            "mime_type": row["mime_type"],
            "status": row["status"],
            "store": row["store"],
            "purchased_at": row["purchased_at"],
            "total": row["total"],
            "currency": row["currency"],
            "review": json.loads(row["review_json"] or "{}"),
            "committed_purchase_id": row["committed_purchase_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _extract_receipt_text(self, text: str) -> dict[str, Any]:
        review: dict[str, Any] = {"store": None, "purchased_at": None, "total": None, "currency": "USD", "items": []}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            key, separator, value = line.partition(":")
            normalized_key = normalize_name(key)
            if separator and normalized_key in {"store", "date", "purchased", "purchased at", "total", "currency"}:
                value = value.strip()
                if normalized_key == "store":
                    review["store"] = value
                elif normalized_key in {"date", "purchased", "purchased at"}:
                    review["purchased_at"] = date.fromisoformat(value[:10]).isoformat()
                elif normalized_key == "total":
                    review["total"] = decimal_text(require_non_negative(value, "total"))
                elif normalized_key == "currency":
                    review["currency"] = value or "USD"
                continue
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 4:
                continue
            item: dict[str, Any] = {
                "name": parts[0],
                "quantity": decimal_text(require_positive(parts[1])),
                "unit": unit_code(parts[2]),
                "total_cost": decimal_text(require_non_negative(parts[3], "total_cost")),
            }
            if len(parts) >= 5 and parts[4]:
                item["barcode"] = parts[4]
            review["items"].append(item)
        return self._validate_receipt_review(review)

    def _validate_receipt_review(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValidationError("Receipt review must be an object")
        items = data.get("items")
        if not isinstance(items, list) or not items:
            raise ValidationError("Receipt review requires at least one item")
        review: dict[str, Any] = {
            "store": data.get("store"),
            "purchased_at": date.fromisoformat(str(data.get("purchased_at") or date.today().isoformat())[:10]).isoformat(),
            "currency": str(data.get("currency") or "USD"),
            "location": data.get("location"),
            "items": [],
        }
        if data.get("total") not in (None, ""):
            review["total"] = decimal_text(require_non_negative(data["total"], "total"))
        else:
            total = sum(require_non_negative(item.get("total_cost", "0"), "total_cost") for item in items if isinstance(item, dict))
            review["total"] = decimal_text(total)
        for item in items:
            if not isinstance(item, dict):
                raise ValidationError("Receipt review items must be objects")
            name = str(item.get("name") or "").strip()
            if not name:
                raise ValidationError("Receipt item name is required")
            normalized_item: dict[str, Any] = {
                "name": name,
                "quantity": decimal_text(require_positive(item.get("quantity", "1"))),
                "unit": unit_code(str(item.get("unit") or "count")),
                "total_cost": decimal_text(require_non_negative(item.get("total_cost", "0"), "total_cost")),
            }
            for key in ("barcode", "location", "expires", "expires_at", "notes"):
                if item.get(key) not in (None, ""):
                    normalized_item[key] = item[key]
            review["items"].append(normalized_item)
        return review

    def _purchase_snapshot(self, connection: sqlite3.Connection, purchase_id: str) -> dict[str, Any]:
        purchase = connection.execute("SELECT * FROM purchases WHERE id = ?", (purchase_id,)).fetchone()
        if purchase is None:
            raise NotFoundError(f"Purchase not found: {purchase_id}")
        lines = [dict(row) for row in connection.execute("SELECT * FROM purchase_lines WHERE purchase_id = ? ORDER BY created_at, id", (purchase_id,))]
        lots: list[dict[str, Any]] = []
        for line in lines:
            lots.extend(
                self.get_lot(connection, row["id"])
                for row in connection.execute(
                    "SELECT id FROM inventory_lots WHERE purchase_line_id = ? ORDER BY created_at, id",
                    (line["id"],),
                )
            )
        prices = [
            dict(row)
            for row in connection.execute(
                """
                SELECT ph.*
                FROM price_history ph
                JOIN purchase_lines pl ON pl.id = ph.purchase_line_id
                WHERE pl.purchase_id = ?
                ORDER BY ph.created_at, ph.id
                """,
                (purchase_id,),
            )
        ]
        return {"purchase": dict(purchase), "lines": lines, "lots": lots, "prices": prices}

    def _price_history_analysis(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        analysis: dict[str, Any] = {
            "baseline_policy": "recent_median_compatible_unit",
            "evidence_window": "up to 5 prior purchases with the same comparable unit",
            "sample_count": len(rows),
            "latest": None,
        }
        if not rows:
            return analysis

        latest = rows[0]
        prior_compatible = [row for row in rows[1:] if row.get("comparable_unit") == latest.get("comparable_unit")][:5]
        anomaly_ratio = latest.get("anomaly_ratio")
        status = "baseline"
        if anomaly_ratio is not None:
            ratio = Decimal(str(anomaly_ratio))
            if ratio >= Decimal("1.25"):
                status = "high"
            elif ratio <= Decimal("0.75"):
                status = "low"
            else:
                status = "normal"
        analysis["latest"] = {
            "price_id": latest.get("id"),
            "unit_price": latest.get("unit_price"),
            "currency": latest.get("currency"),
            "comparable_unit": latest.get("comparable_unit"),
            "baseline_unit_price": latest.get("baseline_unit_price"),
            "anomaly_ratio": anomaly_ratio,
            "status": status,
            "baseline_sample_count": len(prior_compatible),
            "explanation": latest.get("explanation"),
        }
        return analysis

    def _price_baseline(self, connection: sqlite3.Connection, product_id: str, comparable_unit: str) -> dict[str, Any] | None:
        rows = [
            Decimal(str(row["unit_price"]))
            for row in connection.execute(
                """
                SELECT unit_price
                FROM price_history
                WHERE product_id = ? AND comparable_unit = ?
                ORDER BY purchased_at DESC, created_at DESC
                LIMIT 5
                """,
                (product_id, comparable_unit),
            )
        ]
        if not rows:
            return None
        ordered = sorted(rows)
        midpoint = len(ordered) // 2
        if len(ordered) % 2:
            median = ordered[midpoint]
        else:
            median = (ordered[midpoint - 1] + ordered[midpoint]) / Decimal("2")
        return {
            "unit_price": median,
            "sample_count": len(rows),
            "window": "up to 5 prior purchases with the same comparable unit",
        }

    def _record_price_history(self, connection: sqlite3.Connection, line: dict[str, Any], *, store: Any, purchased_at: str) -> None:
        if line.get("product_id") is None or line.get("total_cost") in (None, ""):
            return
        quantity = require_positive(line["quantity"])
        unit = unit_code(str(line["unit"]))
        total_cost = require_non_negative(line["total_cost"], "total_cost")
        comparable_unit = self._comparable_unit(unit)
        comparable_quantity = convert(quantity, unit, comparable_unit)
        unit_price = total_cost / comparable_quantity
        baseline = self._price_baseline(connection, str(line["product_id"]), comparable_unit)
        baseline_text = None
        anomaly_ratio = None
        if baseline is None:
            explanation = f"Baseline initialized at {self._money_text(unit_price)} per {comparable_unit}."
        else:
            baseline_decimal = baseline["unit_price"]
            baseline_text = self._money_text(baseline_decimal)
            ratio = unit_price / baseline_decimal if baseline_decimal > 0 else Decimal("1")
            anomaly_ratio = decimal_text(ratio.quantize(Decimal("0.01")))
            relation = "within"
            if ratio >= Decimal("1.25"):
                relation = "above"
            elif ratio <= Decimal("0.75"):
                relation = "below"
            plural = "" if baseline["sample_count"] == 1 else "s"
            explanation = (
                f"Current price {self._money_text(unit_price)} per {comparable_unit} is {anomaly_ratio}x "
                f"and {relation} the recent median baseline of {baseline_text} per {comparable_unit} "
                f"from {baseline['sample_count']} compatible prior purchase{plural}; window: {baseline['window']}."
            )
        connection.execute(
            """
            INSERT OR IGNORE INTO price_history(
              id, purchase_line_id, product_id, store, purchased_at, quantity, unit,
              total_cost, currency, comparable_quantity, comparable_unit, unit_price,
              baseline_unit_price, anomaly_ratio, explanation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("price"),
                line["id"],
                line["product_id"],
                store,
                purchased_at,
                line["quantity"],
                unit,
                line["total_cost"],
                line["currency"],
                decimal_text(comparable_quantity),
                comparable_unit,
                self._money_text(unit_price),
                baseline_text,
                anomaly_ratio,
                explanation,
            ),
        )

    def _comparable_unit(self, unit: str) -> str:
        dimension = UNITS[unit_code(unit)].dimension
        if dimension == "mass":
            return "oz"
        if dimension == "volume":
            return "fl oz"
        if dimension == "count":
            return "count"
        return unit_code(unit)

    def _normalize_barcode(self, barcode: str) -> str:
        barcode_text = "".join(str(barcode).strip().split())
        if not barcode_text:
            raise ValidationError("Barcode is required")
        if len(barcode_text) > 128:
            raise ValidationError("Barcode is too long")
        return barcode_text

    def _barcode_mapping_row(self, connection: sqlite3.Connection, barcode: str) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT b.*, p.name AS product_name, p.default_unit
            FROM product_barcodes b
            JOIN products p ON p.id = b.product_id
            WHERE b.barcode = ?
            """,
            (barcode,),
        ).fetchone()

    def _barcode_mapping_snapshot(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "barcode": row["barcode"],
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "package_quantity": row["package_quantity"] or "1",
            "package_unit": row["package_unit"] or row["default_unit"],
            "brand": row["brand"],
            "size_text": row["size_text"],
            "source": row["source"],
        }

    def _find_recipe(
        self,
        connection: sqlite3.Connection,
        recipe_id: str | None,
        recipe_name: str | None,
    ) -> sqlite3.Row:
        if recipe_id:
            row = connection.execute("SELECT * FROM recipes WHERE id = ? AND active = 1", (recipe_id,)).fetchone()
        elif recipe_name:
            row = connection.execute("SELECT * FROM recipes WHERE normalized_name = ? AND active = 1", (normalize_name(str(recipe_name)),)).fetchone()
        else:
            raise ValidationError("Cooking session requires recipe_id or recipe_name")
        if row is None:
            raise NotFoundError("Unknown recipe")
        return row

    def _cooking_session_row(self, connection: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM cooking_sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Unknown cooking session: {session_id}")
        return row

    def _cooking_session_snapshot(self, connection: sqlite3.Connection, session_id: str) -> dict[str, Any]:
        row = self._cooking_session_row(connection, session_id)
        session = dict(row)
        session["allocations"] = json.loads(session.pop("allocations_json") or "[]")
        return session
    def _shopping_row(self, connection: sqlite3.Connection, demand_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM shopping_demands WHERE id = ?", (demand_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Unknown shopping item: {demand_id}")
        return row

    def _upsert_shopping_demand(
        self,
        connection: sqlite3.Connection,
        *,
        source_key: str,
        product_id: str | None,
        display_name: str,
        quantity: str,
        unit: str,
        source_kind: str,
        source_id: str | None,
        accepted: bool,
        checked: bool = False,
    ) -> None:
        connection.execute(
            """
            INSERT INTO shopping_demands(
              id, source_key, product_id, display_name, quantity, unit,
              source_kind, source_id, accepted, checked
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
              quantity = excluded.quantity,
              unit = excluded.unit,
              display_name = excluded.display_name,
              status = 'active',
              accepted = excluded.accepted,
              recalculated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                new_id("demand"),
                source_key,
                product_id,
                display_name,
                decimal_text(require_positive(quantity)),
                unit_code(unit),
                source_kind,
                source_id,
                1 if accepted else 0,
                1 if checked else 0,
            ),
        )
        self._append_event(connection, "shopping.changed", source=source_kind)

    def _upsert_meal_plan(
        self,
        connection: sqlite3.Connection,
        *,
        plan_date: str,
        meal_type: str,
        recipe_id: str,
        servings: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO meal_plan_entries(id, plan_date, meal_type, recipe_id, servings)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(plan_date, meal_type) DO UPDATE SET
              recipe_id = excluded.recipe_id,
              servings = excluded.servings,
              updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
              version = version + 1
            """,
            (new_id("plan"), plan_date, meal_type, recipe_id, decimal_text(require_positive(servings, "servings"))),
        )
        self._append_event(connection, "meal_plan.changed", source="legacy_json")

    def _find_product(
        self,
        connection: sqlite3.Connection,
        product_id: str | None,
        product_name: str | None,
    ) -> sqlite3.Row:
        if product_id:
            row = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        elif product_name:
            row = connection.execute(
                "SELECT * FROM products WHERE normalized_name = ?",
                (normalize_name(product_name),),
            ).fetchone()
        else:
            raise ValidationError("product_id or product_name is required")
        if row is None:
            raise NotFoundError("Product not found")
        return row

    def _usable_lots(self, connection: sqlite3.Connection, product_id: str) -> list[sqlite3.Row]:
        today = date.today().isoformat()
        return list(
            connection.execute(
                """
                SELECT * FROM inventory_lots
                WHERE product_id = ?
                  AND status = 'active'
                  AND CAST(quantity AS REAL) > 0
                  AND (expires_at IS NULL OR expires_at >= ?)
                ORDER BY expires_at IS NULL, expires_at ASC, created_at ASC, id ASC
                """,
                (product_id, today),
            )
        )

    def _dashboard(self, connection: sqlite3.Connection) -> dict[str, Any]:
        revision = int(self._metadata(connection, "state_revision"))
        products = [dict(row) for row in connection.execute("SELECT * FROM products ORDER BY name")]
        location_paths = self._location_paths(connection)
        lots = [dict(row) for row in connection.execute("""
            SELECT l.*, p.name AS product_name, p.minimum_stock_quantity,
                   p.minimum_stock_unit, loc.name AS location_name
            FROM inventory_lots l
            JOIN products p ON p.id = l.product_id
            JOIN locations loc ON loc.id = l.location_id
            ORDER BY p.name, l.expires_at IS NULL, l.expires_at, l.created_at
        """)]
        for lot in lots:
            lot["location_path"] = location_paths.get(lot["location_id"], lot["location_name"])
            lot["estimated_value"] = self._money_text(
                self._lot_value_for_quantity(connection, lot, require_non_negative(lot["quantity"]))
            )
        recipes = [self._recipe_snapshot(connection, row["id"]) for row in connection.execute("SELECT id FROM recipes WHERE active = 1 ORDER BY name")]
        shopping = [dict(row) for row in connection.execute("SELECT * FROM shopping_demands ORDER BY display_name")]
        events = [dict(row) for row in connection.execute("SELECT * FROM inventory_events ORDER BY revision DESC LIMIT 25")]
        location_summary = self._location_summary(lots)
        return {
            "revision": revision,
            "instance_id": self._metadata(connection, "instance_id"),
            "products": products,
            "lots": lots,
            "recipes": recipes,
            "shopping": shopping,
            "events": events,
            "summary": {
                "product_count": len(products),
                "active_lot_count": sum(1 for lot in lots if lot["status"] == "active"),
                "shopping_count": sum(1 for row in shopping if row["status"] == "active" and not row["checked"]),
                "event_count": connection.execute("SELECT COUNT(*) FROM inventory_events").fetchone()[0],
                "food_waste_this_month": self._money_text(self._monthly_waste_value(connection)),
                "location_counts": location_summary["counts"],
                "location_values": location_summary["values"],
                "locations": location_summary["locations"],
            },
        }

    def _location_paths(self, connection: sqlite3.Connection) -> dict[str, str]:
        rows = {row["id"]: dict(row) for row in connection.execute("SELECT id, parent_id, name FROM locations")}
        cache: dict[str, str] = {}

        def resolve(location_id: str, seen: set[str] | None = None) -> str:
            if location_id in cache:
                return cache[location_id]
            seen = set() if seen is None else seen
            if location_id in seen or location_id not in rows:
                return rows.get(location_id, {}).get("name", "Unknown")
            seen.add(location_id)
            row = rows[location_id]
            parent_id = row["parent_id"]
            path = str(row["name"]) if parent_id is None else f"{resolve(parent_id, seen)}/{row['name']}"
            cache[location_id] = path
            return path

        return {location_id: resolve(location_id) for location_id in rows}

    def _location_summary(self, lots: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {"Kitchen": 0, "Refrigerator": 0, "Freezer": 0, "Pantry": 0}
        value_totals = {key: Decimal("0") for key in counts}
        by_location: dict[str, dict[str, Any]] = {}
        for lot in lots:
            if lot["status"] != "active":
                continue
            path = lot.get("location_path") or lot["location_name"]
            estimated_value = require_non_negative(lot.get("estimated_value", "0"), "estimated_value")
            location_row = by_location.setdefault(
                path,
                {
                    "location_id": lot["location_id"],
                    "path": path,
                    "active_lot_count": 0,
                    "inventory_value": "0.00",
                    "_inventory_value": Decimal("0"),
                    "currency": lot["currency"],
                },
            )
            location_row["active_lot_count"] += 1
            location_row["_inventory_value"] += estimated_value
            for bucket in self._location_buckets(path):
                counts[bucket] += 1
                value_totals[bucket] += estimated_value
        values = {key: self._money_text(value) for key, value in value_totals.items()}
        locations = []
        for row in by_location.values():
            row["inventory_value"] = self._money_text(row.pop("_inventory_value"))
            locations.append(row)
        locations.sort(key=lambda row: row["path"])
        return {"counts": counts, "values": values, "locations": locations}

    def _location_buckets(self, path: str) -> list[str]:
        normalized = normalize_name(path)
        buckets = []
        if normalized.startswith("kitchen"):
            buckets.append("Kitchen")
        if "refrigerator" in normalized or "fridge" in normalized:
            buckets.append("Refrigerator")
        if "freezer" in normalized:
            buckets.append("Freezer")
        if "pantry" in normalized:
            buckets.append("Pantry")
        return buckets

    def _monthly_waste_value(self, connection: sqlite3.Connection) -> Decimal:
        month_start = date.today().replace(day=1).isoformat()
        total = Decimal("0")
        rows = connection.execute(
            """
            SELECT e.*, l.total_cost, l.quantity AS lot_quantity, l.unit AS lot_unit, l.currency
            FROM inventory_events e
            LEFT JOIN inventory_lots l ON l.id = e.lot_id
            WHERE e.event_type = 'DISCARD' AND substr(e.occurred_at, 1, 10) >= ?
            """,
            (month_start,),
        )
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            if "waste_value" in metadata:
                total += require_non_negative(metadata["waste_value"], "waste_value")
                continue
            if row["lot_id"] is not None and row["quantity"] is not None:
                lot = connection.execute("SELECT * FROM inventory_lots WHERE id = ?", (row["lot_id"],)).fetchone()
                if lot is not None:
                    total += self._lot_value_for_quantity(connection, lot, require_non_negative(row["quantity"]))
        return total

    def _lot_value_for_quantity(self, connection: sqlite3.Connection, lot: sqlite3.Row | dict[str, Any], quantity: Decimal) -> Decimal:
        total_cost = lot["total_cost"]
        if total_cost in (None, "") or quantity == 0:
            return Decimal("0")
        original_quantity, original_unit = self._lot_original_quantity(connection, str(lot["id"]), str(lot["unit"]))
        if original_quantity <= 0:
            return require_non_negative(total_cost, "estimated_cost")
        quantity_in_original_unit = convert(quantity, str(lot["unit"]), original_unit)
        return require_non_negative(total_cost, "estimated_cost") * quantity_in_original_unit / original_quantity

    def _lot_original_quantity(self, connection: sqlite3.Connection, lot_id: str, fallback_unit: str) -> tuple[Decimal, str]:
        row = connection.execute(
            """
            SELECT quantity, unit
            FROM inventory_events
            WHERE lot_id = ? AND event_type IN ('ADD', 'IMPORT', 'LEFTOVER_CREATE')
            ORDER BY revision ASC
            LIMIT 1
            """,
            (lot_id,),
        ).fetchone()
        if row is None or row["quantity"] is None:
            return Decimal("0"), fallback_unit
        return require_positive(row["quantity"]), str(row["unit"] or fallback_unit)

    def _money_text(self, value: Decimal) -> str:
        return f"{value.quantize(Decimal('0.01')):.2f}"

    def _recipe_snapshot(self, connection: sqlite3.Connection, recipe_id: str) -> dict[str, Any]:
        recipe = dict(connection.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone())
        recipe["ingredients"] = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM recipe_ingredients WHERE recipe_id = ? ORDER BY position",
                (recipe_id,),
            )
        ]
        return recipe

    def _ensure_metadata(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO app_metadata(key, value) VALUES ('instance_id', ?)",
            (new_id("inst"),),
        )
        connection.execute("INSERT OR IGNORE INTO app_metadata(key, value) VALUES ('state_revision', '0')")
        connection.execute(
            "INSERT OR REPLACE INTO app_metadata(key, value) VALUES ('schema_version', ?)",
            (str(CURRENT_SCHEMA_VERSION),),
        )

    def _metadata(self, connection: sqlite3.Connection, key: str) -> str:
        row = connection.execute("SELECT value FROM app_metadata WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise ValidationError(f"Missing app metadata: {key}")
        return str(row["value"])

    def _validate_legacy_document(self, data: Any) -> None:
        if not isinstance(data, dict):
            raise ValidationError("Legacy document must be an object")
        for key in ("items", "recipes", "shopping_list"):
            if key in data and not isinstance(data[key], list):
                raise ValidationError(f"Legacy {key} must be a list")
        if "meal_plan" in data and not isinstance(data["meal_plan"], dict):
            raise ValidationError("Legacy meal_plan must be an object")
        for item in data.get("items", []):
            if not isinstance(item, dict) or not item.get("name"):
                raise ValidationError("Legacy item must include name")
            require_non_negative(item.get("quantity", "0"))
            unit_code(str(item.get("unit") or "count"))
        for recipe in data.get("recipes", []):
            if not isinstance(recipe, dict) or not recipe.get("name"):
                raise ValidationError("Legacy recipe must include name")
            for ingredient in recipe.get("ingredients", []):
                if not ingredient.get("name"):
                    raise ValidationError("Legacy ingredient must include name")
                require_positive(ingredient.get("quantity", "1"))
                unit_code(str(ingredient.get("unit") or "count"))

    def _infer_location_type(self, name: str, position: int) -> str:
        normalized = normalize_name(name)
        if position == 0:
            return "room" if normalized != "unassigned" else "other"
        if "freezer" in normalized:
            return "freezer"
        if "refrigerator" in normalized or "fridge" in normalized:
            return "refrigerator"
        if "pantry" in normalized:
            return "pantry"
        if "shelf" in normalized:
            return "shelf"
        if "cabinet" in normalized:
            return "cabinet"
        return "other"


