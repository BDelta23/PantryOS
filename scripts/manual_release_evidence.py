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
EVIDENCE_ARTIFACT_ROOT = ROOT / "docs" / "release" / "evidence"
REVIEW_ARTIFACT_ROOT = ROOT / "docs" / "reviews"

REQUIRED_CHECKS: dict[str, dict[str, tuple[str, ...]]] = {
    "physical-barcode-camera": {
        "acceptance": ("F1", "F2", "G5", "G6"),
        "details": (
            "device",
            "os",
            "browser",
            "app_url",
            "known_barcode",
            "known_result",
            "unknown_barcode",
            "manual_fallback_result",
        ),
    },
    "real-receipt-ocr": {
        "acceptance": ("F5", "F6", "F8"),
        "details": (
            "device",
            "os",
            "browser",
            "receipt_source",
            "capture_method",
            "receipt_id",
            "purchase_id",
            "committed_lot_count",
            "price_history_result",
        ),
    },
    "published-image-signature": {
        "acceptance": ("I4", "I5", "J8"),
        "details": ("image", "digest", "tag", "verification_command", "signature_identity", "transparency_log_url"),
    },
    "independent-full-review": {
        "acceptance": ("J7", "J8"),
        "details": ("review_path", "reviewed_commit", "decision", "open_critical_high", "release_blocking_medium"),
    },
}

REJECTED_VALUES = {"", "todo", "tbd", "pending", "unknown", "n/a", "na", "replace-me", "changeme"}
SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
BARCODE_RE = re.compile(r"^\d{8,14}$")
SYNTHETIC_RECEIPT_SOURCE_TERMS = ("synthetic", "fixture", "generated", "mock", "sample", "test")
OCR_CAPTURE_TERMS = ("ocr", "tesseract")
IMAGE_CAPTURE_TERMS = ("photo", "image", "camera", "scan", "scanned", "jpeg", "jpg", "png")


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


def resolve_target_commit(commit: str | None = None, *, root: Path = ROOT) -> str:
    value = commit.strip() if isinstance(commit, str) else current_commit(root=root)
    if GIT_COMMIT_RE.fullmatch(value) is None:
        raise ManualReleaseEvidenceError("target release commit must be a 40-character lowercase Git SHA")
    return value


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
    payload = build_template(commit=resolve_target_commit(commit, root=root), root=root)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def validate_evidence(path: Path = DEFAULT_EVIDENCE_PATH, *, root: Path = ROOT, commit: str | None = None) -> dict[str, Any]:
    problems: list[dict[str, str]] = []
    evidence_path = path if path.is_absolute() else root / path
    if not evidence_path.exists():
        return _result(
            evidence_path,
            root=root,
            ok=False,
            problems=[{"field": "file", "problem": f"missing {display_path(evidence_path, root=root)}"}],
        )

    try:
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _result(evidence_path, root=root, ok=False, problems=[{"field": "file", "problem": f"invalid JSON: {exc.msg}"}])

    if not isinstance(data, dict):
        return _result(evidence_path, root=root, ok=False, problems=[{"field": "file", "problem": "root value must be a JSON object"}])
    _validate_exact_keys(data, {"schema_version", "release_commit", "checks"}, "root", problems)

    try:
        expected_commit = resolve_target_commit(commit, root=root)
    except ManualReleaseEvidenceError as exc:
        return _result(evidence_path, root=root, ok=False, problems=[{"field": "target_commit", "problem": str(exc)}])
    require_git_tracking = _is_git_worktree(root)
    expected_evidence_path = _rooted_path(DEFAULT_EVIDENCE_PATH, root=root)
    if evidence_path.resolve() != expected_evidence_path.resolve():
        problems.append({"field": "file", "problem": "must be docs/release/manual-validation.json"})
    elif require_git_tracking:
        _validate_git_release_file(evidence_path, root=root, field="file", problems=problems)
    release_commit = data.get("release_commit")
    if data.get("schema_version") != 1:
        problems.append({"field": "schema_version", "problem": "must be 1"})
    if release_commit != expected_commit:
        problems.append({"field": "release_commit", "problem": f"must match target release commit {expected_commit}"})

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
        if check_id not in REQUIRED_CHECKS:
            problems.append({"field": f"checks[{index}].id", "problem": f"unknown check id {check_id}"})
            continue
        by_id[check_id] = check

    for check_id, rule in REQUIRED_CHECKS.items():
        check = by_id.get(check_id)
        if check is None:
            problems.append({"field": "checks", "problem": f"missing required check {check_id}"})
            continue
        problems.extend(
            _validate_check(
                check_id,
                check,
                rule,
                root=root,
                expected_commit=expected_commit,
                require_git_tracking=require_git_tracking,
            )
        )

    return _result(evidence_path, root=root, ok=not problems, problems=problems)


