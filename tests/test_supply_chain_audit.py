from __future__ import annotations

from scripts import supply_chain_audit


def test_parse_base_image_requires_digest_pinned_python_slim() -> None:
    name, digest = supply_chain_audit.parse_base_image()

    assert name == "python:3.12-slim"
    assert digest.startswith("sha256:")
    assert len(digest) == 71


def test_source_tree_digest_is_stable_for_sorted_manifest() -> None:
    files = [
        {"path": "b.txt", "sha256": "b" * 64},
        {"path": "a.txt", "sha256": "a" * 64},
    ]

    assert supply_chain_audit.source_tree_digest(files) == supply_chain_audit.source_tree_digest(files)


def test_build_spdx_includes_base_app_and_debian_packages() -> None:
    lock = {
        "base_image": "python:3.12-slim",
        "base_digest": "sha256:" + "1" * 64,
        "source_tree_sha256": "2" * 64,
    }
    files = [{"path": "Dockerfile", "sha256": "3" * 64}]
    packages = [{"name": "tesseract-ocr", "version": "1.0", "architecture": "amd64"}]

    sbom = supply_chain_audit.build_spdx("pantryos-pantryos:latest", lock, files, packages)
    package_names = {package["name"] for package in sbom["packages"]}

    assert sbom["spdxVersion"] == "SPDX-2.3"
    assert "PantryOS application source" in package_names
    assert "python:3.12-slim" in package_names
    assert "tesseract-ocr" in package_names
    assert sbom["files"][0]["fileName"] == "Dockerfile"
