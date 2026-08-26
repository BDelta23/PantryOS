from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _git_available() -> bool:
    return shutil.which("git") is not None


def _commit_all(root: Path, message: str) -> None:
    subprocess.run(
        ["git", "config", "user.email", "release-test@example.test"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Release Test"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    subprocess.run(["git", "commit", "-m", message], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _complete_manual_release_checks(manual_release_evidence: Any, commit: str) -> list[dict[str, Any]]:
    checks = []
    for check_id, rule in manual_release_evidence.REQUIRED_CHECKS.items():
        details = {field: f"value-{field}" for field in rule["details"]}
        if check_id == "physical-barcode-camera":
            details.update(
                {
                    "app_url": "http://127.0.0.1:8765",
                    "known_barcode": "012345678905",
                    "known_result": "Resolved known product and created lot lot_known",
                    "unknown_barcode": "4006381333931",
                    "manual_fallback_result": "Unknown barcode opened manual mapping and manual item entry succeeded",
                }
            )
        elif check_id == "real-receipt-ocr":
            details.update(
                {
                    "receipt_source": "Real grocery receipt from Example Market",
                    "capture_method": "Phone photo image processed through PantryOS OCR",
                    "receipt_id": "receipt_123",
                    "purchase_id": "purchase_123",
                    "committed_lot_count": "2",
                    "price_history_result": "Price history displayed store, date, package quantity, total, and unit price",
                }
            )
        elif check_id == "published-image-signature":
            details.update(
                {
                    "image": "ghcr.io/example/pantryos:v1.0.0@sha256:" + "1" * 64,
                    "digest": "sha256:" + "1" * 64,
                    "tag": "v1.0.0",
                    "verification_command": "cosign verify ghcr.io/example/pantryos:v1.0.0@sha256:"
                    + "1" * 64
                    + " --certificate-identity release@example.test",
                    "signature_identity": "release@example.test",
                    "transparency_log_url": "https://rekor.example.test/entry/123",
                }
            )
        elif check_id == "independent-full-review":
            details.update(
                {
                    "review_path": "docs/reviews/independent-review.md",
                    "reviewed_commit": commit,
                    "decision": "PASS",
                    "open_critical_high": "0",
                    "release_blocking_medium": "0",
                }
            )
        checks.append(
            {
                "id": check_id,
                "result": "PASS",
                "operator": "release-operator",
                "timestamp_utc": "2026-08-26T12:00:00Z",
                "acceptance": list(rule["acceptance"]),
                "details": details,
                "evidence": {
                    "summary": f"{check_id} passed against the release candidate.",
                    "artifact_paths": ["docs/release/evidence/manual-check.md"],
                },
            }
        )
    return checks


def _passing_review_text(commit: str) -> str:
    return (
        "# Independent review\n\n"
        f"reviewed_commit={commit}\n"
        "decision=PASS\n"
        "open_critical_high=0\n"
        "release_blocking_medium=0\n"
        "\nNo release-blocking findings remain.\n"
    )


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
    result = json.loads(completed.stdout)

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


def test_api_concurrency_smoke_proves_twenty_supported_mutations() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "concurrency_smoke.py")],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    result = json.loads(completed.stdout)

    assert result["ok"] is True
    assert result["successful_mutations"] == 20
    assert result["final_revision"] - result["baseline_revision"] == 20
    assert result["event_count"] == result["final_revision"]
    assert result["product_count"] >= 20
    assert result["active_lot_count"] >= 16


def test_container_smoke_script_is_release_runner_ready() -> None:
    from scripts import container_smoke

    args = container_smoke.build_parser().parse_args(["--skip-image-verifier"])
    assert args.base_url == "http://127.0.0.1:8765"
    assert args.service == "pantryos"
    assert args.container == "pantryos"
    assert args.isolated is False
    assert args.skip_image_verifier is True

    isolated = container_smoke.build_parser().parse_args(["--isolated", "--isolated-port", "9876"])
    assert isolated.isolated is True
    assert isolated.isolated_port == 9876
    env = container_smoke.compose_env("token", project_name="pantryos-container-smoke-test", port=9876)
    assert env["PANTRYOS_API_TOKEN"] == "token"
    assert env["PANTRYOS_PORT"] == "9876"
    assert env["COMPOSE_PROJECT_NAME"] == "pantryos-container-smoke-test"

    with TemporaryDirectory() as directory:
        container_smoke.configure_isolated_context(isolated, Path(directory))
        assert isolated.base_url == "http://127.0.0.1:9876"
        assert isolated.container.startswith("pantryos-container-smoke-")
        assert isolated.isolated_volume.startswith("pantryos-container-smoke-data-")
        assert isolated.compose_project_name.startswith("pantryos-container-smoke-")
        assert len(isolated.compose_files) == 1
        override = isolated.compose_files[0].read_text(encoding="utf-8")
        assert isolated.container in override
        assert isolated.isolated_volume in override
        assert '"127.0.0.1:9876:8765"' in override
        assert 'restart: "no"' in override

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


