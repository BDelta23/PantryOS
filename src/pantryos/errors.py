"""Core exceptions for PantryOS."""

from __future__ import annotations


class PantryOSError(Exception):
    """Base PantryOS error."""

    code = "pantryos_error"


class ValidationError(PantryOSError):
    """Input failed validation."""

    code = "validation_error"


class NotFoundError(PantryOSError):
    """Requested resource was not found."""

    code = "not_found"


class ConflictError(PantryOSError):
    """Request conflicts with current state."""

    code = "conflict"


class InsufficientInventoryError(ConflictError):
    """Inventory request exceeds usable stock."""

    code = "insufficient_inventory"

    def __init__(self, requested: str, available: str, unit: str) -> None:
        self.requested = requested
        self.available = available
        self.unit = unit
        super().__init__(f"Requested {requested} {unit}; {available} {unit} is usable.")
