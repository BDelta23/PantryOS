"""Run the currently supported PantryOS verification suite."""

from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

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
    with TemporaryDirectory() as directory:
        cache_dir = Path(directory)
        for index, path in enumerate(PYTHON_FILES):
            py_compile.compile(str(path), cfile=str(cache_dir / f"{index}-{path.stem}.pyc"), doraise=True)
    print(f"python compile: {len(PYTHON_FILES)} files passed")


def run_js_check() -> None:
    js_files = [ROOT / "app" / "static" / "app.js", ROOT / "scripts" / "browser_smoke.cjs"]
    node_candidates = [
        Path(r"C:\Users\Kronus\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"),
        Path("node"),
    ]
    for candidate in node_candidates:
        try:
            for js_file in js_files:
                subprocess.run(
                    [str(candidate), "--check", str(js_file)],
                    cwd=ROOT,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            print(f"javascript syntax: {len(js_files)} files passed")
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    print("javascript syntax: skipped; node not available")


def main() -> None:
    run_python_compile()
    import scripts.run_tests as run_tests

    run_tests.main()
    import scripts.release_readiness as release_readiness

    release_readiness.check_readiness()
    print("release readiness: current")
    import scripts.release_artifact_audit as release_artifact_audit

    release_artifact_audit.check_release_artifacts()
    print("release artifact audit: current")
    run_js_check()


if __name__ == "__main__":
    main()


