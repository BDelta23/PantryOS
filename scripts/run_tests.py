"""Run PantryOS tests without requiring pytest."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_MODULES = (
    "tests.test_inventory",
    "tests.test_app_server",
    "tests.test_api_client",
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

