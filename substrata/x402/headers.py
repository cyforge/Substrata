"""Typed collection for x402 HTTP payment headers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from substrata.errors import ValidationError
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
from substrata.x402.models import PaymentPayload, PaymentRequired, SettlementResponse


@dataclass(frozen=True, slots=True)
class PaymentHTTPHeaders:
    """Parsed x402 payment headers, independent of HTTP framework."""

    payment_required: PaymentRequired | None = None
    payment_signature: PaymentPayload | None = None
    payment_response: SettlementResponse | None = None

    def __post_init__(self) -> None:
        expected = (
            (self.payment_required, PaymentRequired, PAYMENT_REQUIRED_HEADER),
            (self.payment_signature, PaymentPayload, PAYMENT_SIGNATURE_HEADER),
            (self.payment_response, SettlementResponse, PAYMENT_RESPONSE_HEADER),
        )
        for value, model_type, name in expected:
            if value is not None and not isinstance(value, model_type):
                raise ValidationError(f"{name} header has wrong model type")

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> PaymentHTTPHeaders:
        if not isinstance(headers, Mapping):
            raise ValidationError("HTTP headers must be a mapping")
        values: dict[str, str] = {}
        accepted = {
            PAYMENT_REQUIRED_HEADER.lower(),
            PAYMENT_SIGNATURE_HEADER.lower(),
            PAYMENT_RESPONSE_HEADER.lower(),
        }
        for name, value in headers.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise ValidationError("HTTP header names and values must be strings")
            normalized = name.lower()
            if normalized not in accepted:
                continue
            if normalized in values:
                raise ValidationError("duplicate x402 payment header")
            values[normalized] = value
        required_name = PAYMENT_REQUIRED_HEADER.lower()
        signature_name = PAYMENT_SIGNATURE_HEADER.lower()
        response_name = PAYMENT_RESPONSE_HEADER.lower()
        return cls(
            payment_required=(
                decode_payment_required_header(values[required_name])
                if required_name in values
                else None
            ),
            payment_signature=(
                decode_payment_signature_header(values[signature_name])
                if signature_name in values
                else None
            ),
            payment_response=(
                decode_payment_response_header(values[response_name])
                if response_name in values
                else None
            ),
        )

    def to_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.payment_required is not None:
            headers[PAYMENT_REQUIRED_HEADER] = encode_payment_required_header(self.payment_required)
        if self.payment_signature is not None:
            headers[PAYMENT_SIGNATURE_HEADER] = encode_payment_signature_header(
                self.payment_signature
            )
        if self.payment_response is not None:
            headers[PAYMENT_RESPONSE_HEADER] = encode_payment_response_header(self.payment_response)
        return headers


__all__ = ["PaymentHTTPHeaders"]
