"""Run a deterministic receipt OCR corpus inside the PantryOS container."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTAINER = "pantryos"

CORPUS_CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "standard_single_item",
        "text": "Store: OCR Market\nDate: 2026-08-26\nOCR Beans,2,count,5.00\nTotal: 5.00\n",
        "expected_store": "OCR Market",
        "expected_items": ["OCR Beans"],
        "expected_total": "5",
    },
    {
        "id": "multi_item_with_barcode",
        "text": "Store: Corner Grocery\nDate: 2026-08-25\nMilk,1,gallon,3.99,123456\nBananas,6,count,2.50\nTotal: 6.49\n",
        "expected_store": "Corner Grocery",
        "expected_items": ["Milk", "Bananas"],
        "expected_total": "6.49",
    },
    {
        "id": "header_footer_noise",
        "text": "Local Pantry Co\nStore: Farmers Market\nDate: 2026-08-24\nTomatoes,2,lb,4.00\nEggs,12,count,5.00\nTotal: 9.00\nThank you\n",
        "expected_store": "Farmers Market",
        "expected_items": ["Tomatoes", "Eggs"],
        "expected_total": "9",
    },
)

CONTAINER_RUNNER = r"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app/src")
from pantryos.core import PantryCore  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def run_case(core: PantryCore, directory: Path, case: dict) -> dict:
    source = directory / f"{case['id']}.txt"
    output_base = directory / case["id"]
    source.write_text(case["text"], encoding="utf-8")
    command = [
        "text2image",
        f"--text={source}",
        f"--outputbase={output_base}",
        "--font=DejaVu Sans Mono",
        "--fonts_dir=/usr/share/fonts/truetype/dejavu",
        "--ptsize=24",
        "--leading=16",
        "--xsize=1800",
        "--ysize=1200",
        "--margin=80",
        "--degrade_image=false",
        "--rotate_image=false",
        "--invert=false",
        "--white_noise=false",
        "--smooth_noise=false",
        "--blur=false",
    ]
    generated = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    if generated.returncode != 0:
        raise AssertionError(f"text2image failed for {case['id']}: {generated.stderr or generated.stdout}")
    image_path = output_base.with_suffix(".tif")
    require(image_path.exists(), f"text2image did not create {image_path}")

    ocr_text = core._extract_receipt_image(image_path)
    parsed = core._extract_receipt_text(ocr_text)
    names = {item["name"] for item in parsed["items"]}
    expected_names = set(case["expected_items"])
    require(parsed["store"] == case["expected_store"], f"{case['id']} store mismatch: {parsed!r}; OCR={ocr_text!r}")
    require(expected_names <= names, f"{case['id']} item mismatch: {parsed!r}; OCR={ocr_text!r}")
    require(parsed["total"] == case["expected_total"], f"{case['id']} total mismatch: {parsed!r}; OCR={ocr_text!r}")

    uploaded = core.upload_receipt({"filename": f"{case['id']}.txt", "mime_type": "text/plain", "text": ocr_text})
    extracted = core.extract_receipt(uploaded["receipt"]["id"])
    committed = core.commit_receipt(uploaded["receipt"]["id"])
    require(extracted["review"]["store"] == case["expected_store"], f"{case['id']} public extraction mismatch")
    require(len(committed["lots"]) >= len(expected_names), f"{case['id']} commit missed lots: {committed!r}")
    return {
        "id": case["id"],
        "store": parsed["store"],
        "items": sorted(names),
        "total": parsed["total"],
        "ocr_lines": len([line for line in ocr_text.splitlines() if line.strip()]),
        "receipt_id": uploaded["receipt"]["id"],
    }


def main() -> None:
    cases = json.loads(sys.stdin.read())
    with tempfile.TemporaryDirectory(prefix="pantryos-ocr-corpus-") as tempdir:
        directory = Path(tempdir)
        core = PantryCore(directory / "pantryos.sqlite3")
        core.migrate()
        results = [run_case(core, directory, case) for case in cases]
        print(json.dumps({"ok": True, "case_count": len(results), "cases": results}, sort_keys=True))


main()
"""


class ReceiptOcrCorpusSmokeFailure(AssertionError):
    """Raised when the container OCR corpus cannot prove the expected contract."""


def run_command(args: list[str], *, input_text: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            args,
            cwd=ROOT,
            input=input_text,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ReceiptOcrCorpusSmokeFailure(f"Required command is not available: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ReceiptOcrCorpusSmokeFailure(f"Command timed out after {timeout}s: {' '.join(args)}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ReceiptOcrCorpusSmokeFailure(f"Command failed ({completed.returncode}): {' '.join(args)}\n{detail}")
    return completed


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    completed = run_command(
        ["docker", "exec", "-i", args.container, "python", "-c", CONTAINER_RUNNER],
        input_text=json.dumps(CORPUS_CASES),
        timeout=args.timeout,
    )
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise ReceiptOcrCorpusSmokeFailure(f"Container runner did not return JSON: {completed.stdout}") from exc
    if result.get("case_count") != len(CORPUS_CASES):
        raise ReceiptOcrCorpusSmokeFailure(f"Expected {len(CORPUS_CASES)} OCR cases, got {result}")
    return {"container": args.container, **result}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic receipt OCR corpus checks inside the PantryOS container.")
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--timeout", type=int, default=120)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_smoke(args)
    except ReceiptOcrCorpusSmokeFailure as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
