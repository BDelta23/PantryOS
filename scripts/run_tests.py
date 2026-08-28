"""Run PantryOS tests without requiring pytest."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for candidate in (ROOT / "scripts", ROOT, SRC):
    candidate_text = str(candidate)
    while candidate_text in sys.path:
        sys.path.remove(candidate_text)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SRC))

TEST_MODULES = (
    "tests.test_app_server",
    "tests.test_api_client",
    "tests.test_cross_surface_sync",
    "tests.test_cli_operations",
    "tests.test_container_contract",
    "tests.test_image_hardening_audit",
    "tests.test_ha_contract",
    "tests.test_openapi_contract",
    "tests.test_release_smoke",
    "tests.test_supply_chain_audit",
    "tests.core.test_sqlite_core",
)


def main() -> None:
    failures: list[str] = []
    count = 0
    for module_name in TEST_MODULES:
        module = importlib.import_module(module_name)
        for name in sorted(dir(module)):
            if not name.startswith("test_"):
                continue
            count += 1
            try:
                getattr(module, name)()
            except Exception as exc:  # noqa: BLE001 - simple dependency-free runner
                failures.append(f"{module_name}.{name}: {exc!r}")

    if failures:
        print("FAILED")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)

    print(f"{count} tests passed")


if __name__ == "__main__":
    main()
