"""Synchronous x402 v2 facilitator interface and bounded HTTP adapter."""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol
from urllib.parse import urlsplit

from substrata.errors import IntegrationError, ValidationError
from substrata.x402.codec import MAX_JSON_BYTES, decode_json, decode_json_object
from substrata.x402.models import (
    JsonObject,
    JsonValue,
    PaymentPayload,
    PaymentRequirements,
    SettlementResponse,
    VerifyResponse,
)

MAX_TIMEOUT_SECONDS = 300.0


class FacilitatorError(IntegrationError):
    """Facilitator request failed or returned invalid protocol data."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message, details={"status_code": status_code} if status_code else None)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status: int
    body: bytes


class Transport(Protocol):
    """Transport boundary injectable for deterministic offline tests."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        max_response_bytes: int,
    ) -> TransportResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class UrllibTransport:
    """Standard-library HTTP transport that rejects redirects and caps reads."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect())

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        max_response_bytes: int,
    ) -> TransportResponse:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            response = self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            # Redirect responses are returned for normal status handling; no location is followed.
            if 300 <= error.code < 400:
                return TransportResponse(error.code, b"")
            try:
                raw = error.read(max_response_bytes + 1)
            finally:
                error.close()
            if len(raw) > max_response_bytes:
                raise FacilitatorError(
                    "facilitator response exceeds size limit", status_code=error.code
                ) from None
            return TransportResponse(error.code, raw)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise FacilitatorError("facilitator transport failed") from None
        try:
            raw = response.read(max_response_bytes + 1)
            status = response.status
        finally:
            response.close()
        if len(raw) > max_response_bytes:
            raise FacilitatorError("facilitator response exceeds size limit", status_code=status)
        return TransportResponse(status, raw)


def _freeze_json(value: object) -> JsonValue:
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    raise ValidationError("supported response contains invalid JSON")


def _freeze_object(value: dict[str, object]) -> JsonObject:
    return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})


def _thaw(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class SupportedKind:
    x402_version: int
    scheme: str
    network: str
    extra: JsonObject | None = None

    @classmethod
    def from_dict(cls, value: object) -> SupportedKind:
        if not isinstance(value, dict):
            raise ValidationError("supported kind must be an object")
        version = value.get("x402Version")
        scheme = value.get("scheme")
        network = value.get("network")
        if type(version) is not int or version != 2:
            raise ValidationError("supported kind x402Version must be integer 2")
        if not isinstance(scheme, str) or not scheme or not isinstance(network, str) or not network:
            raise ValidationError("supported kind scheme and network must be non-empty strings")
        extra = value.get("extra")
        if extra is not None:
            if not isinstance(extra, dict):
                raise ValidationError("supported kind extra must be an object")
            extra = _freeze_object(extra)
        return cls(version, scheme, network, extra)

    def to_dict(self) -> JsonObject:
        data: JsonObject = {
            "x402Version": self.x402_version,
            "scheme": self.scheme,
            "network": self.network,
        }
        if self.extra is not None:
            data["extra"] = _thaw(self.extra)
        return data


@dataclass(frozen=True, slots=True)
class SupportedResponse:
    kinds: tuple[SupportedKind, ...]
    extensions: tuple[str, ...]
    signers: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        if not isinstance(self.kinds, tuple) or any(
            not isinstance(item, SupportedKind) for item in self.kinds
        ):
            raise ValidationError("supported.kinds must contain SupportedKind values")
        if not isinstance(self.extensions, tuple) or any(
            not isinstance(item, str) or not item for item in self.extensions
        ):
            raise ValidationError("supported.extensions must contain non-empty strings")
        if not isinstance(self.signers, Mapping):
            raise ValidationError("supported.signers must be a mapping")
        copied: dict[str, tuple[str, ...]] = {}
        for pattern, addresses in self.signers.items():
            if not isinstance(pattern, str) or not pattern or not isinstance(addresses, tuple):
                raise ValidationError("supported.signers must map patterns to address tuples")
            if any(not isinstance(address, str) or not address for address in addresses):
                raise ValidationError("supported.signers addresses must be non-empty strings")
            copied[pattern] = tuple(addresses)
        object.__setattr__(self, "signers", MappingProxyType(copied))

    @classmethod
    def from_dict(cls, value: object) -> SupportedResponse:
        if not isinstance(value, dict):
            raise ValidationError("supported response must be an object")
        kinds = value.get("kinds")
        extensions = value.get("extensions")
        signers = value.get("signers")
        if not isinstance(kinds, list):
            raise ValidationError("supported.kinds must be an array")
        if not isinstance(extensions, list) or any(
            not isinstance(item, str) or not item for item in extensions
        ):
            raise ValidationError("supported.extensions must be an array of non-empty strings")
        if not isinstance(signers, dict):
            raise ValidationError("supported.signers must be an object")
        typed_signers: dict[str, tuple[str, ...]] = {}
        for pattern, addresses in signers.items():
            if not isinstance(pattern, str) or not pattern or not isinstance(addresses, list):
                raise ValidationError("supported.signers must map patterns to address arrays")
            if any(not isinstance(address, str) or not address for address in addresses):
                raise ValidationError("supported.signers addresses must be non-empty strings")
            typed_signers[pattern] = tuple(addresses)
        return cls(
            tuple(SupportedKind.from_dict(item) for item in kinds), tuple(extensions), typed_signers
        )

    def to_dict(self) -> JsonObject:
        return {
            "kinds": [kind.to_dict() for kind in self.kinds],
            "extensions": list(self.extensions),
            "signers": {pattern: list(addresses) for pattern, addresses in self.signers.items()},
        }


class Facilitator(Protocol):
    def verify(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> VerifyResponse: ...

    def settle(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> SettlementResponse: ...

    def supported(self) -> SupportedResponse: ...


def _validate_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme not in ("https", "http")
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise ValidationError("facilitator URL must be an absolute HTTPS URL")
    if parts.query or parts.fragment:
        raise ValidationError("facilitator URL cannot include query or fragment")
    if parts.scheme == "http":
        host = parts.hostname.lower()
        loopback = host == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if not loopback:
            raise ValidationError("HTTP facilitator URL is allowed only for loopback development")
    return url.rstrip("/")


class HTTPFacilitator:
    """x402 facilitator HTTP client with bounded calls and no settlement retries."""

    def __init__(
        self,
        url: str,
        *,
        api_key: str | None = None,
        timeout: float = 10.0,
        max_response_bytes: int = MAX_JSON_BYTES,
        transport: Transport | None = None,
    ) -> None:
        self._url = _validate_url(url)
        if api_key is not None and (not isinstance(api_key, str) or not api_key.strip()):
            raise ValidationError("api_key must be a non-empty string")
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not 0 < timeout <= MAX_TIMEOUT_SECONDS
        ):
            raise ValidationError("timeout must be greater than zero and at most 300 seconds")
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= MAX_JSON_BYTES:
            raise ValidationError("max_response_bytes must be between 1 byte and 1 MiB")
        self._api_key = api_key
        self._timeout = float(timeout)
        self._max_response_bytes = max_response_bytes
        self._transport = transport or UrllibTransport()

    def _post(self, endpoint: str, request_body: JsonObject) -> bytes:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            request_bytes = json.dumps(request_body, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
        except (TypeError, ValueError, RecursionError):
            raise ValidationError("facilitator request contains invalid JSON") from None
        if len(request_bytes) > MAX_JSON_BYTES:
            raise ValidationError("facilitator request exceeds size limit")
        try:
            response = self._transport.request(
                "POST",
                f"{self._url}/{endpoint}",
                headers=headers,
                body=request_bytes,
                timeout=self._timeout,
                max_response_bytes=self._max_response_bytes,
            )
        except FacilitatorError:
            raise
        except Exception:
            # Do not echo transport exceptions: they may contain the authenticated URL or headers.
            raise FacilitatorError("facilitator transport failed") from None
        return self._checked_body(response)

    def _get(self, endpoint: str) -> bytes:
        headers = {"Accept": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = self._transport.request(
                "GET",
                f"{self._url}/{endpoint}",
                headers=headers,
                body=None,
                timeout=self._timeout,
                max_response_bytes=self._max_response_bytes,
            )
        except FacilitatorError:
            raise
        except Exception:
            raise FacilitatorError("facilitator transport failed") from None
        return self._checked_body(response)

    def _checked_body(self, response: TransportResponse) -> bytes:
        if (
            not isinstance(response, TransportResponse)
            or type(response.status) is not int
            or response.status < 100
            or response.status > 599
        ):
            raise FacilitatorError("facilitator transport returned an invalid response")
        if not 200 <= response.status < 300:
            raise FacilitatorError(
                "facilitator returned unsuccessful HTTP status", status_code=response.status
            )
        if not isinstance(response.body, bytes) or len(response.body) > self._max_response_bytes:
            raise FacilitatorError(
                "facilitator response exceeds size limit", status_code=response.status
            )
        return response.body

    def verify(self, payload: PaymentPayload, requirements: PaymentRequirements) -> VerifyResponse:
        if not isinstance(payload, PaymentPayload) or not isinstance(
            requirements, PaymentRequirements
        ):
            raise ValidationError("verify requires typed payment payload and requirements")
        body = self._post(
            "verify",
            {
                "x402Version": 2,
                "paymentPayload": payload.to_dict(),
                "paymentRequirements": requirements.to_dict(),
            },
        )
        try:
            return decode_json(body, VerifyResponse)
        except ValidationError:
            raise FacilitatorError("facilitator returned invalid verify response") from None

    def settle(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> SettlementResponse:
        if not isinstance(payload, PaymentPayload) or not isinstance(
            requirements, PaymentRequirements
        ):
            raise ValidationError("settle requires typed payment payload and requirements")
        body = self._post(
            "settle",
            {
                "x402Version": 2,
                "paymentPayload": payload.to_dict(),
                "paymentRequirements": requirements.to_dict(),
            },
        )
        try:
            return decode_json(body, SettlementResponse)
        except ValidationError:
            raise FacilitatorError("facilitator returned invalid settlement response") from None

    def supported(self) -> SupportedResponse:
        body = self._get("supported")
        try:
            data = decode_json_object(body)
            return SupportedResponse.from_dict(data)
        except ValidationError:
            raise FacilitatorError("facilitator returned invalid supported response") from None


__all__ = [
    "Facilitator",
    "FacilitatorError",
    "HTTPFacilitator",
    "SupportedKind",
    "SupportedResponse",
    "Transport",
    "TransportResponse",
    "UrllibTransport",
]
