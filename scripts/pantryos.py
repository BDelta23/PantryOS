"""Run the PantryOS operations CLI from a source checkout."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) in sys.path:
    sys.path.remove(str(SRC))
sys.path.insert(0, str(SRC))

from pantryos.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
