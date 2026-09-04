"""Typed x402 v2 wire models."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, TypeAlias, cast
from urllib.parse import urlsplit

from substrata.errors import ValidationError

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
FrozenJsonValue: TypeAlias = (
    JsonScalar | tuple["FrozenJsonValue", ...] | Mapping[str, "FrozenJsonValue"]
)
FrozenJsonObject: TypeAlias = Mapping[str, FrozenJsonValue]
MAX_STELLAR_XDR_BASE64_CHARS = 1_398_104


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValidationError(f"{name} must be a JSON object")
    return cast(Mapping[str, object], value)


def _freeze(value: object, name: str, depth: int = 0) -> FrozenJsonValue:
    if depth > 64:
        raise ValidationError(f"{name} exceeds nesting limit")
    if value is None or type(value) in (bool, str):
        return cast(FrozenJsonValue, value)
    if type(value) is int:
        if abs(value) > (1 << 63) - 1:
            raise ValidationError(f"{name} contains oversized integer")
        return value
    if type(value) is float:
        import math

        if not math.isfinite(value):
            raise ValidationError(f"{name} contains non-finite number")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, name, depth + 1) for item in value)
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValidationError(f"{name} keys must be strings")
        return MappingProxyType(
            {key: _freeze(item, name, depth + 1) for key, item in value.items()}
        )
    raise ValidationError(f"{name} must contain JSON values")


def _freeze_object(value: object, name: str) -> FrozenJsonObject:
    return cast(FrozenJsonObject, _freeze(_object(value, name), name))


def _thaw(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _http_url(value: str, name: str) -> None:
    if (
        len(value) > 2048
        or any(char.isspace() or ord(char) < 0x21 or ord(char) == 0x7F for char in value)
        or "\\" in value
        or re.search(r"%(?![0-9A-Fa-f]{2})", value)
    ):
        raise ValidationError(f"{name} must be an absolute HTTP(S) URL")
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise ValidationError(f"{name} must be an absolute HTTP(S) URL") from None
    if (
        parts.scheme not in ("http", "https")
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or "#" in value
        or parts.netloc.endswith(":")
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValidationError(f"{name} must be an absolute HTTP(S) URL")


def _network(value: str) -> None:
    namespace, separator, reference = value.partition(":")
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-_"
    if (
        not separator
        or not 3 <= len(namespace) <= 8
        or any(char not in allowed for char in namespace)
        or not 1 <= len(reference) <= 32
        or any(char not in allowed + allowed.upper() for char in reference)
    ):
        raise ValidationError("network must be a CAIP-2 identifier")


def _check_keys(data: Mapping[str, object], name: str, allowed: set[str]) -> None:
    if set(data) - allowed:
        raise ValidationError(f"{name} contains unsupported fields")


def _validate_stellar_payload(payload: FrozenJsonObject) -> None:
    if set(payload) != {"transaction"}:
        raise ValidationError("exact Stellar payload requires only transaction XDR")
    transaction = payload.get("transaction")
    if (
        not isinstance(transaction, str)
        or not transaction
        or len(transaction) > MAX_STELLAR_XDR_BASE64_CHARS
    ):
        raise ValidationError("Stellar transaction must be bounded base64 XDR")
    try:
        decoded = base64.b64decode(transaction.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise ValidationError("Stellar transaction must be valid base64 XDR") from exc
    if not decoded or base64.b64encode(decoded).decode("ascii") != transaction:
        raise ValidationError("Stellar transaction must use canonical base64 XDR")


def _string(value: object, name: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise ValidationError(f"{name} must be a{' non-empty' if nonempty else ''} string")
    return value


def _version(value: object) -> int:
    if type(value) is not int or value != 2:
        raise ValidationError("x402Version must be integer 2")
    return value


def _optional_object(data: Mapping[str, object], key: str) -> Mapping[str, object] | None:
    if key not in data:
        return None
    return _object(data[key], key)


@dataclass(frozen=True, slots=True)
class ResourceInfo:
    url: str = field(repr=False)
    description: str | None = None
    mime_type: str | None = None
    service_name: str | None = None
    tags: tuple[str, ...] | None = None
    icon_url: str | None = None

    @classmethod
    def from_dict(cls, value: object) -> ResourceInfo:
        data = _object(value, "resource")
        _check_keys(
            data,
            "resource",
            {"url", "description", "mimeType", "serviceName", "tags", "iconUrl"},
        )
        tags_value = data.get("tags")
        tags = None
        if "tags" in data:
            if not isinstance(tags_value, list):
                raise ValidationError("resource.tags must be an array")
            tags = tuple(_string(item, "resource.tags[]") for item in tags_value)
        fields = {
            "description": "resource.description",
            "mimeType": "resource.mimeType",
            "serviceName": "resource.serviceName",
            "iconUrl": "resource.iconUrl",
        }
        optional: dict[str, str | None] = {}
        for wire, label in fields.items():
            raw = data[wire] if wire in data else None
            optional[
                {
                    "description": "description",
                    "mimeType": "mime_type",
                    "serviceName": "service_name",
                    "iconUrl": "icon_url",
                }[wire]
            ] = None if wire not in data else _string(raw, label)
        return cls(url=_string(data.get("url"), "resource.url"), tags=tags, **optional)

    def __post_init__(self) -> None:
        _string(self.url, "resource.url")
        _http_url(self.url, "resource.url")
        for name in ("description", "mime_type", "service_name", "icon_url"):
            value = getattr(self, name)
            if value is not None:
                _string(value, f"resource.{name}", nonempty=False)
        if self.icon_url is not None:
            _http_url(self.icon_url, "resource.iconUrl")
        if self.tags is not None:
            if not isinstance(self.tags, (tuple, list)):
                raise ValidationError("resource.tags must be an array")
            if len(self.tags) > 5:
                raise ValidationError("resource.tags must contain at most five entries")
            for tag in self.tags:
                _string(tag, "resource.tags[]")
                if len(tag) > 32 or not tag.isascii() or not tag.isprintable():
                    raise ValidationError(
                        "resource tags must be printable ASCII up to 32 characters"
                    )
            object.__setattr__(self, "tags", tuple(self.tags))
        if self.service_name is not None and (
            len(self.service_name) > 32
            or not self.service_name.isascii()
            or not self.service_name.isprintable()
        ):
            raise ValidationError(
                "resource.serviceName must be printable ASCII up to 32 characters"
            )

    def to_dict(self) -> JsonObject:
        data: JsonObject = {"url": self.url}
        for key, value in (
            ("description", self.description),
            ("mimeType", self.mime_type),
            ("serviceName", self.service_name),
            ("iconUrl", self.icon_url),
        ):
            if value is not None:
                data[key] = value
        if self.tags is not None:
            data["tags"] = list(self.tags)
        return data


@dataclass(frozen=True, slots=True)
class PaymentRequirements:
    scheme: str
    network: str
    amount: str
    asset: str
    pay_to: str
    max_timeout_seconds: int
    extra: FrozenJsonObject | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for name in ("scheme", "network", "asset", "pay_to"):
            _string(getattr(self, name), name)
        _network(self.network)
        if (
            not isinstance(self.amount, str)
            or not self.amount.isascii()
            or not self.amount.isdigit()
        ):
            raise ValidationError("amount must be a digit string in atomic units")
        if len(self.amount) > 78 or int(self.amount) > (1 << 256) - 1:
            raise ValidationError("amount exceeds unsigned 256-bit range")
        if int(self.amount) <= 0:
            raise ValidationError("amount must be positive")
        if self.network.startswith("stellar:") and int(self.amount) > (1 << 127) - 1:
            raise ValidationError("Stellar amount exceeds signed 128-bit range")
        if (
            type(self.max_timeout_seconds) is not int
            or not 1 <= self.max_timeout_seconds <= 2_147_483_647
        ):
            raise ValidationError("maxTimeoutSeconds must be a positive 32-bit integer")
        if self.extra is not None:
            object.__setattr__(self, "extra", _freeze_object(self.extra, "extra"))
        if self.scheme == "exact" and self.network.startswith("stellar:"):
            if self.extra is None or self.extra.get("areFeesSponsored") is not True:
                raise ValidationError("exact Stellar requirements must enable fee sponsorship")

    @classmethod
    def from_dict(cls, value: object) -> PaymentRequirements:
        data = _object(value, "payment requirements")
        _check_keys(
            data,
            "payment requirements",
            {"scheme", "network", "amount", "asset", "payTo", "maxTimeoutSeconds", "extra"},
        )
        timeout = data.get("maxTimeoutSeconds")
        if type(timeout) is not int:
            raise ValidationError("maxTimeoutSeconds must be an integer")
        extra = _optional_object(data, "extra")
        return cls(
            scheme=_string(data.get("scheme"), "scheme"),
            network=_string(data.get("network"), "network"),
            amount=_string(data.get("amount"), "amount"),
            asset=_string(data.get("asset"), "asset"),
            pay_to=_string(data.get("payTo"), "payTo"),
            max_timeout_seconds=timeout,
            extra=extra,
        )

    def to_dict(self) -> JsonObject:
        data: JsonObject = {
            "scheme": self.scheme,
            "network": self.network,
            "amount": self.amount,
            "asset": self.asset,
            "payTo": self.pay_to,
            "maxTimeoutSeconds": self.max_timeout_seconds,
        }
        if self.extra is not None:
            data["extra"] = cast(JsonObject, _thaw(cast(JsonValue, self.extra)))
        return data


@dataclass(frozen=True, slots=True)
class PaymentRequired:
    resource: ResourceInfo
    accepts: tuple[PaymentRequirements, ...]
    error: str | None = None
    extensions: FrozenJsonObject = field(default_factory=dict, repr=False)
    x402_version: int = 2

    def __post_init__(self) -> None:
        _version(self.x402_version)
        if not isinstance(self.resource, ResourceInfo):
            raise ValidationError("resource must be ResourceInfo")
        if (
            not isinstance(self.accepts, (tuple, list))
            or not self.accepts
            or any(not isinstance(item, PaymentRequirements) for item in self.accepts)
        ):
            raise ValidationError("accepts must contain at least one payment requirement")
        object.__setattr__(self, "accepts", tuple(self.accepts))
        object.__setattr__(self, "extensions", _freeze_object(self.extensions, "extensions"))
        if self.error is not None:
            _string(self.error, "error", nonempty=False)

    @classmethod
    def from_dict(cls, value: object) -> PaymentRequired:
        data = _object(value, "payment required")
        _check_keys(
            data,
            "payment required",
            {"x402Version", "error", "resource", "accepts", "extensions"},
        )
        _version(data.get("x402Version"))
        accepts = data.get("accepts")
        if not isinstance(accepts, list):
            raise ValidationError("accepts must be an array")
        error = data.get("error")
        return cls(
            resource=ResourceInfo.from_dict(data.get("resource")),
            accepts=tuple(PaymentRequirements.from_dict(item) for item in accepts),
            error=None if "error" not in data else _string(error, "error", nonempty=False),
            extensions=_optional_object(data, "extensions") or {},
        )

    def to_dict(self) -> JsonObject:
        data: JsonObject = {
            "x402Version": self.x402_version,
            "resource": self.resource.to_dict(),
            "accepts": [item.to_dict() for item in self.accepts],
        }
        if self.error is not None:
            data["error"] = self.error
        if self.extensions:
            data["extensions"] = cast(JsonObject, _thaw(cast(FrozenJsonValue, self.extensions)))
        return data


@dataclass(frozen=True, slots=True)
class PaymentPayload:
    accepted: PaymentRequirements
    payload: FrozenJsonObject = field(repr=False)
    resource: ResourceInfo | None = None
    extensions: FrozenJsonObject = field(default_factory=dict, repr=False)
    x402_version: int = 2

    def __post_init__(self) -> None:
        _version(self.x402_version)
        if not isinstance(self.accepted, PaymentRequirements):
            raise ValidationError("accepted must be PaymentRequirements")
        if self.resource is not None and not isinstance(self.resource, ResourceInfo):
            raise ValidationError("resource must be ResourceInfo")
        object.__setattr__(self, "payload", _freeze_object(self.payload, "payload"))
        object.__setattr__(self, "extensions", _freeze_object(self.extensions, "extensions"))
        if self.accepted.scheme == "exact" and self.accepted.network.startswith("stellar:"):
            _validate_stellar_payload(self.payload)

    @classmethod
    def from_dict(cls, value: object) -> PaymentPayload:
        data = _object(value, "payment payload")
        _check_keys(
            data,
            "payment payload",
            {"x402Version", "resource", "accepted", "payload", "extensions"},
        )
        _version(data.get("x402Version"))
        return cls(
            accepted=PaymentRequirements.from_dict(data.get("accepted")),
            payload=_object(data.get("payload"), "payload"),
            resource=None if "resource" not in data else ResourceInfo.from_dict(data["resource"]),
            extensions=_optional_object(data, "extensions") or {},
        )

    def to_dict(self) -> JsonObject:
        data: JsonObject = {
            "x402Version": self.x402_version,
            "accepted": self.accepted.to_dict(),
            "payload": cast(JsonObject, _thaw(cast(FrozenJsonValue, self.payload))),
        }
        if self.resource is not None:
            data["resource"] = self.resource.to_dict()
        if self.extensions:
            data["extensions"] = cast(JsonObject, _thaw(cast(FrozenJsonValue, self.extensions)))
        return data

    @property
    def stellar_transaction_xdr(self) -> bytes:
        """Return decoded XDR for exact Stellar payloads."""
        if self.accepted.scheme != "exact" or not self.accepted.network.startswith("stellar:"):
            raise ValidationError("payload does not contain an exact Stellar transaction")
        transaction = self.payload["transaction"]
        assert isinstance(transaction, str)
        return base64.b64decode(transaction.encode("ascii"), validate=True)


@dataclass(frozen=True, slots=True)
class FacilitatorPaymentRequest:
    payment_payload: PaymentPayload
    payment_requirements: PaymentRequirements
    x402_version: int = 2

    def __post_init__(self) -> None:
        _version(self.x402_version)
        if not isinstance(self.payment_payload, PaymentPayload):
            raise ValidationError("paymentPayload must be PaymentPayload")
        if not isinstance(self.payment_requirements, PaymentRequirements):
            raise ValidationError("paymentRequirements must be PaymentRequirements")

    @classmethod
    def from_dict(cls, value: object) -> FacilitatorPaymentRequest:
        data = _object(value, "facilitator payment request")
        _check_keys(
            data,
            "facilitator payment request",
            {"x402Version", "paymentPayload", "paymentRequirements"},
        )
        _version(data.get("x402Version"))
        return cls(
            payment_payload=PaymentPayload.from_dict(data.get("paymentPayload")),
            payment_requirements=PaymentRequirements.from_dict(data.get("paymentRequirements")),
        )

    def to_dict(self) -> JsonObject:
        return {
            "x402Version": self.x402_version,
            "paymentPayload": self.payment_payload.to_dict(),
            "paymentRequirements": self.payment_requirements.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class VerifyResponse:
    is_valid: bool
    invalid_reason: str | None = None
    invalid_message: str | None = None
    payer: str | None = None
    extensions: FrozenJsonObject = field(default_factory=dict, repr=False)
    extra: FrozenJsonObject = field(default_factory=dict, repr=False)
    extension_responses: FrozenJsonObject = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if type(self.is_valid) is not bool:
            raise ValidationError("isValid must be a boolean")
        for name in ("invalid_reason", "invalid_message", "payer"):
            value = getattr(self, name)
            if value is not None:
                _string(value, name, nonempty=False)
        object.__setattr__(self, "extensions", _freeze_object(self.extensions, "extensions"))
        object.__setattr__(self, "extra", _freeze_object(self.extra, "extra"))
        object.__setattr__(
            self,
            "extension_responses",
            _freeze_object(self.extension_responses, "extension responses"),
        )

    @classmethod
    def from_dict(cls, value: object) -> VerifyResponse:
        data = _object(value, "verify response")
        _check_keys(
            data,
            "verify response",
            {"isValid", "invalidReason", "invalidMessage", "payer", "extensions", "extra"},
        )
        valid = data.get("isValid")
        if type(valid) is not bool:
            raise ValidationError("isValid must be a boolean")

        def optional(key: str) -> str | None:
            return None if key not in data else _string(data[key], key, nonempty=False)

        return cls(
            valid,
            optional("invalidReason"),
            optional("invalidMessage"),
            optional("payer"),
            _optional_object(data, "extensions") or {},
            _optional_object(data, "extra") or {},
        )

    def to_dict(self) -> JsonObject:
        data: JsonObject = {"isValid": self.is_valid}
        for wire, value in (
            ("invalidReason", self.invalid_reason),
            ("invalidMessage", self.invalid_message),
            ("payer", self.payer),
        ):
            if value is not None:
                data[wire] = value
        if self.extensions:
            data["extensions"] = cast(JsonObject, _thaw(cast(FrozenJsonValue, self.extensions)))
        if self.extra:
            data["extra"] = cast(JsonObject, _thaw(cast(FrozenJsonValue, self.extra)))
        return data


@dataclass(frozen=True, slots=True)
class SettlementResponse:
    success: bool
    transaction: str
    network: str
    error_reason: str | None = None
    error_message: str | None = None
    payer: str | None = None
    amount: str | None = None
    extensions: FrozenJsonObject = field(default_factory=dict, repr=False)
    extension_responses: FrozenJsonObject = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if type(self.success) is not bool:
            raise ValidationError("success must be a boolean")
        _string(self.transaction, "transaction", nonempty=False)
        _string(self.network, "network")
        _network(self.network)
        for name in ("error_reason", "error_message", "payer"):
            value = getattr(self, name)
            if value is not None:
                _string(value, name, nonempty=False)
        if self.amount is not None:
            _string(self.amount, "amount", nonempty=False)
        if self.error_reason == "settlement_pending" and not self.transaction:
            raise ValidationError("settlement_pending requires transaction hash")
        object.__setattr__(self, "extensions", _freeze_object(self.extensions, "extensions"))
        object.__setattr__(
            self,
            "extension_responses",
            _freeze_object(self.extension_responses, "extension responses"),
        )

    @classmethod
    def from_dict(cls, value: object) -> SettlementResponse:
        data = _object(value, "settlement response")
        _check_keys(
            data,
            "settlement response",
            {
                "success",
                "transaction",
                "network",
                "errorReason",
                "errorMessage",
                "payer",
                "amount",
                "extensions",
            },
        )
        success = data.get("success")
        if type(success) is not bool:
            raise ValidationError("success must be a boolean")

        def optional(key: str) -> str | None:
            return None if key not in data else _string(data[key], key, nonempty=False)

        return cls(
            success,
            _string(data.get("transaction"), "transaction", nonempty=False),
            _string(data.get("network"), "network"),
            optional("errorReason"),
            optional("errorMessage"),
            optional("payer"),
            optional("amount"),
            _optional_object(data, "extensions") or {},
        )

    def to_dict(self) -> JsonObject:
        data: JsonObject = {
            "success": self.success,
            "transaction": self.transaction,
            "network": self.network,
        }
        for wire, value in (
            ("errorReason", self.error_reason),
            ("errorMessage", self.error_message),
            ("payer", self.payer),
            ("amount", self.amount),
        ):
            if value is not None:
                data[wire] = value
        if self.extensions:
            data["extensions"] = cast(JsonObject, _thaw(cast(FrozenJsonValue, self.extensions)))
        return data


__all__ = [
    "FrozenJsonObject",
    "FrozenJsonValue",
    "FacilitatorPaymentRequest",
    "JsonObject",
    "JsonValue",
    "JsonObject",
    "JsonValue",
    "PaymentPayload",
    "PaymentRequired",
    "PaymentRequirements",
    "ResourceInfo",
    "SettlementResponse",
    "VerifyResponse",
]
