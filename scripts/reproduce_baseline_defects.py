"""Reproduce PantryOS v0.1.0 P0/P1 baseline defects.

This script intentionally passes when the known baseline defects are present. It is
Phase 0 evidence, not the final release invariant suite.
"""

from __future__ import annotations

import sys
import tempfile
import threading
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "app" / "server.py"
SPEC = spec_from_file_location("pantryos_p0_server", SERVER_PATH)
assert SPEC is not None and SPEC.loader is not None
server = module_from_spec(SPEC)
sys.modules[SPEC.name] = server
SPEC.loader.exec_module(server)


def reproduce_json_lost_update() -> list[str]:
    with tempfile.TemporaryDirectory() as directory:
        repository = server.JsonInventoryRepository(Path(directory) / "state.json")
        repository.save(server.InventoryManager())
        barrier = threading.Barrier(2)
        errors: list[str] = []

        def worker(name: str) -> None:
            try:
                def mutate(manager: object) -> None:
                    manager.add_item(
                        {
                            "name": name,
                            "quantity": 1,
                            "unit": "count",
                            "location": "Kitchen/Pantry",
                        }
                    )
                    barrier.wait(timeout=5)

                repository.mutate(mutate)
            except Exception as exc:  # noqa: BLE001 - baseline evidence script
                errors.append(repr(exc))

        threads = [
            threading.Thread(target=worker, args=(name,))
            for name in ("Milk", "Eggs")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        items = sorted(item.name for item in repository.load().state.items)
        if errors:
            raise AssertionError(f"expected successful writes, got errors: {errors}")
        if items == ["Eggs", "Milk"]:
            raise AssertionError("lost update was not reproduced")
        return items


def reproduce_additive_shopping_demand() -> str:
    manager = server.InventoryManager()
    manager.add_recipe(
        {
            "name": "Omelette",
            "ingredients": [
                {"name": "Eggs", "quantity": 3, "unit": "count"},
            ],
        }
    )

    manager.add_missing_to_shopping_list("Omelette")
    manager.add_missing_to_shopping_list("Omelette")

    shopping_item = manager.state.shopping_list[0]
    if shopping_item.quantity != server.Decimal("6"):
        raise AssertionError("additive shopping demand was not reproduced")
    return str(shopping_item.quantity)


def main() -> None:
    lost_items = reproduce_json_lost_update()
    doubled_quantity = reproduce_additive_shopping_demand()
    print("P0/P1 baseline defects reproduced")
    print(f"json_lost_update_remaining_items={lost_items}")
    print(f"repeated_recipe_demand_quantity={doubled_quantity}")


if __name__ == "__main__":
    main()
