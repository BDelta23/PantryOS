# PantryOS container supply-chain policy

PantryOS v1 release candidates use a local-first container workflow. The release image must be reproducible from this checkout after Docker images are installed, must not require internet access at runtime, and must not store private signing material in the repository.

## Image inputs

- `Dockerfile` must use a digest-pinned `python:3.12-slim@sha256:...` base image.
- `docs/release/container-image.lock.json` records the expected base digest, required system packages, source manifest digest, OS package count, and SBOM path for the current release candidate image.
- `docs/release/pantryos-image-sbom.spdx.json` is the generated SPDX-style SBOM for the current local image. It lists the pinned base image, Debian packages visible through `dpkg-query`, and the hashed PantryOS source files copied into the image.
- The Docker image installs `tesseract-ocr` as the only required local OCR system package.

## Required checks

Run these after rebuilding the release candidate image:

```powershell
$env:PANTRYOS_API_TOKEN = "replace-with-a-long-local-token"
docker compose up -d --build --force-recreate pantryos
python scripts/image_hardening_audit.py
python scripts/supply_chain_audit.py --write
python scripts/supply_chain_audit.py
```

`--write` refreshes the lock file and SBOM from the current local image. The check-only command fails if either artifact no longer matches the Dockerfile, copied source files, pinned base digest, or installed Debian packages.

## Image signing policy

Published v1 release images must be signed outside this repository after the final image digest is known. Use a keyless or external-key `cosign sign` workflow controlled by the release operator, then record the SemVer `vX.Y.Z` release tag, digest-pinned published image reference, image digest, signature identity, transparency-log URL or explicit unavailability reason, and verification command in the release notes. The final verification command recorded for release must verify the same digest-pinned published image reference, digest, and signature identity recorded in `docs/release/manual-validation.json`.

No signing keys, registry tokens, cosign passwords, or private certificates may be committed to this repository, copied into the image, stored in `.env`, or recorded in generated release artifacts.