def _validate_check(
    check_id: str,
    check: dict[str, Any],
    rule: dict[str, tuple[str, ...]],
    *,
    root: Path,
    expected_commit: str,
    require_git_tracking: bool,
) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    prefix = f"checks[{check_id}]"
    _validate_exact_keys(check, {"id", "result", "operator", "timestamp_utc", "acceptance", "details", "evidence"}, prefix, problems)
    if check.get("result") != "PASS":
        problems.append({"field": f"{prefix}.result", "problem": "must be PASS"})
    _require_clean_string(check.get("operator"), f"{prefix}.operator", problems)
    _validate_timestamp(check.get("timestamp_utc"), f"{prefix}.timestamp_utc", problems)

    acceptance = check.get("acceptance")
    if not isinstance(acceptance, list) or not all(isinstance(item, str) for item in acceptance):
        problems.append({"field": f"{prefix}.acceptance", "problem": "must list acceptance IDs"})
    else:
        required_acceptance = set(rule["acceptance"])
        listed_acceptance = set(acceptance)
        missing = [item for item in rule["acceptance"] if item not in listed_acceptance]
        extras = sorted(listed_acceptance - required_acceptance)
        duplicates = sorted({item for item in acceptance if acceptance.count(item) > 1})
        if missing:
            problems.append({"field": f"{prefix}.acceptance", "problem": "missing " + ", ".join(missing)})
        if extras:
            problems.append({"field": f"{prefix}.acceptance", "problem": "unexpected " + ", ".join(extras)})
        if duplicates:
            problems.append({"field": f"{prefix}.acceptance", "problem": "duplicate " + ", ".join(duplicates)})

    details = check.get("details")
    if not isinstance(details, dict):
        problems.append({"field": f"{prefix}.details", "problem": "must be an object"})
    else:
        _validate_exact_keys(details, set(rule["details"]), f"{prefix}.details", problems)
        for field in rule["details"]:
            _require_clean_string(details.get(field), f"{prefix}.details.{field}", problems)
        _validate_check_specific_details(
            check_id,
            details,
            prefix,
            root=root,
            expected_commit=expected_commit,
            require_git_tracking=require_git_tracking,
            problems=problems,
        )

    evidence = check.get("evidence")
    if not isinstance(evidence, dict):
        problems.append({"field": f"{prefix}.evidence", "problem": "must be an object"})
    else:
        _validate_exact_keys(evidence, {"summary", "artifact_paths"}, f"{prefix}.evidence", problems)
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
                artifact_root = _rooted_path(EVIDENCE_ARTIFACT_ROOT, root=root)
                if not _is_relative_to(artifact_path, artifact_root):
                    problems.append(
                        {
                            "field": f"{prefix}.evidence.artifact_paths[{index}]",
                            "problem": "must be under docs/release/evidence",
                        }
                    )
                if not artifact_path.exists():
                    problems.append({"field": f"{prefix}.evidence.artifact_paths[{index}]", "problem": f"missing {value}"})
                elif not artifact_path.is_file():
                    problems.append({"field": f"{prefix}.evidence.artifact_paths[{index}]", "problem": "must be a file"})
                elif require_git_tracking:
                    _validate_git_release_file(
                        artifact_path,
                        root=root,
                        field=f"{prefix}.evidence.artifact_paths[{index}]",
                        problems=problems,
                    )

    return problems