def test_receipt_ocr_corpus_smoke_runner_is_documented_and_scoped() -> None:
    from scripts import receipt_ocr_corpus_smoke

    args = receipt_ocr_corpus_smoke.build_parser().parse_args(["--container", "pantryos-test", "--timeout", "15"])
    assert args.container == "pantryos-test"
    assert args.timeout == 15
    assert len(receipt_ocr_corpus_smoke.CORPUS_CASES) == 3
    assert "text2image" in receipt_ocr_corpus_smoke.CONTAINER_RUNNER
    assert "_extract_receipt_image" in receipt_ocr_corpus_smoke.CONTAINER_RUNNER
    assert "upload_receipt" in receipt_ocr_corpus_smoke.CONTAINER_RUNNER

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "python scripts/receipt_ocr_corpus_smoke.py" in readme
    assert "temporary SQLite database" in readme
    assert "does not mutate your PantryOS database" in readme


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
    assert "python scripts/concurrency_smoke.py" in markdown
    assert "python scripts/container_smoke.py --isolated" in markdown
    assert "Independent review pending." in markdown


def test_release_readiness_latest_evidence_is_newest_first() -> None:
    from scripts import release_readiness

    evidence = [
        release_readiness.EvidenceRow(
            date=f"2026-08-{day:02d}",
            phase="Phase 8/J",
            change=f"change {day}",
            commands=f"command {day}",
            gap=f"gap {day}",
        )
        for day in range(1, 11)
    ]

    latest = release_readiness.latest_evidence_rows(evidence)

    assert [row.change for row in latest] == [f"change {day}" for day in range(10, 2, -1)]


def test_release_artifact_audit_blocks_unallowed_completion_debt() -> None:
    from scripts import release_artifact_audit

    with TemporaryDirectory() as directory:
        debt_file = Path(directory) / "debt.py"
        debt_file.write_text("# " + "TO" + "DO: replace " + "fake " + "response" + " before release\n", encoding="utf-8")

        result = release_artifact_audit.audit_repository(
            [debt_file],
            require_allowance_coverage=False,
        )

    assert result["ok"] is False
    assert {finding["rule"] for finding in result["findings"]} == {"todo-marker", "fake-or-stub"}


def test_release_artifact_audit_current_allowances_are_reasoned() -> None:
    from scripts import release_artifact_audit

    result = release_artifact_audit.audit_repository()

    assert result["ok"] is True
    assert result["allowed_matches"]
    assert result["missing_allowances"] == []
    for match in result["allowed_matches"]:
        assert match["reason"].strip()


def test_manual_release_evidence_reports_missing_file() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        result = manual_release_evidence.validate_evidence(
            root / "docs" / "release" / "manual-validation.json",
            root=root,
            commit="a" * 40,
        )

    assert result["ok"] is False
    assert result["problem_count"] == 1
    assert result["problems"][0]["field"] == "file"


def test_manual_release_evidence_template_is_current_commit_scaffold() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        commit = "f" * 40
        template_path = root / "docs" / "release" / "manual-validation.template.json"

        template = manual_release_evidence.build_template(commit=commit, root=root)
        written = manual_release_evidence.write_template(template_path, root=root, commit=commit)
        result = manual_release_evidence.validate_evidence(written, root=root, commit=commit)

        assert written == template_path
        assert template_path.exists()
        assert template["schema_version"] == 1
        assert template["release_commit"] == commit
        assert [check["id"] for check in template["checks"]] == list(manual_release_evidence.REQUIRED_CHECKS)
        for check in template["checks"]:
            rule = manual_release_evidence.REQUIRED_CHECKS[check["id"]]
            assert check["result"] == "PENDING"
            assert check["acceptance"] == list(rule["acceptance"])
            assert set(check["details"]) == set(rule["details"])
            assert check["evidence"]["artifact_paths"] == []
        assert result["ok"] is False
        assert not any("missing required check" in problem["problem"] for problem in result["problems"])
        assert {problem["field"] for problem in result["problems"]} >= {
            "checks[physical-barcode-camera].result",
            "checks[real-receipt-ocr].result",
            "checks[published-image-signature].result",
            "checks[independent-full-review].result",
        }


def test_manual_release_evidence_template_accepts_explicit_target_commit() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        target_commit = "a" * 40
        template_path = root / "docs" / "release" / "manual-validation.template.json"

        args = manual_release_evidence.build_parser().parse_args(["--commit", target_commit, "--print-template"])
        written = manual_release_evidence.write_template(template_path, root=root, commit=target_commit)
        template = json.loads(written.read_text(encoding="utf-8"))

    assert args.commit == target_commit
    assert template["release_commit"] == target_commit


def test_manual_release_evidence_rejects_invalid_target_commit() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps({"schema_version": 1, "release_commit": "a" * 40, "checks": []}), encoding="utf-8")
        template_path = root / "docs" / "release" / "manual-validation.template.json"

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit="not-a-sha")
        try:
            manual_release_evidence.write_template(template_path, root=root, commit="not-a-sha")
        except manual_release_evidence.ManualReleaseEvidenceError as exc:
            error = str(exc)
        else:
            raise AssertionError("expected ManualReleaseEvidenceError")

    assert result["ok"] is False
    assert result["problems"] == [{"field": "target_commit", "problem": "target release commit must be a 40-character lowercase Git SHA"}]
    assert "target release commit" in error


