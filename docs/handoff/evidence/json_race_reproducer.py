"""Reproduce the baseline JsonInventoryRepository lost-update race.

Run from the repository root before the JSON repository is removed:
    python docs/handoff/evidence/json_race_reproducer.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SERVER_PATH = ROOT / "app" / "server.py"
SPEC = spec_from_file_location("pantryos_race_server", SERVER_PATH)
assert SPEC is not None and SPEC.loader is not None
server = module_from_spec(SPEC)
sys.modules[SPEC.name] = server
SPEC.loader.exec_module(server)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = server.JsonInventoryRepository(Path(directory) / "state.json")
        repository.save(server.InventoryManager())
        barrier = threading.Barrier(2)
        errors: list[str] = []

        def worker(name: str) -> None:
            try:

                def mutate(manager: object) -> None:
                    manager.add_item(  # type: ignore[attr-defined]
                        {
                            "name": name,
                            "quantity": 1,
                            "unit": "count",
                            "location": "Kitchen/Pantry",
                        }
                    )
                    barrier.wait(timeout=5)

                repository.mutate(mutate)
            except Exception as exc:  # noqa: BLE001 - evidence script
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(name,)) for name in ("Milk", "Eggs")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        items = sorted(item.name for item in repository.load().state.items)
        print(f"errors: {errors}")
        print(f"items: {items}")
        if items != ["Eggs", "Milk"]:
            raise SystemExit("Lost update reproduced: both successful writes were not preserved")


if __name__ == "__main__":
    main()
