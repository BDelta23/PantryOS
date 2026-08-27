from __future__ import annotations

from scripts import image_hardening_audit


def test_canonical_capabilities_accepts_compose_and_docker_names() -> None:
    assert image_hardening_audit.canonical_capabilities(["CAP_SETUID", "chown", "SETGID"]) == ["CHOWN", "SETGID", "SETUID"]


def test_static_image_hardening_checks_cover_container_controls() -> None:
    checks = {check.name: check.ok for check in image_hardening_audit.static_checks()}

    assert checks["dockerfile-slim-base"] is True
    assert checks["dockerfile-pinned-base-digest"] is True
    assert checks["dockerfile-dedicated-user"] is True
    assert checks["entrypoint-drops-privileges"] is True
    assert checks["dockerfile-packages-docker-contract"] is True
    assert checks["compose-read-only-root"] is True
    assert checks["compose-drop-capabilities"] is True
    assert checks["compose-minimal-cap-add"] is True
    assert checks["compose-pids-limit"] is True
    assert checks["compose-no-new-privileges"] is True
    assert checks["dockerignore-.git"] is True
    assert checks["dockerignore-data/*.sqlite3"] is True
    assert checks["supply-chain-policy"] is True


def test_compose_hardening_checks_accept_rendered_config_shape() -> None:
    config = {
        "services": {
            "pantryos": {
                "read_only": True,
                "cap_drop": ["ALL"],
                "cap_add": ["CAP_SETUID", "CAP_CHOWN", "CAP_SETGID"],
                "pids_limit": 256,
                "security_opt": ["no-new-privileges:true"],
                "tmpfs": ["/tmp:mode=1777,size=64m"],
                "volumes": [{"source": "pantryos-data", "target": "/data"}],
            }
        }
    }

    checks = {check.name: check.ok for check in image_hardening_audit.compose_checks(config)}

    assert all(checks.values())
