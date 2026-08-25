"""Report the baseline defects preserved by the handoff package.

The live app no longer contains the JSON repository that originally reproduced
these issues. This script exists to keep Phase 0 evidence discoverable after the
Core replacement. The original executable reproducer remains in
`docs/handoff/evidence/json_race_reproducer.py` and is runnable from baseline
commit `98078e3`.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs" / "handoff" / "evidence" / "baseline_test_run.txt"


def main() -> None:
    evidence = BASELINE.read_text(encoding="utf-8")
    required = [
        "Lost update reproduced",
        "items: ['Milk']",
        "11 tests passed",
    ]
    missing = [needle for needle in required if needle not in evidence]
    if missing:
        raise SystemExit(f"Baseline evidence is incomplete: {missing}")
    print("Baseline defect evidence is preserved in docs/handoff/evidence")
    print("Run the original reproducer from baseline commit 98078e3 if live reproduction is needed.")


if __name__ == "__main__":
    main()
