# PantryOS manual release validation

This release gate records proof that cannot be produced honestly by the automated test suite alone. Do not create `docs/release/manual-validation.json` until the target release commit, published image, physical barcode check, real receipt check, and independent review are all complete.

## Workflow

1. Choose the immutable release candidate commit:

```powershell
git rev-parse HEAD
```

2. Generate the field scaffold for that exact commit:

```powershell
python scripts/manual_release_evidence.py --write-template --commit <release-candidate-sha>
```

3. Perform each manual check against the same release candidate build and commit concrete evidence artifacts under `docs/release/evidence/`.
4. Rename or copy the completed scaffold to `docs/release/manual-validation.json` only after every check has `result` set to `PASS` and all placeholder values have been replaced with concrete values.
5. Commit the evidence artifacts, `docs/release/manual-validation.json`, and the final independent review report.
6. Validate from a clean checkout:

```powershell
python scripts/manual_release_evidence.py --json --commit <release-candidate-sha>
python scripts/release_readiness.py --write
python scripts/release_readiness.py --check
```

The `docs/release/evidence/` directory is intentionally tracked with `.gitkeep` so release operators have a stable destination for manual artifacts. The validator intentionally rejects untracked or dirty manual evidence files in a Git checkout. Commit the files before treating a validation pass as release evidence.

## Required Checks

### physical-barcode-camera

Acceptance IDs: `F1`, `F2`, `G5`, `G6`.

Record the physical device, OS, browser, PantryOS app URL, a valid 8-14 digit GTIN known barcode, the known product or lot outcome, a different valid 8-14 digit GTIN unknown barcode, and the manual fallback product or lot outcome. The evidence artifact must mention `physical-barcode-camera` and describe the real device/browser path, not only the automated browser smoke.

### real-receipt-ocr

Acceptance IDs: `F5`, `F6`, `F8`.

Record the physical device, OS, browser, representative real receipt source, OCR capture method from a receipt image/photo/scan, committed `receipt_<32 lowercase hex>` ID, committed `purchase_<32 lowercase hex>` ID, committed lot count, and price-history result. The price-history result must mention store, date, package or quantity, total, and unit price. The evidence artifact must mention `real-receipt-ocr` and must not be based on synthetic, fixture, generated, mock, sample, or test receipt data.

### published-image-signature

Acceptance IDs: `I4`, `I5`, `J8`.

Record the SemVer tag, digest-pinned published image reference in `image:vX.Y.Z@sha256:<digest>` form, matching `sha256:<64 lowercase hex>` digest, signature identity, Rekor/Sigstore transparency-log URL or `not available: ...` reason, and the exact passing `cosign verify` command. The command must verify the recorded image reference and digest, include the recorded signature identity, constrain that identity with `--certificate-identity` or `--certificate-identity-regexp`, and avoid insecure cosign verification flags. The evidence artifact must mention `published-image-signature`.

### independent-full-review

Acceptance IDs: `J7`, `J8`.

Record the `docs/reviews/` review path, reviewed commit, `decision` of `PASS`, `open_critical_high` of `0`, and `release_blocking_medium` of `0`. The referenced review artifact must be tracked and clean, mention the exact target release commit, and include machine-readable markers equivalent to `decision=PASS`, `open_critical_high=0`, and `release_blocking_medium=0`.

## Non-Passing Evidence

A generated `manual-validation.template.json`, a stale review against an older commit, automated camera/OCR smoke output alone, unsigned local image metadata, or uncommitted local notes must remain a blocker. Leave release readiness as `NOT READY` until `python scripts/manual_release_evidence.py --json --commit <release-candidate-sha>` passes from a clean checkout.