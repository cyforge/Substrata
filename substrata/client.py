"""Policy-controlled x402 HTTP buyer client."""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import ssl
from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from substrata.constants import Network
from substrata.errors import ValidationError
from substrata.models import Asset, PaymentRequest, PaymentResult, PaymentStatus, PolicyDecision
from substrata.policy import PolicyEngine, SpendPolicy
from substrata.storage import Ledger
from substrata.validation import amount_from_atomic_units, origin_from_url
from substrata.x402.codec import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    decode_payment_required_header,
    decode_payment_response_header,
    encode_payment_signature_header,
)
from substrata.x402.models import (
    PaymentPayload,
    PaymentRequired,
    PaymentRequirements,
    ResourceInfo,
    SettlementResponse,
)


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class ClientResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    payment: PaymentResult | None = None


class Transport(Protocol):
    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        max_response_bytes: int,
    ) -> HTTPResponse: ...


class PaymentSigner(Protocol):
    def supports(self, requirements: PaymentRequirements) -> bool: ...

    def sign(self, requirements: PaymentRequirements, resource: ResourceInfo) -> PaymentPayload: ...


class HTTPTransport:
    """Stdlib HTTP transport with redirects disabled and bounded response bodies."""

    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        max_response_bytes: int,
    ) -> HTTPResponse:
        parts = _parse_url(url)
        connection_type = (
            http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        )
        connection = (
            connection_type(
                parts.hostname,
                parts.port,
                timeout=timeout,
                context=ssl.create_default_context() if parts.scheme == "https" else None,
            )
            if parts.scheme == "https"
            else connection_type(parts.hostname, parts.port, timeout=timeout)
        )
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        try:
            connection.request(method, target, body=body, headers=dict(headers))
            response = connection.getresponse()
            data = response.read(max_response_bytes + 1)
            if len(data) > max_response_bytes:
                raise ValidationError("HTTP response exceeds configured size limit")
            return HTTPResponse(
                response.status, {key.lower(): value for key, value in response.getheaders()}, data
            )
        except (OSError, http.client.HTTPException, socket.timeout, ssl.SSLError) as exc:
            raise TransportError("HTTP request failed") from exc
        finally:
            connection.close()


class TransportError(RuntimeError):
    """Transport failure with no response whose payment outcome may be unknown."""


