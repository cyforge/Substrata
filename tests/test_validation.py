from decimal import Decimal
from enum import StrEnum

import pytest

from substrata.errors import ValidationError
from substrata.validation import validate_amount, validate_enum, validate_required_string


class ExampleMode(StrEnum):
    SAFE = "safe"
    LIVE = "live"


@pytest.mark.parametrize("value", ["name", " name "])
def test_required_string_returns_nonblank_input_unchanged(value):
    assert validate_required_string(value, field_name="Name") == value


@pytest.mark.parametrize("value", [None, "", "  "])
def test_required_string_rejects_missing_or_blank_values(value):
    with pytest.raises(ValidationError, match="Name"):
        validate_required_string(value, field_name="Name")


def test_validate_enum_accepts_member_and_wire_value():
    assert validate_enum(ExampleMode.SAFE, ExampleMode, field_name="Mode") is ExampleMode.SAFE
    assert validate_enum("live", ExampleMode, field_name="Mode") is ExampleMode.LIVE


def test_validate_enum_rejects_unrecognized_value_with_choices():
    with pytest.raises(ValidationError, match="safe.*live"):
        validate_enum("unknown", ExampleMode, field_name="Mode")


@pytest.mark.parametrize("value", ["1.0000000", 1, Decimal("0.1")])
def test_validate_amount_preserves_exact_decimal(value):
    assert validate_amount(value) == Decimal(str(value))


@pytest.mark.parametrize("value", [True, False, 0.1, "nan", "Infinity", "-1", "0", "1.00000001"])
def test_validate_amount_rejects_invalid_payment_values(value):
    with pytest.raises(ValidationError):
        validate_amount(value)


def test_validate_amount_allows_nonnegative_zero_for_fee_fields():
    assert validate_amount(0, positive=False) == Decimal(0)
    with pytest.raises(ValidationError):
        validate_amount(Decimal("-0.1"), positive=False)