def _validate_check_specific_details(
    check_id: str,
    details: dict[str, Any],
    prefix: str,
    *,
    root: Path,
    expected_commit: str,
    require_git_tracking: bool,
    problems: list[dict[str, str]],
) -> None:
    if check_id == "physical-barcode-camera":
        app_url = details.get("app_url")
        if not isinstance(app_url, str) or HTTP_URL_RE.match(app_url.strip()) is None:
            problems.append({"field": f"{prefix}.details.app_url", "problem": "must be an http(s) URL"})
        known_barcode = str(details.get("known_barcode", "")).strip()
        unknown_barcode = str(details.get("unknown_barcode", "")).strip()
        if known_barcode and not _valid_gtin(known_barcode):
            problems.append({"field": f"{prefix}.details.known_barcode", "problem": "must be a valid 8 to 14 digit GTIN"})
        if unknown_barcode and not _valid_gtin(unknown_barcode):
            problems.append({"field": f"{prefix}.details.unknown_barcode", "problem": "must be a valid 8 to 14 digit GTIN"})
        if known_barcode and unknown_barcode and known_barcode == unknown_barcode:
            problems.append({"field": f"{prefix}.details.unknown_barcode", "problem": "must differ from known_barcode"})
        known_result = str(details.get("known_result", "")).strip().lower()
        if known_result and "product" not in known_result and "lot" not in known_result:
            problems.append({"field": f"{prefix}.details.known_result", "problem": "must describe the resolved product or lot"})
        fallback_result = str(details.get("manual_fallback_result", "")).strip().lower()
        if fallback_result and "manual" not in fallback_result:
            problems.append({"field": f"{prefix}.details.manual_fallback_result", "problem": "must describe the manual fallback"})
    elif check_id == "real-receipt-ocr":
        receipt_source = str(details.get("receipt_source", "")).strip().lower()
        if receipt_source and any(term in receipt_source for term in SYNTHETIC_RECEIPT_SOURCE_TERMS):
            problems.append(
                {
                    "field": f"{prefix}.details.receipt_source",
                    "problem": "must describe a representative real receipt, not synthetic or test data",
                }
            )
        capture_method = str(details.get("capture_method", "")).strip().lower()
        if capture_method and (
            not any(term in capture_method for term in OCR_CAPTURE_TERMS) or not any(term in capture_method for term in IMAGE_CAPTURE_TERMS)
        ):
            problems.append(
                {
                    "field": f"{prefix}.details.capture_method",
                    "problem": "must describe OCR extraction from a receipt image, photo, camera capture, or scan",
                }
            )
        receipt_id = details.get("receipt_id")
        if not isinstance(receipt_id, str) or not receipt_id.strip().startswith("receipt_"):
            problems.append({"field": f"{prefix}.details.receipt_id", "problem": "must be a committed receipt_ identifier"})
        purchase_id = details.get("purchase_id")
        if not isinstance(purchase_id, str) or not purchase_id.strip().startswith("purchase_"):
            problems.append({"field": f"{prefix}.details.purchase_id", "problem": "must be a purchase_ identifier"})
        _require_positive_int_string(details.get("committed_lot_count"), f"{prefix}.details.committed_lot_count", problems)
        price_history_result = str(details.get("price_history_result", "")).strip().lower()
        if price_history_result and "price" not in price_history_result:
            problems.append({"field": f"{prefix}.details.price_history_result", "problem": "must describe price history visibility"})
    elif check_id == "published-image-signature":
        digest = details.get("digest")
        digest_value = digest.strip() if isinstance(digest, str) else ""
        if SHA256_DIGEST_RE.fullmatch(digest_value) is None:
            problems.append({"field": f"{prefix}.details.digest", "problem": "must be a sha256:<64 lowercase hex> digest"})
        image = details.get("image")
        image_value = image.strip() if isinstance(image, str) else ""
        if image_value and digest_value and digest_value not in image_value:
            problems.append({"field": f"{prefix}.details.image", "problem": "must include the recorded digest"})
        signature_identity = details.get("signature_identity")
        identity_value = signature_identity.strip() if isinstance(signature_identity, str) else ""
        command = details.get("verification_command")
        if not isinstance(command, str) or "cosign" not in command.lower() or "verify" not in command.lower():
            problems.append({"field": f"{prefix}.details.verification_command", "problem": "must record a cosign verify command"})
        elif image_value and image_value not in command:
            problems.append({"field": f"{prefix}.details.verification_command", "problem": "must include the recorded image reference"})
        elif digest_value and digest_value not in command:
            problems.append({"field": f"{prefix}.details.verification_command", "problem": "must include the recorded digest"})
        elif identity_value and identity_value not in command:
            problems.append({"field": f"{prefix}.details.verification_command", "problem": "must include signature_identity"})
        transparency_log_url = details.get("transparency_log_url")
        transparency_value = transparency_log_url.strip() if isinstance(transparency_log_url, str) else ""
        unavailable_prefix = "not available:"
        if transparency_value:
            transparency_lower = transparency_value.lower()
            unavailable_reason = transparency_value[len(unavailable_prefix) :].strip()
            if not HTTP_URL_RE.match(transparency_value) and not (transparency_lower.startswith(unavailable_prefix) and unavailable_reason):
                problems.append(
                    {
                        "field": f"{prefix}.details.transparency_log_url",
                        "problem": "must be an http(s) URL or start with 'not available:' and include a reason",
                    }
                )
    elif check_id == "independent-full-review":
        if details.get("decision") != "PASS":
            problems.append({"field": f"{prefix}.details.decision", "problem": "must be PASS"})
        if str(details.get("open_critical_high", "")).strip() != "0":
            problems.append({"field": f"{prefix}.details.open_critical_high", "problem": "must be 0"})
        if str(details.get("release_blocking_medium", "")).strip() != "0":
            problems.append({"field": f"{prefix}.details.release_blocking_medium", "problem": "must be 0"})
        if details.get("reviewed_commit") != expected_commit:
            problems.append(
                {"field": f"{prefix}.details.reviewed_commit", "problem": f"must match target release commit {expected_commit}"}
            )
        review_path = details.get("review_path")
        if isinstance(review_path, str) and not _rejected(review_path):
            resolved = Path(review_path)
            if not resolved.is_absolute():
                resolved = root / resolved
            review_root = _rooted_path(REVIEW_ARTIFACT_ROOT, root=root)
            if not _is_relative_to(resolved, review_root):
                problems.append({"field": f"{prefix}.details.review_path", "problem": "must be under docs/reviews"})
            if not resolved.exists():
                problems.append({"field": f"{prefix}.details.review_path", "problem": f"missing {review_path}"})
            elif not resolved.is_file():
                problems.append({"field": f"{prefix}.details.review_path", "problem": "must be a file"})
            elif require_git_tracking:
                _validate_git_release_file(resolved, root=root, field=f"{prefix}.details.review_path", problems=problems)
            if resolved.exists() and resolved.is_file():
                _validate_review_artifact_text(
                    resolved.read_text(encoding="utf-8", errors="replace"),
                    expected_commit=expected_commit,
                    field=f"{prefix}.details.review_path",
                    problems=problems,
                )