def test_manual_release_evidence_cli_rejects_invalid_template_commit_cleanly() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "manual_release_evidence.py"), "--print-template", "--commit", "not-a-sha"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 2
    assert completed.stdout.strip() == "target release commit must be a 40-character lowercase Git SHA"
    assert "Traceback" not in completed.stderr


def test_manual_release_evidence_template_refuses_evidence_path() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True)

        try:
            manual_release_evidence.write_template(evidence_path, root=root, commit="f" * 40)
        except manual_release_evidence.ManualReleaseEvidenceError as exc:
            error = str(exc)
        else:
            raise AssertionError("expected ManualReleaseEvidenceError")

        assert "refusing to write incomplete template" in error
        assert not evidence_path.exists()


def test_manual_release_evidence_accepts_complete_current_commit_record() -> None:
    from scripts import manual_release_evidence

    if not _git_available():
        return

    with TemporaryDirectory() as directory:
        root = Path(directory)
        subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        commit = "b" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        subprocess.run(
            ["git", "add", "docs/release/evidence/manual-check.md", "docs/reviews/independent-review.md"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        checks = []
        for check_id, rule in manual_release_evidence.REQUIRED_CHECKS.items():
            details = {field: f"value-{field}" for field in rule["details"]}
            if check_id == "physical-barcode-camera":
                details.update(
                    {
                        "app_url": "http://127.0.0.1:8765",
                        "known_barcode": "012345678905",
                        "known_result": "Resolved known product and created lot lot_known",
                        "unknown_barcode": "4006381333931",
                        "manual_fallback_result": "Unknown barcode opened manual mapping and manual item entry succeeded",
                    }
                )
            elif check_id == "real-receipt-ocr":
                details.update(
                    {
                        "receipt_source": "Real grocery receipt from Example Market",
                        "capture_method": "Phone photo image processed through PantryOS OCR",
                        "receipt_id": "receipt_123",
                        "purchase_id": "purchase_123",
                        "committed_lot_count": "2",
                        "price_history_result": "Price history displayed store, date, package quantity, total, and unit price",
                    }
                )
            elif check_id == "published-image-signature":
                details.update(
                    {
                        "image": "ghcr.io/example/pantryos:v1.0.0@sha256:" + "1" * 64,
                        "digest": "sha256:" + "1" * 64,
                        "tag": "v1.0.0",
                        "verification_command": "cosign verify ghcr.io/example/pantryos:v1.0.0@sha256:"
                        + "1" * 64
                        + " --certificate-identity release@example.test",
                        "signature_identity": "release@example.test",
                        "transparency_log_url": "https://rekor.example.test/entry/123",
                    }
                )
            elif check_id == "independent-full-review":
                details.update(
                    {
                        "review_path": "docs/reviews/independent-review.md",
                        "reviewed_commit": commit,
                        "decision": "PASS",
                        "open_critical_high": "0",
                        "release_blocking_medium": "0",
                    }
                )
            checks.append(
                {
                    "id": check_id,
                    "result": "PASS",
                    "operator": "release-operator",
                    "timestamp_utc": "2026-08-26T12:00:00Z",
                    "acceptance": list(rule["acceptance"]),
                    "details": details,
                    "evidence": {
                        "summary": f"{check_id} passed against the release candidate.",
                        "artifact_paths": ["docs/release/evidence/manual-check.md"],
                    },
                }
            )
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        subprocess.run(
            ["git", "add", "docs/release/manual-validation.json"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _commit_all(root, "manual release evidence")
        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is True
    assert result["problem_count"] == 0
    assert result["required_checks"] == sorted(manual_release_evidence.REQUIRED_CHECKS)


def test_manual_release_evidence_rejects_tracked_review_without_target_commit() -> None:
    from scripts import manual_release_evidence

    if not _git_available():
        return

    with TemporaryDirectory() as directory:
        root = Path(directory)
        subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "b" * 40
        other_commit = "c" * 40
        review.write_text(_passing_review_text(other_commit), encoding="utf-8")
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "release_commit": commit,
                    "checks": _complete_manual_release_checks(manual_release_evidence, commit),
                }
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                "git",
                "add",
                "docs/release/evidence/manual-check.md",
                "docs/reviews/independent-review.md",
                "docs/release/manual-validation.json",
            ],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _commit_all(root, "manual release evidence")

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[independent-full-review].details.review_path",
        "problem": "review artifact must mention reviewed_commit",
    } in result["problems"]


def test_manual_release_evidence_rejects_review_without_outcome_markers() -> None:
    from scripts import manual_release_evidence

    if not _git_available():
        return

    with TemporaryDirectory() as directory:
        root = Path(directory)
        subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "b" * 40
        review.write_text(
            f"# Independent review\n\nReviewed commit: {commit}\n\nPASS: no release-blocking findings.\n",
            encoding="utf-8",
        )
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "release_commit": commit,
                    "checks": _complete_manual_release_checks(manual_release_evidence, commit),
                }
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                "git",
                "add",
                "docs/release/evidence/manual-check.md",
                "docs/reviews/independent-review.md",
                "docs/release/manual-validation.json",
            ],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _commit_all(root, "manual release evidence")

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[independent-full-review].details.review_path",
        "problem": "review artifact must record decision=PASS",
    } in result["problems"]
    assert {
        "field": "checks[independent-full-review].details.review_path",
        "problem": "review artifact must record open_critical_high=0",
    } in result["problems"]
    assert {
        "field": "checks[independent-full-review].details.review_path",
        "problem": "review artifact must record release_blocking_medium=0",
    } in result["problems"]


