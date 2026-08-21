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