def _validate_exact_keys(value: dict[str, Any], expected: set[str], field: str, problems: list[dict[str, str]]) -> None:
    extras = sorted(set(value) - expected)
    if extras:
        problems.append({"field": field, "problem": "unexpected fields: " + ", ".join(extras)})


def _validate_review_artifact_text(review_text: str, *, expected_commit: str, field: str, problems: list[dict[str, str]]) -> None:
    normalized = re.sub(r"\s+", "", review_text.lower())
    if expected_commit not in review_text:
        problems.append({"field": field, "problem": "review artifact must mention reviewed_commit"})
    required_markers = {
        "decision": "PASS",
        "open_critical_high": "0",
        "release_blocking_medium": "0",
    }
    for key, value in required_markers.items():
        if f"{key}={value.lower()}" not in normalized and f"{key}:{value.lower()}" not in normalized:
            problems.append({"field": field, "problem": f"review artifact must record {key}={value}"})


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
        return
    if parsed.astimezone(UTC) > datetime.now(UTC):
        problems.append({"field": field, "problem": "must not be in the future"})


def _require_clean_string(value: Any, field: str, problems: list[dict[str, str]]) -> None:
    if not isinstance(value, str) or _rejected(value):
        problems.append({"field": field, "problem": "must be a concrete non-empty string"})


def _require_positive_int_string(value: Any, field: str, problems: list[dict[str, str]]) -> None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        problems.append({"field": field, "problem": "must be a positive integer"})
        return
    if parsed < 1:
        problems.append({"field": field, "problem": "must be a positive integer"})


def _valid_gtin(value: str) -> bool:
    if BARCODE_RE.fullmatch(value) is None:
        return False
    digits = [int(char) for char in value]
    total = 0
    for index, digit in enumerate(reversed(digits[:-1])):
        total += digit * (3 if index % 2 == 0 else 1)
    return (10 - (total % 10)) % 10 == digits[-1]


def _is_git_worktree(root: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def _is_git_tracked(path: Path, *, root: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", relative],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        return False
    return completed.returncode == 0


def _is_git_clean(path: Path, *, root: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--", relative],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        return False
    return completed.returncode == 0 and completed.stdout.strip() == ""


def _validate_git_release_file(path: Path, *, root: Path, field: str, problems: list[dict[str, str]]) -> None:
    if not _is_git_tracked(path, root=root):
        problems.append({"field": field, "problem": "must be tracked by git"})
    elif not _is_git_clean(path, root=root):
        problems.append({"field": field, "problem": "must match committed git content"})


def _rooted_path(path: Path, *, root: Path) -> Path:
    return root / path.relative_to(ROOT)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


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
    parser.add_argument("--commit", help="Target release commit for validation/templates; defaults to current HEAD")
    parser.add_argument("--json", action="store_true", help="Print machine-readable validation details")
    template_group = parser.add_mutually_exclusive_group()
    template_group.add_argument(
        "--print-template", action="store_true", help="Print an incomplete evidence template for the target release commit"
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
        try:
            target_commit = resolve_target_commit(args.commit)
        except ManualReleaseEvidenceError as exc:
            print(str(exc))
            return 2
        print(json.dumps(build_template(commit=target_commit), indent=2))
        return 0
    if args.write_template is not None:
        try:
            path = write_template(args.write_template, commit=args.commit)
        except ManualReleaseEvidenceError as exc:
            print(str(exc))
            return 2
        print(f"manual release evidence template written: {display_path(path)}")
        return 0

    result = validate_evidence(args.path, commit=args.commit)
    if args.json or not result["ok"]:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"manual release evidence: ok path={result['path']} checks={len(result['required_checks'])}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