def test_manual_release_evidence_rejects_future_pass_timestamps() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "b" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        checks[0]["timestamp_utc"] = "2999-01-01T00:00:00Z"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[physical-barcode-camera].timestamp_utc",
        "problem": "must not be in the future",
    } in result["problems"]


def test_manual_release_evidence_rejects_unknown_checks_and_acceptance_overstatement() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "b" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        checks[0]["acceptance"].append("J8")
        checks[1]["acceptance"].append(checks[1]["acceptance"][0])
        checks.append(
            {
                "id": "operator-note",
                "result": "PASS",
                "operator": "release-operator",
                "timestamp_utc": "2026-08-26T12:00:00Z",
                "acceptance": ["J8"],
                "details": {},
                "evidence": {
                    "summary": "Unsupported extra check should not satisfy or extend release evidence.",
                    "artifact_paths": ["docs/release/evidence/manual-check.md"],
                },
            }
        )
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[4].id",
        "problem": "unknown check id operator-note",
    } in result["problems"]
    assert {
        "field": "checks[physical-barcode-camera].acceptance",
        "problem": "unexpected J8",
    } in result["problems"]
    assert {
        "field": "checks[real-receipt-ocr].acceptance",
        "problem": "duplicate F5",
    } in result["problems"]


def test_manual_release_evidence_rejects_unexpected_schema_fields() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "b" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        checks[0]["operator_note"] = "operator-only assertion"
        checks[0]["details"]["extra_device_claim"] = "external camera attachment"
        checks[0]["evidence"]["remote_url"] = "https://release.example.invalid/evidence"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "release_commit": commit,
                    "checks": checks,
                    "release_manager_note": "do not let ignored root fields influence release status",
                }
            ),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "root",
        "problem": "unexpected fields: release_manager_note",
    } in result["problems"]
    assert {
        "field": "checks[physical-barcode-camera]",
        "problem": "unexpected fields: operator_note",
    } in result["problems"]
    assert {
        "field": "checks[physical-barcode-camera].details",
        "problem": "unexpected fields: extra_device_claim",
    } in result["problems"]
    assert {
        "field": "checks[physical-barcode-camera].evidence",
        "problem": "unexpected fields: remote_url",
    } in result["problems"]


