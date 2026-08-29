from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_drops_runtime_to_pantryos_user_and_limits_writable_volume() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    entrypoint = (ROOT / "scripts" / "docker_entrypoint.py").read_text(encoding="utf-8")
    healthcheck = (ROOT / "scripts" / "healthcheck.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "container-image.yml").read_text(encoding="utf-8")

    assert "--uid 10001" in dockerfile
    assert "--gid 10001" in dockerfile
    assert "PANTRYOS_LISTEN_HOST=0.0.0.0" in dockerfile
    assert "PANTRYOS_DATABASE_PATH=/data/pantryos.sqlite3" in dockerfile
    assert "ENTRYPOINT" in dockerfile
    assert "scripts/docker_entrypoint.py" in dockerfile
    assert "scripts/healthcheck.py" in dockerfile
    assert "/api/v1/health/ready" in dockerfile
    assert "PANTRYOS_TLS_CERT_FILE" in healthcheck
    assert "PANTRYOS_TLS_KEY_FILE" in healthcheck
    assert "PANTRYOS_HEALTHCHECK_SCHEME" in healthcheck
    assert "ssl._create_unverified_context" in healthcheck
    assert "/api/v1/health/ready" in healthcheck
    assert ".dockerignore" in dockerfile
    assert "os.setuid(user.pw_uid)" in entrypoint
    assert "os.setgid(user.pw_gid)" in entrypoint
    assert "def chown_tree" in entrypoint
    assert "os.walk(path)" in entrypoint
    assert 'Path("/data")' in entrypoint
    assert "pantryos-data:/data" in compose
    assert "read_only: true" in compose
    assert "/tmp:mode=1777,size=64m" in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose
    assert "- ALL" in compose
    assert "cap_add:" in compose
    assert "- CHOWN" in compose
    assert "- SETGID" in compose
    assert "- SETUID" in compose
    assert "pids_limit: 256" in compose
    assert "PANTRYOS_TLS_CERT_FILE: ${PANTRYOS_TLS_CERT_FILE:-}" in compose
    assert "PANTRYOS_TLS_KEY_FILE: ${PANTRYOS_TLS_KEY_FILE:-}" in compose
    assert "PANTRYOS_HEALTHCHECK_SCHEME: ${PANTRYOS_HEALTHCHECK_SCHEME:-}" in compose
    assert "PANTRYOS_DATA_DIR: /data" in compose
    assert "PANTRYOS_BACKUP_DIR: /data/backups" in compose
    assert "${GITHUB_REPOSITORY,,}" in workflow
    assert "ghcr.io/${GITHUB_REPOSITORY,,}" in workflow
    assert "ghcr.io/${{ github.repository }}" not in workflow
