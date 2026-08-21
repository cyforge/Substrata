"""Bounded JSON and HTTP header codecs for x402 v2."""

from __future__ import annotations

import base64
import binascii
import json
import math
from collections.abc import Mapping
from typing import TypeVar

from substrata.errors import ValidationError
from substrata.x402.models import (
    JsonValue,
    PaymentPayload,
    PaymentRequired,
    SettlementResponse,
    VerifyResponse,
)

MAX_JSON_BYTES = 1_048_576
MAX_HEADER_CHARS = 1_398_104
MAX_JSON_DEPTH = 64
MAX_INTEGER = (1 << 63) - 1
WireModel = TypeVar(
    "WireModel", PaymentRequired, PaymentPayload, SettlementResponse, VerifyResponse
)
_MODEL_PARSERS = {
    PaymentRequired: PaymentRequired.from_dict,
    PaymentPayload: PaymentPayload.from_dict,
    SettlementResponse: SettlementResponse.from_dict,
    VerifyResponse: VerifyResponse.from_dict,
}


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("JSON object contains duplicate keys")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValidationError(f"JSON constant {value} is not permitted")


def _validate_tree(value: object, depth: int = 0) -> JsonValue:
    if depth > MAX_JSON_DEPTH:
        raise ValidationError("JSON nesting exceeds limit")
    if value is None or type(value) in (bool, str):
        return value
    if type(value) is int:
        if abs(value) > MAX_INTEGER:
            raise ValidationError("JSON integer exceeds signed 64-bit range")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValidationError("JSON numbers must be finite")
        return value
    if isinstance(value, list):
        return [_validate_tree(item, depth + 1) for item in value]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValidationError("JSON object keys must be strings")
        return {key: _validate_tree(item, depth + 1) for key, item in value.items()}
    raise ValidationError("value is not JSON-compatible")


def _decode_value(data: bytes | str) -> JsonValue:
    if isinstance(data, str):
        try:
            encoded = data.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValidationError("JSON text is not valid UTF-8") from exc
    elif isinstance(data, bytes):
        encoded = data
    else:
        raise ValidationError("JSON input must be bytes or text")
    if len(encoded) > MAX_JSON_BYTES:
        raise ValidationError("JSON input exceeds size limit")
    try:
        text = encoded.decode("utf-8", errors="strict")
        parsed = json.loads(text, object_pairs_hook=_object_pairs, parse_constant=_reject_constant)
    except ValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ValidationError("invalid JSON input") from exc
    return _validate_tree(parsed)


def decode_json_object(data: bytes | str) -> dict[str, JsonValue]:
    """Decode bounded JSON and require a top-level object."""
    parsed = _decode_value(data)
    if not isinstance(parsed, dict):
        raise ValidationError("JSON top level must be an object")
    return parsed


def decode_json(data: bytes | str, model_type: type[WireModel]) -> WireModel:
    """Decode bounded UTF-8 JSON into one supported wire model."""
    if model_type not in _MODEL_PARSERS:
        raise ValidationError("unsupported x402 wire model")
    return _MODEL_PARSERS[model_type](decode_json_object(data))


def encode_json(
    value: PaymentRequired | PaymentPayload | SettlementResponse | VerifyResponse,
) -> bytes:
    """Encode a wire model as compact UTF-8 JSON."""
    if type(value) not in _MODEL_PARSERS:
        raise ValidationError("unsupported x402 wire model")
    data = _validate_tree(value.to_dict())
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    if len(encoded) > MAX_JSON_BYTES:
        raise ValidationError("JSON output exceeds size limit")
    return encoded


def encode_header(
    value: PaymentRequired | PaymentPayload | SettlementResponse | VerifyResponse,
) -> str:
    """Encode an x402 wire model as a strict standard-base64 header value."""
    encoded = base64.b64encode(encode_json(value)).decode("ascii")
    if len(encoded) > MAX_HEADER_CHARS:
        raise ValidationError("header output exceeds size limit")
    return encoded


def decode_header(value: str, model_type: type[WireModel]) -> WireModel:
    """Decode a strict standard-base64 x402 header value."""
    if not isinstance(value, str) or len(value) > MAX_HEADER_CHARS:
        raise ValidationError("header value exceeds size limit")
    try:
        raw = value.encode("ascii", errors="strict")
        decoded = base64.b64decode(raw, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise ValidationError("invalid base64 header value") from exc
    return decode_json(decoded, model_type)


__all__ = [
    "MAX_HEADER_CHARS",
    "decode_json_object",
    "MAX_JSON_BYTES",
    "decode_header",
    "decode_json",
    "encode_header",
    "encode_json",
]