def test_manual_release_evidence_rejects_incomplete_records() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "release_commit": "c" * 40,
                    "checks": [
                        {
                            "id": "physical-barcode-camera",
                            "result": "FAIL",
                            "operator": "release-operator",
                            "timestamp_utc": "2026-08-26T12:00:00Z",
                            "acceptance": ["F2"],
                            "details": {"device": "phone"},
                            "evidence": {"summary": "camera did not scan", "artifact_paths": ["docs/release/evidence/missing.md"]},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit="c" * 40)

    assert result["ok"] is False
    assert any(problem["field"] == "checks[physical-barcode-camera].result" for problem in result["problems"])
    assert any("missing required check real-receipt-ocr" in problem["problem"] for problem in result["problems"])
    assert any(problem["field"] == "checks[physical-barcode-camera].evidence.artifact_paths[0]" for problem in result["problems"])


def test_manual_release_evidence_rejects_artifacts_outside_release_evidence_dir() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        outside_dir = root / "docs" / "reviews"
        outside_dir.mkdir(parents=True)
        outside_artifact = outside_dir / "manual-check.md"
        outside_artifact.write_text("release evidence in the wrong folder\n", encoding="utf-8")
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        commit = "d" * 40
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "release_commit": commit,
                    "checks": [
                        {
                            "id": "physical-barcode-camera",
                            "result": "PASS",
                            "operator": "release-operator",
                            "timestamp_utc": "2026-08-26T12:00:00Z",
                            "acceptance": ["F1", "F2", "G5", "G6"],
                            "details": {
                                "device": "Pixel 8",
                                "os": "Android 16",
                                "browser": "Chrome",
                                "app_url": "http://127.0.0.1:8765",
                                "known_barcode": "012345678905",
                                "known_result": "Resolved known product and created lot lot_known",
                                "unknown_barcode": "4006381333931",
                                "manual_fallback_result": "Unknown barcode opened manual mapping and manual item entry succeeded",
                            },
                            "evidence": {
                                "summary": "Physical barcode evidence artifact is in the wrong committed folder.",
                                "artifact_paths": ["docs/reviews/manual-check.md"],
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert any(
        problem["field"] == "checks[physical-barcode-camera].evidence.artifact_paths[0]"
        and problem["problem"] == "must be under docs/release/evidence"
        for problem in result["problems"]
    )


def test_manual_release_evidence_rejects_non_release_evidence_path() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "e" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        evidence_path = evidence_dir / "manual-validation.json"
        evidence_path.write_text(
            json.dumps(
                {"schema_version": 1, "release_commit": commit, "checks": _complete_manual_release_checks(manual_release_evidence, commit)}
            ),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {problem["field"]: problem["problem"] for problem in result["problems"]}["file"] == "must be docs/release/manual-validation.json"


def test_manual_release_evidence_rejects_untracked_evidence_file() -> None:
    from scripts import manual_release_evidence

    if not _git_available():
        return

    with TemporaryDirectory() as directory:
        root = Path(directory)
        subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "e" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(
                {"schema_version": 1, "release_commit": commit, "checks": _complete_manual_release_checks(manual_release_evidence, commit)}
            ),
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "add", "docs/release/evidence/manual-check.md", "docs/reviews/independent-review.md"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {problem["field"]: problem["problem"] for problem in result["problems"]}["file"] == "must be tracked by git"


def test_manual_release_evidence_rejects_dirty_tracked_evidence_file() -> None:
    from scripts import manual_release_evidence

    if not _git_available():
        return

    with TemporaryDirectory() as directory:
        root = Path(directory)
        subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "e" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(
                {"schema_version": 1, "release_commit": commit, "checks": _complete_manual_release_checks(manual_release_evidence, commit)}
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                "git",
                "add",
                "docs/release/evidence/manual-check.md",
                "docs/reviews/independent-review.md",
                "docs/release/manual-validation.json",
            ],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _commit_all(root, "manual release evidence")
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "release_commit": commit,
                    "checks": _complete_manual_release_checks(manual_release_evidence, commit),
                    "dirty": True,
                }
            ),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {problem["field"]: problem["problem"] for problem in result["problems"]}["file"] == "must match committed git content"


def test_manual_release_evidence_rejects_dirty_tracked_artifacts() -> None:
    from scripts import manual_release_evidence

    if not _git_available():
        return

    with TemporaryDirectory() as directory:
        root = Path(directory)
        subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "e" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(
                {"schema_version": 1, "release_commit": commit, "checks": _complete_manual_release_checks(manual_release_evidence, commit)}
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                "git",
                "add",
                "docs/release/evidence/manual-check.md",
                "docs/reviews/independent-review.md",
                "docs/release/manual-validation.json",
            ],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _commit_all(root, "manual release evidence")
        artifact.write_text("release evidence changed after commit\n", encoding="utf-8")
        review.write_text(_passing_review_text(commit) + "\\nLocal edit after commit.\\n", encoding="utf-8")

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    fields = {problem["field"] for problem in result["problems"] if problem["problem"] == "must match committed git content"}
    assert result["ok"] is False
    assert "checks[physical-barcode-camera].evidence.artifact_paths[0]" in fields
    assert "checks[independent-full-review].details.review_path" in fields


def test_manual_release_evidence_rejects_untracked_git_artifacts() -> None:
    from scripts import manual_release_evidence

    if not _git_available():
        return

    with TemporaryDirectory() as directory:
        root = Path(directory)
        subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured but not tracked\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "release_commit": commit,
                    "checks": [
                        {
                            "id": "physical-barcode-camera",
                            "result": "PASS",
                            "operator": "release-operator",
                            "timestamp_utc": "2026-08-26T12:00:00Z",
                            "acceptance": ["F1", "F2", "G5", "G6"],
                            "details": {
                                "device": "Pixel 8",
                                "os": "Android 16",
                                "browser": "Chrome",
                                "app_url": "http://127.0.0.1:8765",
                                "known_barcode": "012345678905",
                                "known_result": "Resolved known product and created lot lot_known",
                                "unknown_barcode": "4006381333931",
                                "manual_fallback_result": "Unknown barcode opened manual mapping and manual item entry succeeded",
                            },
                            "evidence": {
                                "summary": "Physical barcode evidence artifact is not tracked by git.",
                                "artifact_paths": ["docs/release/evidence/manual-check.md"],
                            },
                        },
                        {
                            "id": "independent-full-review",
                            "result": "PASS",
                            "operator": "release-operator",
                            "timestamp_utc": "2026-08-26T12:00:00Z",
                            "acceptance": ["J7", "J8"],
                            "details": {
                                "review_path": "docs/reviews/independent-review.md",
                                "reviewed_commit": commit,
                                "decision": "PASS",
                                "open_critical_high": "0",
                                "release_blocking_medium": "0",
                            },
                            "evidence": {
                                "summary": "Independent review artifact is not tracked by git.",
                                "artifact_paths": ["docs/release/evidence/manual-check.md"],
                            },
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    fields = {problem["field"] for problem in result["problems"] if problem["problem"] == "must be tracked by git"}
    assert result["ok"] is False
    assert "checks[physical-barcode-camera].evidence.artifact_paths[0]" in fields
    assert "checks[independent-full-review].evidence.artifact_paths[0]" in fields
    assert "checks[independent-full-review].details.review_path" in fields


def test_manual_release_evidence_rejects_malformed_physical_barcodes() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        for check in checks:
            if check["id"] == "physical-barcode-camera":
                check["details"]["known_barcode"] = "ABC12345"
                check["details"]["unknown_barcode"] = "1234567"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    fields = {problem["field"]: problem["problem"] for problem in result["problems"]}
    assert fields["checks[physical-barcode-camera].details.known_barcode"] == "must be a valid 8 to 14 digit GTIN"
    assert fields["checks[physical-barcode-camera].details.unknown_barcode"] == "must be a valid 8 to 14 digit GTIN"


def test_manual_release_evidence_rejects_invalid_physical_barcode_check_digits() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        for check in checks:
            if check["id"] == "physical-barcode-camera":
                check["details"]["known_barcode"] = "012345678906"
                check["details"]["unknown_barcode"] = "4006381333932"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    fields = {problem["field"]: problem["problem"] for problem in result["problems"]}
    assert fields["checks[physical-barcode-camera].details.known_barcode"] == "must be a valid 8 to 14 digit GTIN"
    assert fields["checks[physical-barcode-camera].details.unknown_barcode"] == "must be a valid 8 to 14 digit GTIN"


def test_manual_release_evidence_rejects_incomplete_price_history_result() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        for check in checks:
            if check["id"] == "real-receipt-ocr":
                check["details"]["price_history_result"] = "Price history was visible for the product"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[real-receipt-ocr].details.price_history_result",
        "problem": "must describe price history visibility for store, date, package/quantity, total, unit price",
    } in result["problems"]


def test_manual_release_evidence_rejects_synthetic_receipt_source_and_manual_capture() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        for check in checks:
            if check["id"] == "real-receipt-ocr":
                check["details"]["receipt_source"] = "Generated synthetic test receipt fixture"
                check["details"]["capture_method"] = "Manual text entry from the receipt"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    fields = {problem["field"]: problem["problem"] for problem in result["problems"]}
    assert (
        fields["checks[real-receipt-ocr].details.receipt_source"]
        == "must describe a representative real receipt, not synthetic or test data"
    )
    assert (
        fields["checks[real-receipt-ocr].details.capture_method"]
        == "must describe OCR extraction from a receipt image, photo, camera capture, or scan"
    )


def test_manual_release_evidence_rejects_weak_physical_and_receipt_records() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = []
        for check_id, rule in manual_release_evidence.REQUIRED_CHECKS.items():
            details = {field: f"value-{field}" for field in rule["details"]}
            if check_id == "physical-barcode-camera":
                details.update(
                    {
                        "app_url": "pantry.local",
                        "known_barcode": "012345678905",
                        "known_result": "Scanner beeped",
                        "unknown_barcode": "012345678905",
                        "manual_fallback_result": "Created item",
                    }
                )
            elif check_id == "real-receipt-ocr":
                details.update(
                    {
                        "receipt_source": "Real grocery receipt from Example Market",
                        "capture_method": "Phone photo image processed through PantryOS OCR",
                        "receipt_id": "123",
                        "purchase_id": "123",
                        "committed_lot_count": "0",
                        "price_history_result": "History displayed",
                    }
                )
            elif check_id == "published-image-signature":
                details.update(
                    {
                        "image": "ghcr.io/example/pantryos:v1.0.0@sha256:" + "1" * 64,
                        "digest": "sha256:" + "1" * 64,
                        "tag": "v1.0.0",
                        "verification_command": "cosign verify ghcr.io/example/pantryos:v1.0.0@sha256:"
                        + "1" * 64
                        + " --certificate-identity release@example.test",
                        "signature_identity": "release@example.test",
                        "transparency_log_url": "https://rekor.example.test/entry/123",
                    }
                )
            elif check_id == "independent-full-review":
                details.update(
                    {
                        "review_path": "docs/reviews/independent-review.md",
                        "reviewed_commit": commit,
                        "decision": "PASS",
                        "open_critical_high": "0",
                        "release_blocking_medium": "0",
                    }
                )
            checks.append(
                {
                    "id": check_id,
                    "result": "PASS",
                    "operator": "release-operator",
                    "timestamp_utc": "2026-08-26T12:00:00Z",
                    "acceptance": list(rule["acceptance"]),
                    "details": details,
                    "evidence": {
                        "summary": f"{check_id} passed against the release candidate.",
                        "artifact_paths": ["docs/release/evidence/manual-check.md"],
                    },
                }
            )
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    fields = {problem["field"] for problem in result["problems"]}
    assert result["ok"] is False
    assert "checks[physical-barcode-camera].details.app_url" in fields
    assert "checks[physical-barcode-camera].details.unknown_barcode" in fields
    assert "checks[physical-barcode-camera].details.known_result" in fields
    assert "checks[physical-barcode-camera].details.manual_fallback_result" in fields
    assert "checks[real-receipt-ocr].details.receipt_id" in fields
    assert "checks[real-receipt-ocr].details.purchase_id" in fields
    assert "checks[real-receipt-ocr].details.committed_lot_count" in fields
    assert "checks[real-receipt-ocr].details.price_history_result" in fields


def test_manual_release_evidence_rejects_mismatched_signature_and_review_artifacts() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        review.write_text(_passing_review_text("e" * 40), encoding="utf-8")
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        commit = "d" * 40
        recorded_digest = "sha256:" + "1" * 64
        other_digest = "sha256:" + "2" * 64
        checks = []
        for check_id, rule in manual_release_evidence.REQUIRED_CHECKS.items():
            details = {field: f"value-{field}" for field in rule["details"]}
            if check_id == "physical-barcode-camera":
                details.update(
                    {
                        "app_url": "http://127.0.0.1:8765",
                        "known_barcode": "012345678905",
                        "known_result": "Resolved known product and created lot lot_known",
                        "unknown_barcode": "4006381333931",
                        "manual_fallback_result": "Unknown barcode opened manual mapping and manual item entry succeeded",
                    }
                )
            elif check_id == "real-receipt-ocr":
                details.update(
                    {
                        "receipt_source": "Real grocery receipt from Example Market",
                        "capture_method": "Phone photo image processed through PantryOS OCR",
                        "receipt_id": "receipt_123",
                        "purchase_id": "purchase_123",
                        "committed_lot_count": "2",
                        "price_history_result": "Price history displayed store, date, package quantity, total, and unit price",
                    }
                )
            elif check_id == "published-image-signature":
                details.update(
                    {
                        "image": "ghcr.io/example/pantryos:v1.0.0@" + other_digest,
                        "digest": recorded_digest,
                        "tag": "v1.0.0",
                        "verification_command": "cosign verify ghcr.io/example/pantryos@"
                        + other_digest
                        + " --certificate-identity release@example.test",
                        "signature_identity": "release@example.test",
                        "transparency_log_url": "https://rekor.example.test/entry/123",
                    }
                )
            elif check_id == "independent-full-review":
                details.update(
                    {
                        "review_path": "docs/reviews/independent-review.md",
                        "reviewed_commit": commit,
                        "decision": "PASS",
                        "open_critical_high": "0",
                        "release_blocking_medium": "0",
                    }
                )
            checks.append(
                {
                    "id": check_id,
                    "result": "PASS",
                    "operator": "release-operator",
                    "timestamp_utc": "2026-08-26T12:00:00Z",
                    "acceptance": list(rule["acceptance"]),
                    "details": details,
                    "evidence": {
                        "summary": f"{check_id} passed against the release candidate.",
                        "artifact_paths": ["docs/release/evidence/manual-check.md"],
                    },
                }
            )
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    fields = {problem["field"] for problem in result["problems"]}
    assert result["ok"] is False
    assert "checks[published-image-signature].details.image" in fields
    assert "checks[published-image-signature].details.verification_command" in fields
    assert "checks[independent-full-review].details.review_path" in fields


def test_manual_release_evidence_rejects_signature_image_reference_mismatch() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        for check in checks:
            if check["id"] == "published-image-signature":
                digest = check["details"]["digest"]
                check["details"]["image"] = "ghcr.io/example/pantryos-release:v1.0.0@" + digest
                check["details"]["verification_command"] = (
                    "cosign verify ghcr.io/example/pantryos:v1.0.0@" + digest + " --certificate-identity release@example.test"
                )
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[published-image-signature].details.verification_command",
        "problem": "must include the recorded image reference",
    } in result["problems"]


def test_manual_release_evidence_rejects_signature_image_without_recorded_tag() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        for check in checks:
            if check["id"] == "published-image-signature":
                digest = check["details"]["digest"]
                image = "ghcr.io/example/pantryos@" + digest
                check["details"]["image"] = image
                check["details"]["verification_command"] = "cosign verify " + image + " --certificate-identity release@example.test"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[published-image-signature].details.image",
        "problem": "must include the recorded SemVer tag before the digest",
    } in result["problems"]


def test_manual_release_evidence_rejects_signature_identity_mismatch() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        for check in checks:
            if check["id"] == "published-image-signature":
                digest = check["details"]["digest"]
                check["details"]["verification_command"] = (
                    "cosign verify ghcr.io/example/pantryos:v1.0.0@" + digest + " --certificate-identity other@example.test"
                )
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[published-image-signature].details.verification_command",
        "problem": "must include signature_identity",
    } in result["problems"]


def test_manual_release_evidence_rejects_signature_identity_only_outside_cosign_flag() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        for check in checks:
            if check["id"] == "published-image-signature":
                digest = check["details"]["digest"]
                image = "ghcr.io/example/pantryos:v1.0.0@" + digest
                check["details"]["image"] = image
                check["details"]["verification_command"] = (
                    "cosign verify " + image + " --certificate-identity other@example.test --annotations release@example.test"
                )
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[published-image-signature].details.verification_command",
        "problem": "must constrain the recorded signature_identity with --certificate-identity or --certificate-identity-regexp",
    } in result["problems"]


