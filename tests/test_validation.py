from decimal import Decimal
from enum import StrEnum

import pytest

from substrata.errors import ValidationError
from substrata.validation import (
    amount_to_atomic_units,
    normalize_origin,
    validate_amount,
    validate_enum,
    validate_required_string,
)


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


@pytest.mark.parametrize(
    ("origin", "canonical"),
    [
        ("HTTPS://Example.COM:443/", "https://example.com"),
        ("http://[2001:db8::1]:80", "http://[2001:db8::1]"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("https://bücher.example", "https://xn--bcher-kva.example"),
    ],
)
def test_origin_normalization_canonicalizes_web_authority(origin, canonical):
    assert normalize_origin(origin) == canonical


@pytest.mark.parametrize(
    "origin",
    [
        "file:///tmp/data",
        "https://user@example.com",
        "https://example.com/path",
        "https://example.com?query=1",
        "https://example.com:70000",
        "https://example.com:",
        "https://bad_host.example",
        "https://example.com..",
    ],
)
def test_origin_normalization_rejects_non_origins(origin):
    with pytest.raises(ValidationError):
        normalize_origin(origin)


@pytest.mark.parametrize("value", ["1.0000000", 1, Decimal("0.1")])
def test_validate_amount_preserves_exact_decimal(value):
    assert validate_amount(value) == Decimal(str(value))


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        0.1,
        "nan",
        "Infinity",
        "-1",
        "0",
        "1.00000001",
        "1e1000000000",
        "0e1000000000",
    ],
)
def test_validate_amount_rejects_invalid_payment_values(value):
    with pytest.raises(ValidationError):
        validate_amount(value)


def test_validate_amount_allows_nonnegative_zero_for_fee_fields():
    assert validate_amount(0, positive=False) == Decimal(0)
    with pytest.raises(ValidationError):
        validate_amount(Decimal("-0.1"), positive=False)


def test_amount_to_atomic_units_uses_exact_integer_conversion():
    assert amount_to_atomic_units(Decimal("1.2300001")) == 12_300_001
