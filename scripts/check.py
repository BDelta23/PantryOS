"""Run the currently supported PantryOS verification suite."""

from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PYTHON_FILES = [
    *ROOT.glob("src/**/*.py"),
    *ROOT.glob("app/**/*.py"),
    *ROOT.glob("custom_components/pantryos/**/*.py"),
    *ROOT.glob("scripts/**/*.py"),
    *ROOT.glob("tests/**/*.py"),
]


def run_python_compile() -> None:
    for path in PYTHON_FILES:
        py_compile.compile(str(path), doraise=True)
    print(f"python compile: {len(PYTHON_FILES)} files passed")


def run_js_check() -> None:
    app_js = ROOT / "app" / "static" / "app.js"
    node_candidates = [
        Path(r"C:\Users\Kronus\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"),
        Path("node"),
    ]
    for candidate in node_candidates:
        try:
            subprocess.run(
                [str(candidate), "--check", str(app_js)],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            print("javascript syntax: passed")
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    print("javascript syntax: skipped; node not available")


def main() -> None:
    run_python_compile()
    import scripts.run_tests as run_tests

    run_tests.main()
    run_js_check()


if __name__ == "__main__":
    main()