def test_manual_release_evidence_rejects_signature_identity_without_cosign_flag() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        for check in checks:
            if check["id"] == "published-image-signature":
                digest = check["details"]["digest"]
                image = "ghcr.io/example/pantryos:v1.0.0@" + digest
                check["details"]["image"] = image
                check["details"]["verification_command"] = "cosign verify " + image + " release@example.test"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[published-image-signature].details.verification_command",
        "problem": "must constrain signature identity with --certificate-identity or --certificate-identity-regexp",
    } in result["problems"]


def test_manual_release_evidence_rejects_free_text_signature_image_reference() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        for check in checks:
            if check["id"] == "published-image-signature":
                digest = check["details"]["digest"]
                image_note = "Release image digest " + digest
                check["details"]["image"] = image_note
                check["details"]["verification_command"] = "cosign verify " + image_note + " --certificate-identity release@example.test"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[published-image-signature].details.image",
        "problem": "must be a SemVer-tagged digest-pinned image reference like ghcr.io/org/pantryos:v1.0.0@sha256:<digest>",
    } in result["problems"]


def test_manual_release_evidence_rejects_non_semver_signature_tag() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        for check in checks:
            if check["id"] == "published-image-signature":
                check["details"]["tag"] = "latest"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[published-image-signature].details.tag",
        "problem": "must be a SemVer release tag like v1.0.0",
    } in result["problems"]


