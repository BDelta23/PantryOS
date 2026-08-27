"""Docker entrypoint that prepares writable paths and drops root privileges."""

from __future__ import annotations

import os
import pwd
import sys
from pathlib import Path

APP_USER = "pantryos"
WRITABLE_PATHS = (Path("/data"),)


def chown_tree(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid)
    for root, dirs, files in os.walk(path):
        root_path = Path(root)
        for name in dirs:
            os.chown(root_path / name, uid, gid)
        for name in files:
            os.chown(root_path / name, uid, gid)


def main() -> int:
    command = sys.argv[1:]
    if not command:
        print("docker_entrypoint.py requires a command", file=sys.stderr)
        return 2
    if os.name != "posix" or os.getuid() != 0:
        os.execvp(command[0], command)
    user = pwd.getpwnam(APP_USER)
    for path in WRITABLE_PATHS:
        path.mkdir(parents=True, exist_ok=True)
        chown_tree(path, user.pw_uid, user.pw_gid)
    os.setgroups([])
    os.setgid(user.pw_gid)
    os.setuid(user.pw_uid)
    os.environ.setdefault("HOME", user.pw_dir)
    os.execvp(command[0], command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
