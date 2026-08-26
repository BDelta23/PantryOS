from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_scripted_demo_proves_supported_surface_vertical_slice() -> None:
    env = {**os.environ, "PANTRYOS_API_TOKEN": "scripted-demo-test-token"}
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "smoke_e2e.py")],
        cwd=ROOT,
        env=env,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])

    assert result["ok"] is True
    assert result["browser_added_lot_id"] == result["ha_synced_lot_id"]
    assert result["purchase_id"].startswith("purchase_")
    assert result["cooking_session_id"].startswith("cook_")
    assert result["leftover_count"] >= 1
    assert "Smoke Rice" in result["use_soon"]
    assert "Smoke Rice Bowl Leftovers" in result["use_soon"]
    assert "cooking.started" in result["event_types"]
    assert "cooking.completed" in result["event_types"]
    assert result["revision"] > 0