def test_manual_release_evidence_rejects_malformed_signature_transparency_log() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        for check in checks:
            if check["id"] == "published-image-signature":
                check["details"]["transparency_log_url"] = "rekor entry captured manually"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[published-image-signature].details.transparency_log_url",
        "problem": "must be a Rekor/Sigstore transparency-log URL or start with 'not available:' and include a reason",
    } in result["problems"]


def test_manual_release_evidence_rejects_generic_signature_transparency_url() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        for check in checks:
            if check["id"] == "published-image-signature":
                check["details"]["transparency_log_url"] = "https://example.test/release-notes/v1.0.0"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[published-image-signature].details.transparency_log_url",
        "problem": "must be a Rekor/Sigstore transparency-log URL or start with 'not available:' and include a reason",
    } in result["problems"]


def test_manual_release_evidence_rejects_empty_signature_transparency_unavailability_reason() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        review_dir = root / "docs" / "reviews"
        review_dir.mkdir(parents=True)
        review = review_dir / "independent-review.md"
        commit = "d" * 40
        review.write_text(_passing_review_text(commit), encoding="utf-8")
        checks = _complete_manual_release_checks(manual_release_evidence, commit)
        for check in checks:
            if check["id"] == "published-image-signature":
                check["details"]["transparency_log_url"] = "not available:"
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    assert result["ok"] is False
    assert {
        "field": "checks[published-image-signature].details.transparency_log_url",
        "problem": "must be a Rekor/Sigstore transparency-log URL or start with 'not available:' and include a reason",
    } in result["problems"]


