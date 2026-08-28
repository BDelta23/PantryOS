from __future__ import annotations

import json
from pathlib import Path

from pantryos.openapi import openapi_document

ROOT = Path(__file__).resolve().parents[1]


def test_readme_lists_every_current_openapi_operation() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    document = openapi_document()

    for path, methods in document["paths"].items():
        for method in methods:
            expected = f"- `{method.upper()} {path}`"
            assert expected in readme, expected


def test_setup_operations_and_troubleshooting_docs_match_runtime_contracts() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    deploy_readme = (ROOT / "deploy" / "docker" / "README.md").read_text(encoding="utf-8")
    env_example = (ROOT / "deploy" / "docker" / ".env.example").read_text(encoding="utf-8")
    compose = (ROOT / "deploy" / "docker" / "compose.yaml").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "custom_components" / "pantryos" / "manifest.json").read_text(encoding="utf-8"))

    for text in (readme, deploy_readme):
        assert "http://<NAS-LAN-IP>:8765" in text
        assert "Do not use `127.0.0.1`" in text or "Do not enter `127.0.0.1`" in text

    assert "https://github.com/BDelta23/PantryOS" in readme
    assert manifest["documentation"] == "https://github.com/BDelta23/PantryOS#readme"
    assert manifest["issue_tracker"] == "https://github.com/BDelta23/PantryOS/issues"
    assert manifest["domain"] == "pantryos"
    assert "repository type **Integration**" in readme
    assert "No SSH, SD-card access, Samba copying" in readme
    assert "supports reconfiguration" in readme
    assert "supports reauthentication" in readme

    assert "${PANTRYOS_PORT:-8765}:8765" in compose
    assert "./data:/data" in compose
    assert "restart: unless-stopped" in compose
    assert "PANTRYOS_API_TOKEN=replace-with-long-random-token" in env_example
    assert "PANTRYOS_LISTEN_HOST=0.0.0.0" in env_example
    assert "PANTRYOS_DATABASE_PATH=/data/pantryos.sqlite3" in env_example
    assert "PANTRYOS_BACKUP_DIR=/data/backups" in env_example

    assert "python scripts/pantryos.py --db /data/pantryos.sqlite3 backup --output /data/backups/pantryos.zip" in readme
    assert "python scripts/pantryos.py --db /data/pantryos.sqlite3 restore --input /data/backups/pantryos.zip --verify" in readme
    assert "python scripts/pantryos.py --db data/pantryos.sqlite3 import-legacy --path data/pantryos.json --dry-run" in readme
    assert "pre-migration SQLite backup" in readme
    assert "`.failed` copy" in readme

    assert "## Troubleshooting" in readme
    assert "503 not_ready" in readme
    assert "401 unauthorized" in readme
    assert "401 browser_session_required" in readme
    assert "PANTRYOS_LISTEN_HOST=0.0.0.0" in readme
    assert "python scripts/manual_release_evidence.py --json" in readme
