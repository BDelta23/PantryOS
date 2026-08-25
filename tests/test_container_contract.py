from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_drops_runtime_to_pantryos_user_and_limits_writable_volume() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    entrypoint = (ROOT / "scripts" / "docker_entrypoint.py").read_text(encoding="utf-8")

    assert "--uid 10001" in dockerfile
    assert "--gid 10001" in dockerfile
    assert "ENTRYPOINT" in dockerfile
    assert "scripts/docker_entrypoint.py" in dockerfile
    assert "os.setuid(user.pw_uid)" in entrypoint
    assert "os.setgid(user.pw_gid)" in entrypoint
    assert "def chown_tree" in entrypoint
    assert "os.walk(path)" in entrypoint
    assert "Path(\"/app/data\")" in entrypoint
    assert "pantryos-data:/app/data" in compose