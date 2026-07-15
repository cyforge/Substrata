from decimal import Decimal

import pytest

from substrata.errors import ValidationError
from substrata.validation import validate_amount


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
