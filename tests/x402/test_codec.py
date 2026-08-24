import base64

import pytest

from substrata.errors import ValidationError
from substrata.x402.codec import decode_header, decode_json, encode_header, encode_json
from substrata.x402.models import PaymentPayload, PaymentRequired

REQ = {
    "scheme": "exact",
    "network": "stellar:testnet",
    "amount": "25",
    "asset": "CABC",
    "payTo": "GABC",
    "maxTimeoutSeconds": 60,
}


def required() -> PaymentRequired:
    return PaymentRequired.from_dict(
        {
            "x402Version": 2,
            "resource": {"url": "https://example.test"},
            "accepts": [REQ],
        }
    )


def test_json_and_base64_headers_round_trip() -> None:
    model = required()
    assert decode_json(encode_json(model), PaymentRequired) == model
    assert decode_header(encode_header(model), PaymentRequired) == model
    payload = PaymentPayload.from_dict(
        {"x402Version": 2, "accepted": REQ, "payload": {"transaction": "abc"}}
    )
    assert decode_header(encode_header(payload), PaymentPayload) == payload


@pytest.mark.parametrize(
    "wire",
    [
        b'{"x402Version":2,"x402Version":2}',
        b'{"x402Version":2,"resource":{"url":"x"},"accepts":[],"x":NaN}',
        b'{"x402Version":2,"resource":{"url":"x"},"accepts":[],"n":1e999}',
        b'{"x402Version":2,"resource":{"url":"x"},"accepts":[],"n":9223372036854775808}',
        b"\xff",
    ],
)
def test_json_rejects_ambiguous_or_invalid_values(wire: bytes) -> None:
    with pytest.raises(ValidationError):
        decode_json(wire, PaymentRequired)


def test_header_requires_strict_standard_base64() -> None:
    for value in ("%%%", "e30", "YWJj====", "é"):
        with pytest.raises(ValidationError):
            decode_header(value, PaymentRequired)


def test_header_rejects_payload_larger_than_decoded_json_limit() -> None:
    value = base64.b64encode(b" " * (1_048_577)).decode("ascii")
    with pytest.raises(ValidationError):
        decode_header(value, PaymentRequired)


def test_depth_bounded_before_model_validation() -> None:
    nested = "[" * 66 + "0" + "]" * 66
    with pytest.raises(ValidationError):
        decode_json(nested, PaymentRequired)


def test_named_http_headers_bind_expected_wire_models() -> None:
    from substrata.x402.codec import (
        PAYMENT_REQUIRED_HEADER,
        PAYMENT_RESPONSE_HEADER,
        PAYMENT_SIGNATURE_HEADER,
        decode_payment_required_header,
        decode_payment_response_header,
        decode_payment_signature_header,
        encode_payment_required_header,
        encode_payment_response_header,
        encode_payment_signature_header,
    )
    from substrata.x402.models import SettlementResponse

    challenge = PaymentRequired.from_dict(
        {
            "x402Version": 2,
            "resource": {"url": "https://example.test"},
            "accepts": [REQ],
        }
    )
    payment = PaymentPayload.from_dict(
        {"x402Version": 2, "accepted": REQ, "payload": {"transaction": "xdr"}}
    )
    receipt = SettlementResponse(True, "hash", "stellar:testnet")
    assert PAYMENT_REQUIRED_HEADER == "PAYMENT-REQUIRED"
    assert PAYMENT_SIGNATURE_HEADER == "PAYMENT-SIGNATURE"
    assert PAYMENT_RESPONSE_HEADER == "PAYMENT-RESPONSE"
    assert decode_payment_required_header(encode_payment_required_header(challenge)) == challenge
    assert decode_payment_signature_header(encode_payment_signature_header(payment)) == payment
    assert decode_payment_response_header(encode_payment_response_header(receipt)) == receipt
    with pytest.raises(ValidationError):
        encode_payment_required_header(payment)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        decode_payment_response_header(encode_payment_signature_header(payment))


def test_extension_response_header_uses_bounded_json_object_codec() -> None:
    from substrata.x402.codec import (
        decode_extension_responses_header,
        encode_extension_responses_header,
    )

    result = {"bazaar": {"item": "abc", "accepted": True}}
    assert decode_extension_responses_header(encode_extension_responses_header(result)) == result
    malformed = base64.b64encode(b'{"bazaar":{},"bazaar":{}}').decode()
    with pytest.raises(ValidationError):
        decode_extension_responses_header(malformed)
