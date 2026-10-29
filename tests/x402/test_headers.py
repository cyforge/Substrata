import pytest

from substrata.errors import ValidationError
from substrata.x402.headers import PaymentHTTPHeaders
from substrata.x402.models import (
    PaymentPayload,
    PaymentRequired,
    PaymentRequirements,
    ResourceInfo,
    SettlementResponse,
)

REQUIREMENTS = PaymentRequirements(
    scheme="exact",
    network="stellar:testnet",
    amount="10",
    asset="CABC",
    pay_to="GABC",
    max_timeout_seconds=60,
    extra={"areFeesSponsored": True},
)


def test_payment_http_headers_round_trip_with_case_insensitive_names() -> None:
    challenge = PaymentRequired(ResourceInfo("https://example.test/data"), (REQUIREMENTS,))
    payload = PaymentPayload(REQUIREMENTS, {"transaction": "eA=="})
    receipt = SettlementResponse(True, "hash", "stellar:testnet")
    value = PaymentHTTPHeaders(challenge, payload, receipt)
    raw = value.to_headers()
    restored = PaymentHTTPHeaders.from_headers({name.lower(): item for name, item in raw.items()})
    assert restored == value


def test_payment_http_headers_ignore_unrelated_names_and_missing_fields() -> None:
    parsed = PaymentHTTPHeaders.from_headers({"Authorization": "Bearer token"})
    assert parsed == PaymentHTTPHeaders()


def test_payment_http_headers_reject_duplicates_and_wrong_types() -> None:
    raw = PaymentHTTPHeaders(
        payment_required=PaymentRequired(ResourceInfo("https://example.test"), (REQUIREMENTS,))
    ).to_headers()
    value = raw["PAYMENT-REQUIRED"]
    with pytest.raises(ValidationError, match="duplicate"):
        PaymentHTTPHeaders.from_headers({"PAYMENT-REQUIRED": value, "payment-required": value})
    with pytest.raises(ValidationError, match="wrong model type"):
        PaymentHTTPHeaders(payment_required=REQUIREMENTS)  # type: ignore[arg-type]


def test_payment_http_headers_reject_malformed_known_header() -> None:
    with pytest.raises(ValidationError):
        PaymentHTTPHeaders.from_headers({"PAYMENT-SIGNATURE": "not-base64"})
