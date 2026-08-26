"""Generate and check PantryOS container supply-chain release artifacts."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "docs" / "release" / "container-image.lock.json"
SBOM_PATH = ROOT / "docs" / "release" / "pantryos-image-sbom.spdx.json"
POLICY_PATH = ROOT / "docs" / "release" / "SUPPLY_CHAIN.md"
DEFAULT_IMAGE = "pantryos-pantryos:latest"
BASE_IMAGE_RE = re.compile(r"^FROM (?P<name>python:3\.12-slim)@(?P<digest>sha256:[a-f0-9]{64})$", re.MULTILINE)
REQUIRED_SYSTEM_PACKAGES = ("tesseract-ocr",)
SOURCE_ROOTS = ("app", "custom_components", "scripts", "src", "tests")
ROOT_FILES = (".dockerignore", "Dockerfile", "README.md", "compose.yaml", "pyproject.toml")


@dataclass(frozen=True)
class SupplyChainCheck:
    name: str
    ok: bool
    detail: str


class SupplyChainError(AssertionError):
    """Raised when a required supply-chain artifact cannot be produced."""


def run_command(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            args,
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise SupplyChainError(f"Required command is not available: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SupplyChainError(f"Command timed out after {timeout}s: {' '.join(args)}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise SupplyChainError(f"Command failed ({completed.returncode}): {' '.join(args)}\n{detail}")
    return completed


def parse_base_image() -> tuple[str, str]:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    match = BASE_IMAGE_RE.search(dockerfile)
    if not match:
        raise SupplyChainError("Dockerfile base image must be python:3.12-slim pinned with @sha256:<digest>")
    return match.group("name"), match.group("digest")


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tracked_source_files() -> list[Path]:
    paths: list[Path] = []
    for root_file in ROOT_FILES:
        path = ROOT / root_file
        if path.exists():
            paths.append(path)
    for source_root in SOURCE_ROOTS:
        base = ROOT / source_root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                paths.append(path)
    return sorted(paths, key=lambda item: item.relative_to(ROOT).as_posix())


def source_manifest() -> list[dict[str, str]]:
    return [
        {"path": path.relative_to(ROOT).as_posix(), "sha256": file_sha256(path)}
        for path in tracked_source_files()
    ]


def source_tree_digest(files: list[dict[str, str]]) -> str:
    material = "\n".join(f"{item['sha256']}  {item['path']}" for item in files)
    return sha256(material.encode("utf-8")).hexdigest()



def os_packages(image: str) -> list[dict[str, str]]:
    script = "dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n'"
    completed = run_command(["docker", "run", "--rm", "--entrypoint", "sh", image, "-c", script], timeout=120)
    packages: list[dict[str, str]] = []
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, version, architecture = parts
        packages.append({"name": name, "version": version, "architecture": architecture})
    return sorted(packages, key=lambda item: (item["name"], item["architecture"], item["version"]))


def build_lock(image: str, files: list[dict[str, str]], packages: list[dict[str, str]]) -> dict[str, Any]:
    base_name, base_digest = parse_base_image()
    package_names = {package["name"] for package in packages}
    missing = [name for name in REQUIRED_SYSTEM_PACKAGES if name not in package_names]
    if missing:
        raise SupplyChainError(f"Required system packages missing from image SBOM: {', '.join(missing)}")
    return {
        "schema_version": 1,
        "image": image,
        "base_image": base_name,
        "base_digest": base_digest,
        "required_system_packages": list(REQUIRED_SYSTEM_PACKAGES),
        "source_file_count": len(files),
        "source_tree_sha256": source_tree_digest(files),
        "os_package_count": len(packages),
        "sbom_path": SBOM_PATH.relative_to(ROOT).as_posix(),
        "signing_policy": POLICY_PATH.relative_to(ROOT).as_posix(),
    }


def build_spdx(image: str, lock: dict[str, Any], files: list[dict[str, str]], packages: list[dict[str, str]]) -> dict[str, Any]:
    spdx_packages: list[dict[str, Any]] = [
        {
            "SPDXID": "SPDXRef-PantryOS-App",
            "name": "PantryOS application source",
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": True,
            "checksums": [{"algorithm": "SHA256", "checksumValue": lock["source_tree_sha256"]}],
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        },
        {
            "SPDXID": "SPDXRef-BaseImage",
            "name": lock["base_image"],
            "versionInfo": lock["base_digest"],
            "downloadLocation": f"pkg:docker/{lock['base_image']}@{lock['base_digest']}",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        },
    ]
    for package in packages:
        spdx_packages.append(
            {
                "SPDXID": "SPDXRef-DebianPackage-" + re.sub(r"[^A-Za-z0-9.-]", "-", package["name"]),
                "name": package["name"],
                "versionInfo": package["version"],
                "supplier": "Organization: Debian",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": f"pkg:deb/debian/{package['name']}@{package['version']}?arch={package['architecture']}",
                    }
                ],
            }
        )
    spdx_files = [
        {
            "SPDXID": "SPDXRef-File-" + re.sub(r"[^A-Za-z0-9.-]", "-", item["path"]),
            "fileName": item["path"],
            "checksums": [{"algorithm": "SHA256", "checksumValue": item["sha256"]}],
            "licenseConcluded": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        }
        for item in files
    ]
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"PantryOS container image SBOM ({image})",
        "documentNamespace": "https://pantryos.local/spdx/pantryos-image",
        "creationInfo": {
            "created": "2026-08-26T00:00:00Z",
            "creators": ["Tool: scripts/supply_chain_audit.py"],
        },
        "documentDescribes": ["SPDXRef-PantryOS-App", "SPDXRef-BaseImage"],
        "packages": spdx_packages,
        "files": spdx_files,
        "relationships": [
            {"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": "SPDXRef-PantryOS-App"},
            {"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": "SPDXRef-BaseImage"},
            {"spdxElementId": "SPDXRef-PantryOS-App", "relationshipType": "DEPENDS_ON", "relatedSpdxElement": "SPDXRef-BaseImage"},
        ],
        "pantryosLock": lock,
    }


def expected_artifacts(image: str) -> tuple[dict[str, Any], dict[str, Any]]:
    files = source_manifest()
    packages = os_packages(image)
    lock = build_lock(image, files, packages)
    return lock, build_spdx(image, lock, files, packages)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_file(path: Path, expected: dict[str, Any], checks: list[SupplyChainCheck]) -> None:
    try:
        actual = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        checks.append(SupplyChainCheck(path.name, False, f"Missing {path.relative_to(ROOT).as_posix()}"))
        return
    checks.append(SupplyChainCheck(path.name, actual == expected, f"{path.relative_to(ROOT).as_posix()} matches current Docker image and source manifest."))


def policy_checks() -> list[SupplyChainCheck]:
    try:
        policy = POLICY_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [SupplyChainCheck("supply-chain-policy", False, "Missing docs/release/SUPPLY_CHAIN.md")]
    required_terms = ["digest-pinned", "container-image.lock.json", "pantryos-image-sbom.spdx.json", "cosign", "No signing keys"]
    missing = [term for term in required_terms if term not in policy]
    return [SupplyChainCheck("supply-chain-policy", not missing, "Supply-chain policy covers digest pinning, lock file, SBOM, and signing key handling.")]


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    lock, sbom = expected_artifacts(args.image)
    checks: list[SupplyChainCheck] = []
    if args.write:
        write_json(LOCK_PATH, lock)
        write_json(SBOM_PATH, sbom)
    check_file(LOCK_PATH, lock, checks)
    check_file(SBOM_PATH, sbom, checks)
    checks.extend(policy_checks())
    failures = [check.name for check in checks if not check.ok]
    return {
        "ok": not failures,
        "image": args.image,
        "lock_path": LOCK_PATH.relative_to(ROOT).as_posix(),
        "sbom_path": SBOM_PATH.relative_to(ROOT).as_posix(),
        "checks": [check.__dict__ for check in checks],
        "failed": failures,
        "source_file_count": lock["source_file_count"],
        "os_package_count": lock["os_package_count"],
        "base_image": lock["base_image"],
        "base_digest": lock["base_digest"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate and check PantryOS container supply-chain release artifacts.")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--write", action="store_true", help="Write the current lock file and SPDX-style SBOM before checking them.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_audit(args)
    except SupplyChainError as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())