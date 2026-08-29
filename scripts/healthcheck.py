"""Docker healthcheck for PantryOS readiness over HTTP or optional HTTPS."""

from __future__ import annotations

import json
import os
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _healthcheck_scheme() -> str:
    configured = os.environ.get("PANTRYOS_HEALTHCHECK_SCHEME")
    if configured:
        return configured.strip().lower()
    if os.environ.get("PANTRYOS_TLS_CERT_FILE") and os.environ.get("PANTRYOS_TLS_KEY_FILE"):
        return "https"
    return "http"


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/api/v1/health/ready"
    scheme = _healthcheck_scheme()
    if scheme not in {"http", "https"}:
        print(f"unsupported healthcheck scheme: {scheme}", file=sys.stderr)
        return 2
    port = _env_int("PANTRYOS_LISTEN_PORT", _env_int("PANTRYOS_PORT", 8765))
    url = f"{scheme}://127.0.0.1:{port}{path}"
    context = ssl._create_unverified_context() if scheme == "https" else None
    try:
        with urlopen(url, timeout=3, context=context) as response:
            body = response.read().decode("utf-8")
    except (HTTPError, OSError, URLError) as exc:
        print(f"healthcheck failed: {exc}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        print(f"healthcheck returned non-JSON body: {body[:120]}", file=sys.stderr)
        return 1
    if payload.get("status") != "ready":
        print(f"healthcheck not ready: {payload}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())