class BuyerClient:
    def __init__(
        self,
        policy: SpendPolicy,
        signer: PaymentSigner,
        *,
        ledger: Ledger | None = None,
        transport: Transport | None = None,
        timeout: float = 10.0,
        max_response_bytes: int = 1_048_576,
        ledger_path: str | None = None,
        allow_ephemeral_ledger: bool = False,
    ) -> None:
        if not isinstance(policy, SpendPolicy):
            raise ValidationError("policy must be SpendPolicy")
        if not callable(getattr(signer, "sign", None)):
            raise ValidationError("signer must provide sign(requirements, resource)")
        if type(timeout) not in (int, float) or not 0 < timeout <= 300:
            raise ValidationError("timeout must be between 0 and 300 seconds")
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 16_777_216:
            raise ValidationError("max_response_bytes must be between 1 and 16777216")
        if ledger is not None and ledger_path is not None:
            raise ValidationError("pass ledger or ledger_path, not both")
        if type(allow_ephemeral_ledger) is not bool:
            raise ValidationError("allow_ephemeral_ledger must be bool")
        if ledger is None and ledger_path is None and not allow_ephemeral_ledger:
            raise ValidationError(
                "pass a ledger or ledger_path; ephemeral ledger requires explicit opt-in"
            )
        self.policy = policy
        self._policy_engine = PolicyEngine(policy)
        self.signer = signer
        self.ledger = ledger if ledger is not None else Ledger(ledger_path or ":memory:")
        self._owns_ledger = ledger is None
        self.transport = transport or HTTPTransport()
        self.timeout = float(timeout)
        self.max_response_bytes = max_response_bytes
        self._closed = False

    def get(
        self, url: str, *, headers: Mapping[str, str] | None = None, request_id: str | None = None
    ) -> ClientResponse:
        return self.request("GET", url, headers=headers, request_id=request_id)

    def post(
        self,
        url: str,
        body: bytes,
        *,
        headers: Mapping[str, str] | None = None,
        request_id: str | None = None,
        replayable: bool = False,
    ) -> ClientResponse:
        return self.request(
            "POST", url, body=body, headers=headers, request_id=request_id, replayable=replayable
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        request_id: str | None = None,
        replayable: bool = False,
    ) -> ClientResponse:
        self._ensure_open()
        if (
            not isinstance(method, str)
            or not method
            or method.upper() != method
            or not method.isalpha()
        ):
            raise ValidationError("method must be uppercase ASCII letters")
        if method not in {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValidationError("unsupported HTTP method")
        if method in {"GET", "HEAD", "OPTIONS"} and body not in (None, b""):
            raise ValidationError(f"{method} request cannot include a body")
        if body is not None and not isinstance(body, bytes):
            raise ValidationError("request body must be immutable bytes")
        if method not in {"GET", "HEAD", "OPTIONS"} and body is not None and not replayable:
            # The initial request is safe; explicit opt-in permits its one paid replay.
            pass
        if method in {"POST", "PUT", "PATCH", "DELETE"} and not replayable:
            can_pay = False
        else:
            can_pay = True
        _parse_url(url)
        request_headers = _headers(headers)
        if _has_header(request_headers, PAYMENT_SIGNATURE_HEADER):
            raise ValidationError("PAYMENT-SIGNATURE cannot be supplied by the caller")
        original = self.transport.send(
            method, url, request_headers, body, self.timeout, self.max_response_bytes
        )
        if original.status_code != 402:
            return ClientResponse(original.status_code, original.headers, original.body)
        if not can_pay:
            return ClientResponse(original.status_code, original.headers, original.body)
        if not _has_header(original.headers, PAYMENT_REQUIRED_HEADER):
            return ClientResponse(original.status_code, original.headers, original.body)
        challenge = decode_payment_required_header(
            _header(original.headers, PAYMENT_REQUIRED_HEADER)
        )
        return self._pay_retry(method, url, body, request_headers, request_id, challenge)

    def _pay_retry(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: dict[str, str],
        request_id: str | None,
        challenge: PaymentRequired,
    ) -> ClientResponse:
        origin = origin_from_url(url)
        if origin_from_url(challenge.resource.url) != origin or challenge.resource.url != url:
            raise ValidationError("payment resource must exactly match requested URL")
        requirement = self._choose(challenge.accepts)
        request = _payment_request(requirement, challenge.resource.url, origin, request_id)
        spent_total = self.ledger.spent(request.currency_key)
        spent_today = self.ledger.spent_today(request.currency_key)
        decision = self._policy_engine.evaluate(
            request, spent_total=spent_total, spent_today=spent_today
        )
        if not decision.allowed:
            return ClientResponse(402, {}, _policy_error(decision))
        fingerprint = _fingerprint(method, url, body, requirement, challenge.resource)
        reservation = self.ledger.reserve_once(
            request.request_id,
            request.amount,
            currency=request.currency_key,
            limit=self.policy.total_limit,
            daily_limit=self.policy.daily_limit,
            request_fingerprint=fingerprint,
            metadata={"origin": origin, "resource": url, "method": method},
        )
        if not reservation.created:
            raise ValidationError(
                "request ID already has a ledger reservation; payment will not be resent"
            )
        try:
            payload = self.signer.sign(requirement, challenge.resource)
        except Exception:
            self.ledger.release(request.request_id, reason="signer failed before paid request")
            raise
        if payload.accepted != requirement or payload.resource != challenge.resource:
            self.ledger.release(request.request_id, reason="signer returned mismatched offer")
            raise ValidationError("signer returned payload for a different offer or resource")
        paid_headers = dict(headers)
        paid_headers[PAYMENT_SIGNATURE_HEADER] = encode_payment_signature_header(payload)
        try:
            response = self.transport.send(
                method, url, paid_headers, body, self.timeout, self.max_response_bytes
            )
        except Exception:
            self.ledger.mark_uncertain(
                request.request_id, reason="paid retry transport outcome unknown"
            )
            raise
        receipt_value = _maybe_header(response.headers, PAYMENT_RESPONSE_HEADER)
        if receipt_value is None:
            self.ledger.mark_uncertain(
                request.request_id, reason="paid response omitted settlement receipt"
            )
            return ClientResponse(
                response.status_code,
                response.headers,
                response.body,
                PaymentResult(
                    request.request_id,
                    PaymentStatus.UNCERTAIN,
                    amount=request.amount,
                    asset=request.asset,
                    network=request.network,
                    reason="missing settlement receipt",
                ),
            )
        try:
            receipt = decode_payment_response_header(receipt_value)
            _validate_receipt(receipt, requirement, payload)
        except ValidationError:
            self.ledger.mark_uncertain(request.request_id, reason="paid response receipt invalid")
            return ClientResponse(
                response.status_code,
                response.headers,
                response.body,
                PaymentResult(
                    request.request_id,
                    PaymentStatus.UNCERTAIN,
                    amount=request.amount,
                    asset=request.asset,
                    network=request.network,
                    reason="invalid settlement receipt",
                ),
            )
        if response.status_code < 200 or response.status_code >= 300:
            self.ledger.mark_uncertain(
                request.request_id, reason="paid response had unexpected status"
            )
            return ClientResponse(
                response.status_code,
                response.headers,
                response.body,
                PaymentResult(
                    request.request_id,
                    PaymentStatus.UNCERTAIN,
                    amount=request.amount,
                    asset=request.asset,
                    network=request.network,
                    reason="unexpected paid response status",
                ),
            )
        if not receipt.success:
            self.ledger.mark_uncertain(
                request.request_id, reason="facilitator reported unsuccessful settlement"
            )
            return ClientResponse(
                response.status_code,
                response.headers,
                response.body,
                PaymentResult(
                    request.request_id,
                    PaymentStatus.UNCERTAIN,
                    transaction_id=receipt.transaction or None,
                    amount=request.amount,
                    asset=request.asset,
                    network=request.network,
                    reason=receipt.error_reason or "settlement unsuccessful",
                ),
            )
        self.ledger.commit(request.request_id, settlement_id=receipt.transaction)
        return ClientResponse(
            response.status_code,
            response.headers,
            response.body,
            PaymentResult(
                request.request_id,
                PaymentStatus.SETTLED,
                transaction_id=receipt.transaction,
                amount=request.amount,
                asset=request.asset,
                network=request.network,
            ),
        )

    def _choose(self, offers: tuple[PaymentRequirements, ...]) -> PaymentRequirements:
        if len(offers) > 32:
            raise ValidationError("payment challenge contains too many offers")
        supported = [offer for offer in offers if _supports(self.signer, offer)]
        if not supported:
            raise ValidationError("no payment offer supported by configured signer")
        for offer in supported:
            if offer.network not in {"stellar:testnet", "stellar:pubnet"}:
                continue
            if offer.scheme == "substrata-demo" and offer.network == "stellar:testnet":
                return offer
            if offer.scheme == "exact":
                return offer
        raise ValidationError("no payment offer supported by configured signer")

    def close(self) -> None:
        if not self._closed:
            if self._owns_ledger:
                self.ledger.close()
            self._closed = True

    def __enter__(self) -> BuyerClient:
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("BuyerClient is closed")


def _supports(signer: object, offer: PaymentRequirements) -> bool:
    supports = getattr(signer, "supports", None)
    if callable(supports):
        return bool(supports(offer))
    return (
        signer.__class__.__name__ == "LocalDemoSigner"
        and offer.scheme == "substrata-demo"
        and offer.network == "stellar:testnet"
    )


def _payment_request(
    offer: PaymentRequirements, resource: str, origin: str, request_id: str | None
) -> PaymentRequest:
    if offer.network == "stellar:testnet":
        network = Network.STELLAR_TESTNET
    elif offer.network == "stellar:pubnet":
        network = Network.STELLAR_MAINNET
    else:
        raise ValidationError("unsupported payment network")
    if offer.asset == "XLM":
        asset = Asset("XLM")
    else:
        asset = Asset.soroban(offer.asset)
    return PaymentRequest(
        recipient=offer.pay_to,
        amount=amount_from_atomic_units(int(offer.amount)),
        asset=asset,
        network=network,
        resource=resource,
        request_id=request_id or str(uuid4()),
        origin=origin,
        metadata={"scheme": offer.scheme, "offer": offer.to_dict()},
    )


def _fingerprint(
    method: str, url: str, body: bytes | None, offer: PaymentRequirements, resource: ResourceInfo
) -> str:
    parts = urlsplit(url)
    canonical = {
        "method": method,
        "origin": origin_from_url(url),
        "path": parts.path or "/",
        "query": parts.query,
        "body_sha256": hashlib.sha256(body or b"").hexdigest(),
        "resource": resource.to_dict(),
        "offer": offer.to_dict(),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _validate_receipt(
    receipt: SettlementResponse, offer: PaymentRequirements, payload: PaymentPayload
) -> None:
    if receipt.network != offer.network:
        raise ValidationError("settlement network does not match offer")
    if not receipt.success:
        return
    if not receipt.transaction:
        raise ValidationError("settlement receipt has no transaction ID")
    if offer.scheme == "substrata-demo":
        if not receipt.transaction.startswith("demo:") or receipt.payer != "demo-payer":
            raise ValidationError("demo receipt identity mismatch")
    else:
        if len(receipt.transaction) != 64 or any(
            c not in "0123456789abcdefABCDEF" for c in receipt.transaction
        ):
            raise ValidationError("Stellar receipt transaction must be a 64-character hash")
        expected_payer = getattr(payload, "payer", None)
        if (
            receipt.payer is not None
            and expected_payer is not None
            and receipt.payer != expected_payer
        ):
            raise ValidationError("settlement payer mismatch")
    if receipt.amount is not None and receipt.amount != offer.amount:
        raise ValidationError("settlement amount does not match offer")


def _parse_url(url: str):
    if not isinstance(url, str) or len(url) > 8192:
        raise ValidationError("URL must be bounded absolute HTTP(S) URL")
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValidationError("URL must be absolute HTTP(S) without credentials or fragment")
    try:
        _ = parts.port
    except ValueError:
        raise ValidationError("URL has invalid port") from None
    return parts


def _headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    if not isinstance(headers, Mapping):
        raise ValidationError("headers must be a mapping")
    result: dict[str, str] = {}
    for key, value in headers.items():
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or any(ord(c) < 33 or c in ":\r\n" for c in key)
            or "\r" in value
            or "\n" in value
        ):
            raise ValidationError("invalid HTTP header")
        if key.lower() in {"host", "content-length", "transfer-encoding", "connection"}:
            raise ValidationError(f"caller cannot set {key} header")
        result[key] = value
    return result


def _has_header(headers: Mapping[str, str], name: str) -> bool:
    return any(key.lower() == name.lower() for key in headers)


def _header(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    raise ValidationError(f"missing {name} header")


def _maybe_header(headers: Mapping[str, str], name: str) -> str | None:
    try:
        return _header(headers, name)
    except ValidationError:
        return None


def _policy_error(decision: PolicyDecision) -> bytes:
    return json.dumps(
        {"error": "payment denied by local policy", "reason": decision.reason_code.value}
    ).encode()


__all__ = [
    "BuyerClient",
    "ClientResponse",
    "HTTPResponse",
    "HTTPTransport",
    "PaymentSigner",
    "Transport",
    "TransportError",
]
