"""SQLite-backed PantryOS Core service."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterator

from .errors import InsufficientInventoryError, NotFoundError, ValidationError
from .units import convert, decimal_text, require_non_negative, require_positive, unit_code

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
CURRENT_SCHEMA_VERSION = 1


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
                for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                    version = int(path.stem.split("_", 1)[0])
                    if version in applied:
                        continue
                    connection.executescript(path.read_text(encoding="utf-8"))
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (?)",
                        (version,),
                    )
                self._ensure_metadata(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

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
                "capabilities": ["sqlite_core", "legacy_import", "inventory_lots", "events"],
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

    def backup(self, output_path: Path | str) -> Path:
        self.migrate()
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
        return target

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
        temp.replace(self.db_path)

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
        return dict(row)

    def _insert_lot(
        self,
        connection: sqlite3.Connection,
        product_id: str,
        location_id: str,
        data: dict[str, Any],
        *,
        source_legacy_id: str | None = None,
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
              expires_at, opened_at, lot_type, total_cost, notes, source_legacy_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        lots = [dict(row) for row in connection.execute("""
            SELECT l.*, p.name AS product_name, p.minimum_stock_quantity,
                   p.minimum_stock_unit, loc.name AS location_name
            FROM inventory_lots l
            JOIN products p ON p.id = l.product_id
            JOIN locations loc ON loc.id = l.location_id
            ORDER BY p.name, l.expires_at IS NULL, l.expires_at, l.created_at
        """)]
        recipes = [self._recipe_snapshot(connection, row["id"]) for row in connection.execute("SELECT id FROM recipes ORDER BY name")]
        shopping = [dict(row) for row in connection.execute("SELECT * FROM shopping_demands ORDER BY display_name")]
        events = [dict(row) for row in connection.execute("SELECT * FROM inventory_events ORDER BY revision DESC LIMIT 25")]
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
            },
        }

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


