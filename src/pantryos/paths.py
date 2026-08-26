"""Path policy helpers for local PantryOS operations."""

from __future__ import annotations

from pathlib import Path

from .errors import ValidationError


def path_within(candidate: Path | str, root: Path | str, label: str) -> Path:
    """Resolve candidate and require it to stay inside root."""
    root_path = Path(root).expanduser().resolve()
    candidate_path = Path(candidate).expanduser()
    if not candidate_path.is_absolute():
        candidate_path = Path.cwd() / candidate_path
    resolved = candidate_path.resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise ValidationError(f"{label} must be inside {root_path}") from exc
    return resolved