def test_manual_release_evidence_rejects_weak_signature_and_review_records() -> None:
    from scripts import manual_release_evidence

    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_dir = root / "docs" / "release" / "evidence"
        evidence_dir.mkdir(parents=True)
        artifact = evidence_dir / "manual-check.md"
        artifact.write_text("release evidence captured by operator\n", encoding="utf-8")
        evidence_path = root / "docs" / "release" / "manual-validation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        commit = "d" * 40
        checks = []
        for check_id, rule in manual_release_evidence.REQUIRED_CHECKS.items():
            details = {field: f"value-{field}" for field in rule["details"]}
            if check_id == "physical-barcode-camera":
                details.update(
                    {
                        "app_url": "http://127.0.0.1:8765",
                        "known_barcode": "012345678905",
                        "known_result": "Resolved known product and created lot lot_known",
                        "unknown_barcode": "4006381333931",
                        "manual_fallback_result": "Unknown barcode opened manual mapping and manual item entry succeeded",
                    }
                )
            elif check_id == "real-receipt-ocr":
                details.update(
                    {
                        "receipt_source": "Real grocery receipt from Example Market",
                        "capture_method": "Phone photo image processed through PantryOS OCR",
                        "receipt_id": "receipt_123",
                        "purchase_id": "purchase_123",
                        "committed_lot_count": "2",
                        "price_history_result": "Price history displayed store, date, package quantity, total, and unit price",
                    }
                )
            elif check_id == "published-image-signature":
                details.update(
                    {
                        "digest": "value-digest",
                        "verification_command": "docker inspect pantryos",
                    }
                )
            elif check_id == "independent-full-review":
                details.update(
                    {
                        "review_path": "docs/reviews/missing-review.md",
                        "reviewed_commit": "e" * 40,
                        "decision": "FAIL",
                        "open_critical_high": "1",
                        "release_blocking_medium": "2",
                    }
                )
            checks.append(
                {
                    "id": check_id,
                    "result": "PASS",
                    "operator": "release-operator",
                    "timestamp_utc": "2026-08-26T12:00:00Z",
                    "acceptance": list(rule["acceptance"]),
                    "details": details,
                    "evidence": {
                        "summary": f"{check_id} passed against the release candidate.",
                        "artifact_paths": ["docs/release/evidence/manual-check.md"],
                    },
                }
            )
        evidence_path.write_text(
            json.dumps({"schema_version": 1, "release_commit": commit, "checks": checks}),
            encoding="utf-8",
        )

        result = manual_release_evidence.validate_evidence(evidence_path, root=root, commit=commit)

    fields = {problem["field"] for problem in result["problems"]}
    assert result["ok"] is False
    assert "checks[published-image-signature].details.digest" in fields
    assert "checks[published-image-signature].details.image" in fields
    assert "checks[published-image-signature].details.verification_command" in fields
    assert "checks[independent-full-review].details.review_path" in fields
    assert "checks[independent-full-review].details.reviewed_commit" in fields
    assert "checks[independent-full-review].details.decision" in fields
    assert "checks[independent-full-review].details.open_critical_high" in fields
    assert "checks[independent-full-review].details.release_blocking_medium" in fields
