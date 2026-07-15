"""Validation helpers shared by Substrata's domain objects."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TypeAlias

from .errors import ValidationError

AmountInput: TypeAlias = str | int | Decimal


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
    if positive and amount <= 0:
        raise ValidationError("Amount must be greater than zero")
    if not positive and amount < 0:
        raise ValidationError("Amount cannot be negative")
    if max(0, -amount.as_tuple().exponent) > 7:
        raise ValidationError("Amount cannot have more than 7 fractional digits")
    return amount
