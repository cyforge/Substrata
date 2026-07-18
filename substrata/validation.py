"""Validation helpers shared by Substrata's domain objects."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import TypeAlias, TypeVar

from .errors import ValidationError

AmountInput: TypeAlias = str | int | Decimal
EnumValue = TypeVar("EnumValue", bound=Enum)


def validate_required_string(value: str, *, field_name: str) -> str:
    """Return non-blank string unchanged or raise a field-specific error."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string")
    return value


def validate_enum(
    value: EnumValue | str, enum_type: type[EnumValue], *, field_name: str
) -> EnumValue:
    """Parse enum member or its value and report invalid values uniformly."""
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (ValueError, TypeError) as exc:
        choices = ", ".join(repr(member.value) for member in enum_type)
        raise ValidationError(f"{field_name} must be one of: {choices}") from exc


def validate_amount(value: AmountInput, *, positive: bool = True) -> Decimal:
    """Return exact monetary value; reject floats and values beyond Stellar precision."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ValidationError("Amount must be supplied as str, int, or Decimal")
    if not isinstance(value, (str, int, Decimal)):
        raise ValidationError("Amount must be supplied as str, int, or Decimal")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("Amount is not a valid decimal") from exc
    if not amount.is_finite():
        raise ValidationError("Amount must be finite")
    parts = amount.as_tuple()
    if abs(parts.exponent) > 38 or len(parts.digits) > 38 or amount.adjusted() >= 38:
        raise ValidationError("Amount exceeds supported decimal precision or magnitude")
    if positive and amount <= 0:
        raise ValidationError("Amount must be greater than zero")
    if not positive and amount < 0:
        raise ValidationError("Amount cannot be negative")
    if max(0, -amount.as_tuple().exponent) > 7:
        raise ValidationError("Amount cannot have more than 7 fractional digits")
    return amount


def amount_to_atomic_units(amount: Decimal) -> int:
    """Convert validated seven-place decimal amount to exact integer stroops."""
    normalized = validate_amount(amount, positive=False)
    parts = normalized.as_tuple()
    coefficient = int("".join(map(str, parts.digits)) or "0")
    units = coefficient * 10 ** (parts.exponent + 7)
    return -units if parts.sign else units
