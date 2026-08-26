"""Validate manual PantryOS release evidence for external release gates."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_PATH = ROOT / "docs" / "release" / "manual-validation.json"
DEFAULT_TEMPLATE_PATH = ROOT / "docs" / "release" / "manual-validation.template.json"

REQUIRED_CHECKS: dict[str, dict[str, tuple[str, ...]]] = {
    "physical-barcode-camera": {
        "acceptance": ("F2", "G5", "G6"),
        "details": ("device", "os", "browser", "app_url", "barcode_case"),
    },
    "real-receipt-ocr": {
        "acceptance": ("F5", "F6"),
        "details": ("device", "os", "browser", "receipt_source", "capture_method"),
    },
    "published-image-signature": {
        "acceptance": ("I4", "I5", "J8"),
        "details": ("image", "digest", "tag", "verification_command", "signature_identity"),
    },
    "independent-full-review": {
        "acceptance": ("J7", "J8"),
        "details": ("review_path", "reviewed_commit", "decision", "open_critical_high", "release_blocking_medium"),
    },
}

REJECTED_VALUES = {"", "todo", "tbd", "pending", "unknown", "n/a", "na", "replace-me", "changeme"}
SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ManualReleaseEvidenceError(AssertionError):
    """Raised when manual release evidence is missing or incomplete."""


def current_commit(*, root: Path = ROOT) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def build_template(*, commit: str, root: Path = ROOT) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "release_commit": commit,
        "checks": [
            {
                "id": check_id,
                "result": "PENDING",
                "operator": "",
                "timestamp_utc": "",
                "acceptance": list(rule["acceptance"]),
                "details": {field: "" for field in rule["details"]},
                "evidence": {
                    "summary": "",
                    "artifact_paths": [],
                },
            }
            for check_id, rule in REQUIRED_CHECKS.items()
        ],
    }


def write_template(
    path: Path = DEFAULT_TEMPLATE_PATH,
    *,
    root: Path = ROOT,
    commit: str | None = None,
) -> Path:
    target = path if path.is_absolute() else root / path
    evidence_path = root / DEFAULT_EVIDENCE_PATH.relative_to(ROOT)
    if target.resolve() == evidence_path.resolve():
        raise ManualReleaseEvidenceError(
            f"refusing to write incomplete template over release evidence file {display_path(evidence_path, root=root)}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = build_template(commit=commit or current_commit(root=root), root=root)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def validate_evidence(path: Path = DEFAULT_EVIDENCE_PATH, *, root: Path = ROOT, commit: str | None = None) -> dict[str, Any]:
    problems: list[dict[str, str]] = []
    if not path.exists():
        return _result(path, root=root, ok=False, problems=[{"field": "file", "problem": f"missing {display_path(path, root=root)}"}])

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _result(path, root=root, ok=False, problems=[{"field": "file", "problem": f"invalid JSON: {exc.msg}"}])

    if not isinstance(data, dict):
        return _result(path, root=root, ok=False, problems=[{"field": "file", "problem": "root value must be a JSON object"}])

    expected_commit = commit or current_commit(root=root)
    release_commit = data.get("release_commit")
    if data.get("schema_version") != 1:
        problems.append({"field": "schema_version", "problem": "must be 1"})
    if release_commit != expected_commit:
        problems.append({"field": "release_commit", "problem": f"must match current commit {expected_commit}"})

    checks = data.get("checks")
    if not isinstance(checks, list):
        problems.append({"field": "checks", "problem": "must be a list"})
        checks = []

    by_id: dict[str, dict[str, Any]] = {}
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            problems.append({"field": f"checks[{index}]", "problem": "must be an object"})
            continue
        check_id = check.get("id")
        if not isinstance(check_id, str) or not check_id.strip():
            problems.append({"field": f"checks[{index}].id", "problem": "must be a non-empty string"})
            continue
        if check_id in by_id:
            problems.append({"field": f"checks[{index}].id", "problem": f"duplicate check id {check_id}"})
            continue
        by_id[check_id] = check

    for check_id, rule in REQUIRED_CHECKS.items():
        check = by_id.get(check_id)
        if check is None:
            problems.append({"field": "checks", "problem": f"missing required check {check_id}"})
            continue
        problems.extend(_validate_check(check_id, check, rule, root=root, expected_commit=expected_commit))

    return _result(path, root=root, ok=not problems, problems=problems)


def _validate_check(
    check_id: str,
    check: dict[str, Any],
    rule: dict[str, tuple[str, ...]],
    *,
    root: Path,
    expected_commit: str,
) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    prefix = f"checks[{check_id}]"
    if check.get("result") != "PASS":
        problems.append({"field": f"{prefix}.result", "problem": "must be PASS"})
    _require_clean_string(check.get("operator"), f"{prefix}.operator", problems)
    _validate_timestamp(check.get("timestamp_utc"), f"{prefix}.timestamp_utc", problems)

    acceptance = check.get("acceptance")
    if not isinstance(acceptance, list) or not all(isinstance(item, str) for item in acceptance):
        problems.append({"field": f"{prefix}.acceptance", "problem": "must list acceptance IDs"})
    else:
        missing = [item for item in rule["acceptance"] if item not in acceptance]
        if missing:
            problems.append({"field": f"{prefix}.acceptance", "problem": "missing " + ", ".join(missing)})

    details = check.get("details")
    if not isinstance(details, dict):
        problems.append({"field": f"{prefix}.details", "problem": "must be an object"})
    else:
        for field in rule["details"]:
            _require_clean_string(details.get(field), f"{prefix}.details.{field}", problems)
        _validate_check_specific_details(check_id, details, prefix, root=root, expected_commit=expected_commit, problems=problems)

    evidence = check.get("evidence")
    if not isinstance(evidence, dict):
        problems.append({"field": f"{prefix}.evidence", "problem": "must be an object"})
    else:
        _require_clean_string(evidence.get("summary"), f"{prefix}.evidence.summary", problems)
        artifact_paths = evidence.get("artifact_paths")
        if not isinstance(artifact_paths, list) or not artifact_paths:
            problems.append({"field": f"{prefix}.evidence.artifact_paths", "problem": "must list at least one local evidence artifact"})
        else:
            for index, value in enumerate(artifact_paths):
                if not isinstance(value, str) or _rejected(value):
                    problems.append({"field": f"{prefix}.evidence.artifact_paths[{index}]", "problem": "must be a non-empty path"})
                    continue
                artifact_path = Path(value)
                if not artifact_path.is_absolute():
                    artifact_path = root / artifact_path
                if not artifact_path.exists():
                    problems.append({"field": f"{prefix}.evidence.artifact_paths[{index}]", "problem": f"missing {value}"})

    return problems


def _validate_check_specific_details(
    check_id: str,
    details: dict[str, Any],
    prefix: str,
    *,
    root: Path,
    expected_commit: str,
    problems: list[dict[str, str]],
) -> None:
    if check_id == "published-image-signature":
        digest = details.get("digest")
        if not isinstance(digest, str) or SHA256_DIGEST_RE.fullmatch(digest.strip()) is None:
            problems.append({"field": f"{prefix}.details.digest", "problem": "must be a sha256:<64 lowercase hex> digest"})
        command = details.get("verification_command")
        if not isinstance(command, str) or "cosign" not in command.lower() or "verify" not in command.lower():
            problems.append({"field": f"{prefix}.details.verification_command", "problem": "must record a cosign verify command"})
    elif check_id == "independent-full-review":
        if details.get("decision") != "PASS":
            problems.append({"field": f"{prefix}.details.decision", "problem": "must be PASS"})
        if str(details.get("open_critical_high", "")).strip() != "0":
            problems.append({"field": f"{prefix}.details.open_critical_high", "problem": "must be 0"})
        if str(details.get("release_blocking_medium", "")).strip() != "0":
            problems.append({"field": f"{prefix}.details.release_blocking_medium", "problem": "must be 0"})
        if details.get("reviewed_commit") != expected_commit:
            problems.append({"field": f"{prefix}.details.reviewed_commit", "problem": f"must match current commit {expected_commit}"})
        review_path = details.get("review_path")
        if isinstance(review_path, str) and not _rejected(review_path):
            resolved = Path(review_path)
            if not resolved.is_absolute():
                resolved = root / resolved
            if not resolved.exists():
                problems.append({"field": f"{prefix}.details.review_path", "problem": f"missing {review_path}"})


def _validate_timestamp(value: Any, field: str, problems: list[dict[str, str]]) -> None:
    if not isinstance(value, str) or _rejected(value):
        problems.append({"field": field, "problem": "must be an ISO UTC timestamp ending in Z"})
        return
    if not value.endswith("Z"):
        problems.append({"field": field, "problem": "must end in Z"})
        return
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        problems.append({"field": field, "problem": "must be a valid ISO timestamp"})
        return
    if parsed.tzinfo is None or parsed.astimezone(UTC).isoformat().endswith("+00:00") is False:
        problems.append({"field": field, "problem": "must include UTC timezone"})


def _require_clean_string(value: Any, field: str, problems: list[dict[str, str]]) -> None:
    if not isinstance(value, str) or _rejected(value):
        problems.append({"field": field, "problem": "must be a concrete non-empty string"})


def _rejected(value: str) -> bool:
    stripped = value.strip().lower()
    return stripped in REJECTED_VALUES or stripped.startswith("replace ")


def _result(path: Path, *, root: Path, ok: bool, problems: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "ok": ok,
        "path": display_path(path, root=root),
        "required_checks": sorted(REQUIRED_CHECKS),
        "problem_count": len(problems),
        "problems": problems,
    }


def display_path(path: Path, *, root: Path = ROOT) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate PantryOS manual release evidence.")
    parser.add_argument("--path", type=Path, default=DEFAULT_EVIDENCE_PATH, help="Manual evidence JSON path")
    parser.add_argument("--json", action="store_true", help="Print machine-readable validation details")
    template_group = parser.add_mutually_exclusive_group()
    template_group.add_argument(
        "--print-template", action="store_true", help="Print an incomplete evidence template for the current commit"
    )
    template_group.add_argument(
        "--write-template",
        nargs="?",
        const=DEFAULT_TEMPLATE_PATH,
        type=Path,
        help="Write an incomplete evidence template, defaulting to docs/release/manual-validation.template.json",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.print_template:
        print(json.dumps(build_template(commit=current_commit()), indent=2))
        return 0
    if args.write_template is not None:
        try:
            path = write_template(args.write_template)
        except ManualReleaseEvidenceError as exc:
            print(str(exc))
            return 2
        print(f"manual release evidence template written: {display_path(path)}")
        return 0

    result = validate_evidence(args.path)
    if args.json or not result["ok"]:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"manual release evidence: ok path={result['path']} checks={len(result['required_checks'])}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
