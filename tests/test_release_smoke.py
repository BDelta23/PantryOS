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


def test_container_smoke_script_is_release_runner_ready() -> None:
    from scripts import container_smoke

    args = container_smoke.build_parser().parse_args(["--skip-image-verifier"])
    assert args.base_url == "http://127.0.0.1:8765"
    assert args.service == "pantryos"
    assert args.container == "pantryos"
    assert args.skip_image_verifier is True

    nested_lot = {"id": "lot_nested", "product_name": "Nested Rice"}
    assert container_smoke.dashboard_lots({"core": {"lots": [nested_lot]}}) == [nested_lot]
    assert container_smoke.dashboard_lots({"lots": [{"id": "lot_top"}]}) == [{"id": "lot_top"}]

def test_home_assistant_installed_smoke_runner_is_documented_and_scoped() -> None:
    from scripts import ha_installed_smoke

    args = ha_installed_smoke.build_parser().parse_args(["--image", "local-ha:test", "--timeout", "12"])
    assert args.image == "local-ha:test"
    assert args.timeout == 12
    assert ha_installed_smoke.DEFAULT_IMAGE == "ghcr.io/home-assistant/home-assistant:stable"
    assert "custom_components.pantryos" in ha_installed_smoke.CONTAINER_SMOKE
    assert "ConfigEntryAuthFailed" in ha_installed_smoke.CONTAINER_SMOKE
    assert "async_setup_entry" in ha_installed_smoke.CONTAINER_SMOKE
    assert "async_unload_entry" in ha_installed_smoke.CONTAINER_SMOKE

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "python scripts/ha_installed_smoke.py" in readme
    assert "PANTRYOS_HA_IMAGE" in readme
    assert "installed Home Assistant" in readme

def test_release_readiness_generator_tracks_acceptance_and_blockers() -> None:
    from scripts import release_readiness

    acceptance = """# Gates
- [ ] A1. One source.
- [ ] J8. Readiness PASS.
"""
    status = """# Status
## Phase gates
- [x] Phase 0 — baseline
- [ ] Release gate PASS

## Evidence log
| Date/time | Phase | Change or decision | Commands and result | Remaining proof gap |
|---|---|---|---|---|
| 2026-08-26 | Phase 8/J | Added audit | `python scripts/check.py` -> passed | Release review pending |

## Open blockers

- Independent review pending.
"""
    criteria = release_readiness.parse_acceptance(acceptance)
    gates = release_readiness.parse_phase_gates(status)
    evidence = release_readiness.parse_evidence(status)
    blockers = release_readiness.parse_open_blockers(status)

    assert [criterion.id for criterion in criteria] == ["A1", "J8"]
    assert [gate.name for gate in gates if not gate.complete] == ["Release gate PASS"]
    assert evidence[-1].phase == "Phase 8/J"
    assert evidence[-1].commands == "`python scripts/check.py` -> passed"
    assert blockers == ["Independent review pending."]

    markdown = release_readiness.render_markdown(
        {
            "generated_at": "2026-08-26T00:00:00Z",
            "decision": "NOT READY",
            "acceptance_criteria_count": len(criteria),
            "phase_gate_count": len(gates),
            "open_phase_gates": ["Release gate PASS"],
            "open_blockers": blockers,
            "latest_evidence": evidence,
            "required_release_commands": release_readiness.release_commands(),
        }
    )
    assert "Status: **NOT READY**" in markdown
    assert "python scripts/container_smoke.py" in markdown
    assert "Independent review pending." in markdown
