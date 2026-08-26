"""PantryOS operations command line interface."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from .core import PantryCore
from .errors import PantryOSError

DEFAULT_DB_PATH = Path("data") / "pantryos.sqlite3"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = Path(args.db or os.environ.get("PANTRYOS_DB_PATH") or DEFAULT_DB_PATH)
    core = PantryCore(db_path)
    try:
        result = args.func(core, args)
    except (OSError, ValueError, PantryOSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pantryos", description="PantryOS local operations")
    parser.add_argument("--db", help="SQLite database path. Defaults to PANTRYOS_DB_PATH or data/pantryos.sqlite3.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Run database readiness and integrity checks.")
    doctor.set_defaults(func=run_doctor)

    backup = subparsers.add_parser("backup", help="Create a verified SQLite backup or upload-inclusive .zip archive.")
    backup.add_argument("--output", required=True, help="Backup output path. Use .zip to include receipt upload files.")
    backup.set_defaults(func=run_backup)

    restore = subparsers.add_parser("restore", help="Restore a SQLite backup or upload-inclusive .zip archive after verification.")
    restore.add_argument("--input", required=True, help="Backup input path. .zip archives restore receipt upload files too.")
    restore.add_argument("--verify", action="store_true", help="Run integrity verification after restore.")
    restore.add_argument("--verify-only", action="store_true", help="Validate the backup and checksum without restoring.")
    restore.set_defaults(func=run_restore)

    legacy = subparsers.add_parser("import-legacy", help="Validate or import a legacy PantryOS JSON file.")
    legacy.add_argument("--path", required=True, help="Legacy JSON input path.")
    legacy.add_argument("--backup-dir", help="Directory for the legacy JSON backup when importing.")
    legacy.add_argument("--dry-run", action="store_true", help="Validate and summarize without changing the database.")
    legacy.set_defaults(func=run_import_legacy)
    return parser


def run_doctor(core: PantryCore, args: argparse.Namespace) -> dict[str, Any]:
    core.migrate()
    core.integrity_check()
    instance = core.instance()
    dashboard = core.dashboard()
    return {
        "ok": True,
        "database": str(core.db_path),
        "schema_version": instance["schema_version"],
        "state_revision": instance["state_revision"],
        "capabilities": instance["capabilities"],
        "counts": {
            "products": len(dashboard["products"]),
            "lots": len(dashboard["lots"]),
            "recipes": len(dashboard["recipes"]),
            "events": dashboard["summary"]["event_count"],
            "purchases": len(core.purchases()),
        },
    }


def run_backup(core: PantryCore, args: argparse.Namespace) -> dict[str, Any]:
    output_path = Path(args.output)
    if output_path.suffix.casefold() == ".zip":
        archive = core.backup_archive(output_path)
        return {
            "ok": True,
            "format": "archive",
            "backup": archive["path"],
            "manifest": "manifest.json",
            "sha256": archive["sha256"],
            "schema_version": archive["schema_version"],
            "state_revision": archive["state_revision"],
            "receipt_upload_count": archive["receipt_upload_count"],
        }
    output = core.backup(output_path)
    core.integrity_check()
    manifest = write_backup_manifest(core, output)
    return {
        "ok": True,
        "format": "sqlite",
        "backup": str(output),
        "manifest": str(manifest),
        "sha256": sha256_file(output),
    }


def run_restore(core: PantryCore, args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.input)
    if source.suffix.casefold() == ".zip":
        archive_probe = core.verify_backup_archive(source)
        if args.verify_only:
            return {
                "ok": True,
                "format": "archive",
                "verified": True,
                "restored": False,
                "schema_version": archive_probe["schema_version"],
                "receipt_upload_count": archive_probe["receipt_upload_count"],
            }
        core.restore_archive(source)
        if args.verify:
            core.integrity_check()
        instance = core.instance()
        return {
            "ok": True,
            "format": "archive",
            "verified": bool(args.verify),
            "restored": True,
            "schema_version": instance["schema_version"],
            "state_revision": instance["state_revision"],
            "receipt_upload_count": archive_probe["receipt_upload_count"],
        }

    verify_backup_manifest(source)
    probe_path = source.with_suffix(source.suffix + ".verify.tmp")
    try:
        probe = PantryCore(probe_path)
        probe.restore(source)
        probe.integrity_check()
        probe_instance = probe.instance()
    finally:
        if probe_path.exists():
            probe_path.unlink()
    if args.verify_only:
        return {"ok": True, "format": "sqlite", "verified": True, "restored": False, "schema_version": probe_instance["schema_version"]}
    core.restore(source)
    if args.verify:
        core.integrity_check()
    instance = core.instance()
    return {
        "ok": True,
        "format": "sqlite",
        "verified": bool(args.verify),
        "restored": True,
        "schema_version": instance["schema_version"],
        "state_revision": instance["state_revision"],
    }


def run_import_legacy(core: PantryCore, args: argparse.Namespace) -> dict[str, Any]:
    inspection = core.inspect_legacy_json(Path(args.path))
    if args.dry_run:
        return {"ok": True, "dry_run": True, "imported": False, **inspection}
    result = core.import_legacy_json(Path(args.path), backup_dir=Path(args.backup_dir) if args.backup_dir else None)
    return {
        "ok": True,
        "dry_run": False,
        "imported": result.imported,
        "content_hash": result.content_hash,
        "backup_path": result.backup_path,
        "item_count": result.item_count,
        "recipe_count": result.recipe_count,
        "shopping_count": result.shopping_count,
        "meal_plan_count": result.meal_plan_count,
    }


def write_backup_manifest(core: PantryCore, backup_path: Path) -> Path:
    instance = core.instance()
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".sha256.json")
    manifest = {
        "file": backup_path.name,
        "sha256": sha256_file(backup_path),
        "schema_version": instance["schema_version"],
        "state_revision": instance["state_revision"],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest_path


def verify_backup_manifest(backup_path: Path) -> None:
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".sha256.json")
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_file = manifest.get("file")
    if expected_file and expected_file != backup_path.name:
        raise ValueError("Backup manifest file name does not match input")
    expected_hash = manifest.get("sha256")
    if expected_hash and expected_hash != sha256_file(backup_path):
        raise ValueError("Backup checksum does not match manifest")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())