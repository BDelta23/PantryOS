"""Decimal-safe unit handling."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .errors import ValidationError


@dataclass(frozen=True, slots=True)
class UnitDef:
    code: str
    dimension: str
    to_base: Decimal


UNITS: dict[str, UnitDef] = {
    "mg": UnitDef("mg", "mass", Decimal("0.001")),
    "g": UnitDef("g", "mass", Decimal("1")),
    "kg": UnitDef("kg", "mass", Decimal("1000")),
    "oz": UnitDef("oz", "mass", Decimal("28.349523125")),
    "lb": UnitDef("lb", "mass", Decimal("453.59237")),
    "ml": UnitDef("ml", "volume", Decimal("1")),
    "l": UnitDef("l", "volume", Decimal("1000")),
    "tsp": UnitDef("tsp", "volume", Decimal("4.92892159375")),
    "tbsp": UnitDef("tbsp", "volume", Decimal("14.78676478125")),
    "fl oz": UnitDef("fl oz", "volume", Decimal("29.5735295625")),
    "cup": UnitDef("cup", "volume", Decimal("236.5882365")),
    "pint": UnitDef("pint", "volume", Decimal("473.176473")),
    "quart": UnitDef("quart", "volume", Decimal("946.352946")),
    "gallon": UnitDef("gallon", "volume", Decimal("3785.411784")),
    "count": UnitDef("count", "count", Decimal("1")),
    "each": UnitDef("each", "count", Decimal("1")),
    "dozen": UnitDef("dozen", "count", Decimal("12")),
    "serving": UnitDef("serving", "serving", Decimal("1")),
    "bag": UnitDef("bag", "package", Decimal("1")),
    "can": UnitDef("can", "package", Decimal("1")),
    "bottle": UnitDef("bottle", "package", Decimal("1")),
    "package": UnitDef("package", "package", Decimal("1")),
    "bunch": UnitDef("bunch", "package", Decimal("1")),
    "slice": UnitDef("slice", "package", Decimal("1")),
    "other": UnitDef("other", "other", Decimal("1")),
}

ALIASES = {
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "ounces": "oz",
    "ounce": "oz",
    "grams": "g",
    "gram": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "liter": "l",
    "liters": "l",
    "milliliter": "ml",
    "milliliters": "ml",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "cups": "cup",
    "ct": "count",
    "ea": "each",
}


def decimal_value(value: int | float | str | Decimal) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"Invalid decimal value: {value!r}") from exc
    if not number.is_finite():
        raise ValidationError("Quantity must be finite")
    return number


def require_positive(value: int | float | str | Decimal, field: str = "quantity") -> Decimal:
    number = decimal_value(value)
    if number <= 0:
        raise ValidationError(f"{field} must be greater than zero")
    return number


def require_non_negative(value: int | float | str | Decimal, field: str = "quantity") -> Decimal:
    number = decimal_value(value)
    if number < 0:
        raise ValidationError(f"{field} cannot be negative")
    return number


def unit_code(value: str | None) -> str:
    code = "count" if value is None else " ".join(str(value).casefold().strip().split())
    code = ALIASES.get(code, code)
    if code not in UNITS:
        raise ValidationError(f"Unsupported unit: {value}")
    return code


def assert_compatible(from_unit: str, to_unit: str) -> None:
    source = UNITS[unit_code(from_unit)]
    target = UNITS[unit_code(to_unit)]
    if source.dimension != target.dimension:
        raise ValidationError(f"Cannot convert {from_unit} to {to_unit}: incompatible dimensions")


def convert(value: Decimal, from_unit: str, to_unit: str) -> Decimal:
    source = UNITS[unit_code(from_unit)]
    target = UNITS[unit_code(to_unit)]
    if source.dimension != target.dimension:
        raise ValidationError(f"Cannot convert {from_unit} to {to_unit}: incompatible dimensions")
    return (value * source.to_base / target.to_base).normalize()


def decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")
