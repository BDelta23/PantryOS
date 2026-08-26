"""Audit release-critical files for unresolved completion debt markers."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    "app",
    "custom_components",
    "scripts",
    "src",
    "tests",
    "README.md",
    "docs/handoff",
    "docs/home_assistant",
)
EXCLUDED_PATHS = {
    "scripts/release_artifact_audit.py": "The audit script contains the marker patterns it enforces.",
    "docs/handoff/IMPLEMENTATION_STATUS.md": "The status ledger records historical evidence and residual risks; release_readiness.py audits it separately.",
}
EXCLUDED_PARTS = {".git", "__pycache__", "data"}


@dataclass(frozen=True)
class Rule:
    id: str
    description: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Allowance:
    path: str
    rule_id: str
    snippet: str
    reason: str


RULES = (
    Rule("todo-marker", "Completion-critical TODO/FIXME/HACK markers", re.compile(r"\b(?:TODO|FIXME|XXX|HACK)\b")),
    Rule(
        "placeholder-url",
        "Placeholder URLs or placeholder URL references",
        re.compile(r"\b(?:https?://)?(?:www\.)?example\.com\b|github\.com/example|placeholder [^\n]{0,80}URL", re.IGNORECASE),
    ),
    Rule(
        "fake-or-stub",
        "Fake responses, hard-coded success, or primary-workflow stubs",
        re.compile(
            r"fake response|fake[- ]success|hard-coded success(?: simulation)?|toast-only stub|stubbed primary workflow|"
            r"stub controls|primary workflow remains a stub|completion-critical control may be a stub",
            re.IGNORECASE,
        ),
    ),
    Rule(
        "disabled-test",
        "Disabled test constructs",
        re.compile(r"pytest\.mark\.skip|unittest\.skip|(?:describe|it|test)\.skip\(", re.IGNORECASE),
    ),
    Rule("not-implemented", "Unresolved not-implemented markers", re.compile(r"\bnot implemented\b", re.IGNORECASE)),
    Rule(
        "skipped-release-check",
        "Skipped release-check paths or switches",
        re.compile(
            r"javascript syntax: skipped; node not available|--skip-(?:image-verifier|live)|"
            r"skip_image_verifier|skip_live|skip_pip=True|verifier_tail = \"skipped\"",
            re.IGNORECASE,
        ),
    ),
)

ALLOWANCES = (
    Allowance(
        "README.md",
        "skipped-release-check",
        "python scripts/image_hardening_audit.py --skip-live",
        "Documents the static-only variant; the full live image audit remains a required release command.",
    ),
    Allowance(
        "docs/handoff/01_VISION_AND_SCOPE.md",
        "fake-or-stub",
        "no stub controls",
        "Acceptance-contract language describing what is prohibited, not shipped implementation debt.",
    ),
    Allowance(
        "docs/handoff/03_KNOWN_ISSUES.md",
        "placeholder-url",
        "placeholder manifest documentation URL",
        "Historical baseline issue entry noting that the placeholder URL was removed.",
    ),
    Allowance(
        "docs/handoff/08_ACCEPTANCE_CRITERIA.md",
        "fake-or-stub",
        "toast-only stub",
        "Acceptance criterion text defining the release blocker.",
    ),
    Allowance(
        "docs/handoff/08_ACCEPTANCE_CRITERIA.md",
        "todo-marker",
        "completion-critical TODO",
        "Acceptance criterion text defining the release blocker.",
    ),
    Allowance(
        "docs/handoff/08_ACCEPTANCE_CRITERIA.md",
        "placeholder-url",
        "placeholder URL",
        "Acceptance criterion text defining the release blocker.",
    ),
    Allowance(
        "docs/handoff/08_ACCEPTANCE_CRITERIA.md",
        "fake-or-stub",
        "fake response",
        "Acceptance criterion text defining the release blocker.",
    ),
    Allowance(
        "docs/handoff/09_TEST_AND_RELEASE_PLAN.md",
        "fake-or-stub",
        "no stubbed primary workflow",
        "Release-plan checklist language describing what final verification must prove.",
    ),
    Allowance(
        "docs/handoff/12_REVIEW_CHECKLIST.md",
        "fake-or-stub",
        "No stub or fake-success control.",
        "Review checklist item, not application behavior.",
    ),
    Allowance(
        "docs/handoff/12_REVIEW_CHECKLIST.md",
        "fake-or-stub",
        "primary workflow remains a stub",
        "Review gate prohibition explaining when PASS is disallowed.",
    ),
    Allowance(
        "docs/handoff/13_DECISIONS_AND_NON_GOALS.md",
        "fake-or-stub",
        "control may be a stub",
        "Architecture decision recording the no-stub policy.",
    ),
    Allowance(
        "scripts/check.py",
        "skipped-release-check",
        "javascript syntax: skipped; node not available",
        "Dependency-free verifier reports missing Node; release commands still require the browser smoke with Node.",
    ),
    Allowance(
        "scripts/container_smoke.py",
        "skipped-release-check",
        'verifier_tail = "skipped"',
        "Initial sentinel text only; the default container smoke runs the image verifier.",
    ),
    Allowance(
        "scripts/container_smoke.py",
        "skipped-release-check",
        "skip_image_verifier",
        "Explicit test/debug flag; default release smoke runs the in-image verifier.",
    ),
    Allowance(
        "scripts/container_smoke.py",
        "skipped-release-check",
        "--skip-image-verifier",
        "Explicit test/debug flag; not used by required release commands.",
    ),
    Allowance(
        "scripts/image_hardening_audit.py",
        "skipped-release-check",
        "skip_live",
        "Static audit mode is documented; the required release command runs the live audit.",
    ),
    Allowance(
        "scripts/image_hardening_audit.py",
        "skipped-release-check",
        "--skip-live",
        "Static audit mode is documented; the required release command runs the live audit.",
    ),
    Allowance(
        "scripts/ha_core_live_smoke.py",
        "skipped-release-check",
        "skip_pip=True",
        "Home Assistant Core bootstrap disables HA pip installs inside the disposable smoke container.",
    ),
    Allowance(
        "tests/test_release_smoke.py",
        "skipped-release-check",
        "--skip-image-verifier",
        "Parser unit test covers the optional container-smoke flag without using it for release verification.",
    ),
    Allowance(
        "tests/test_release_smoke.py",
        "skipped-release-check",
        "skip_image_verifier",
        "Parser unit test covers the optional container-smoke flag without using it for release verification.",
    ),
)


class ReleaseArtifactAuditError(AssertionError):
    """Raised when unresolved release artifact debt is found."""


def iter_source_files(scan_paths: Iterable[str | Path] | None = None, *, root: Path = ROOT) -> list[Path]:
    candidates = scan_paths if scan_paths is not None else SOURCE_ROOTS
    files: list[Path] = []
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_absolute():
            path = root / path
        if path.is_file():
            if not is_excluded(path, root=root):
                files.append(path)
            continue
        if not path.is_dir():
            continue
        for child in path.rglob("*"):
            if child.is_file() and not is_excluded(child, root=root):
                files.append(child)
    return sorted(set(files), key=lambda item: display_path(item, root=root))


def is_excluded(path: Path, *, root: Path = ROOT) -> bool:
    relative = display_path(path, root=root)
    if relative in EXCLUDED_PATHS:
        return True
    return any(part in EXCLUDED_PARTS for part in path.parts)


def display_path(path: Path, *, root: Path = ROOT) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def audit_repository(
    scan_paths: Iterable[str | Path] | None = None,
    *,
    root: Path = ROOT,
    require_allowance_coverage: bool = True,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    allowed_matches: list[dict[str, Any]] = []
    seen_allowances: set[tuple[str, str, str]] = set()
    files = iter_source_files(scan_paths, root=root)

    for path in files:
        relative = display_path(path, root=root)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line_number, line in enumerate(lines, start=1):
            for rule in RULES:
                if not rule.pattern.search(line):
                    continue
                allowance = find_allowance(relative, rule.id, line)
                match = {
                    "path": relative,
                    "line": line_number,
                    "rule": rule.id,
                    "excerpt": line.strip(),
                }
                if allowance is None:
                    findings.append(match)
                else:
                    key = (allowance.path, allowance.rule_id, allowance.snippet)
                    seen_allowances.add(key)
                    allowed_matches.append({**match, "reason": allowance.reason})

    missing_allowances: list[dict[str, str]] = []
    if require_allowance_coverage and scan_paths is None:
        for allowance in ALLOWANCES:
            key = (allowance.path, allowance.rule_id, allowance.snippet)
            if key not in seen_allowances:
                missing_allowances.append(
                    {
                        "path": allowance.path,
                        "rule": allowance.rule_id,
                        "snippet": allowance.snippet,
                        "reason": allowance.reason,
                    }
                )

    return {
        "ok": not findings and not missing_allowances,
        "scanned_files": len(files),
        "excluded_paths": EXCLUDED_PATHS,
        "rules": [{"id": rule.id, "description": rule.description} for rule in RULES],
        "findings": findings,
        "allowed_matches": allowed_matches,
        "missing_allowances": missing_allowances,
    }


def find_allowance(path: str, rule_id: str, line: str) -> Allowance | None:
    for allowance in ALLOWANCES:
        if allowance.path == path and allowance.rule_id == rule_id and allowance.snippet in line:
            return allowance
    return None


def check_release_artifacts() -> None:
    result = audit_repository()
    if result["ok"]:
        return
    details = result["findings"] or result["missing_allowances"]
    raise ReleaseArtifactAuditError("Release artifact audit failed: " + json.dumps(details[:10], sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit PantryOS release-critical artifacts for unresolved completion debt.")
    parser.add_argument("--json", action="store_true", help="Print full machine-readable audit results")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = audit_repository()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result["ok"]:
        print(f"release artifact audit: ok scanned_files={result['scanned_files']} allowed_matches={len(result['allowed_matches'])}")
    else:
        print("release artifact audit: failed", file=sys.stderr)
        for finding in result["findings"][:25]:
            print(
                f"{finding['path']}:{finding['line']}: {finding['rule']}: {finding['excerpt']}",
                file=sys.stderr,
            )
        for missing in result["missing_allowances"][:25]:
            print(
                f"missing allowance coverage: {missing['path']} {missing['rule']} {missing['snippet']}",
                file=sys.stderr,
            )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
