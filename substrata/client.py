"""Policy-controlled x402 HTTP buyer client."""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import json
import socket
import ssl
from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from substrata.audit import AuditLog
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
            return HTTPResponse(response.status, _response_headers(response.getheaders()), data)
        except (OSError, http.client.HTTPException, socket.timeout, ssl.SSLError):
            raise TransportError("HTTP request failed") from None
        finally:
            connection.close()


class ClientError(RuntimeError):
    """Sanitized buyer-client operation error."""


class TransportError(ClientError):
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
        audit_log: AuditLog | None = None,
        max_request_body_bytes: int = 1_048_576,
    ) -> None:
        if not isinstance(policy, SpendPolicy):
            raise ValidationError("policy must be SpendPolicy")
        if not callable(getattr(signer, "sign", None)):
            raise ValidationError("signer must provide sign(requirements, resource)")
        if type(timeout) not in (int, float) or not 0 < timeout <= 300:
            raise ValidationError("timeout must be between 0 and 300 seconds")
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 16_777_216:
            raise ValidationError("max_response_bytes must be between 1 and 16777216")
        if type(max_request_body_bytes) is not int or not 0 <= max_request_body_bytes <= 16_777_216:
            raise ValidationError("max_request_body_bytes must be between 0 and 16777216")
        if audit_log is not None and not isinstance(audit_log, AuditLog):
            raise ValidationError("audit_log must be AuditLog")
        if ledger is not None and not isinstance(ledger, Ledger):
            raise ValidationError("ledger must be a Ledger")
        if ledger is not None and ledger_path is not None:
            raise ValidationError("pass ledger or ledger_path, not both")
        if ledger_path is not None and (
            not isinstance(ledger_path, str) or not ledger_path.strip()
        ):
            raise ValidationError("ledger_path must be a non-empty string")
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
        if transport is not None and not callable(getattr(transport, "send", None)):
            if self._owns_ledger:
                self.ledger.close()
            raise ValidationError("transport must provide send()")
        self.transport = transport or HTTPTransport()
        self.timeout = float(timeout)
        self.max_response_bytes = max_response_bytes
        self.max_request_body_bytes = max_request_body_bytes
        self.audit_log = audit_log
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
        if body is not None and len(body) > self.max_request_body_bytes:
            raise ValidationError("request body exceeds configured size limit")
        if method in {"POST", "PUT", "PATCH", "DELETE"} and not replayable:
            can_pay = False
        else:
            can_pay = True
        _parse_url(url)
        request_headers = _headers(headers)
        if _has_header(request_headers, PAYMENT_SIGNATURE_HEADER):
            raise ValidationError("PAYMENT-SIGNATURE cannot be supplied by the caller")
        try:
            raw_response = self.transport.send(
                method, url, request_headers, body, self.timeout, self.max_response_bytes
            )
        except Exception:
            raise TransportError("initial HTTP request failed") from None
        original = _validate_response(raw_response, self.max_response_bytes)
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
        requirement: PaymentRequirements | None = None
        request: PaymentRequest | None = None
        denied_request: PaymentRequest | None = None
        denied_decision: PolicyDecision | None = None
        for candidate in challenge.accepts:
            if not _offer_supported(self.signer, candidate):
                continue
            if candidate.network not in {"stellar:testnet", "stellar:pubnet"}:
                continue
            if candidate.scheme not in {"exact", "substrata-demo"}:
                continue
            if candidate.scheme == "substrata-demo" and candidate.network != "stellar:testnet":
                continue
            parts = _parse_url(url)
            if parts.scheme != "https" and not (
                candidate.scheme == "substrata-demo" and _is_loopback_host(parts.hostname or "")
            ):
                raise ValidationError("paid requests require HTTPS except local demo loopback")
            candidate_request = _payment_request(
                candidate, challenge.resource.url, origin, request_id
            )
            spent_total = self.ledger.spent(candidate_request.currency_key)
            spent_today = self.ledger.spent_today(candidate_request.currency_key)
            candidate_decision = self._policy_engine.evaluate(
                candidate_request,
                spent_total=spent_total,
                spent_today=spent_today,
            )
            if candidate_decision.allowed:
                requirement, request = candidate, candidate_request
                break
            denied_request, denied_decision = candidate_request, candidate_decision
        if requirement is None or request is None:
            if denied_request is not None and denied_decision is not None:
                self._audit("payment_denied", denied_request, reason=denied_decision.reason_code)
                return ClientResponse(402, {}, _policy_error(denied_decision))
            raise ValidationError("no payment offer supported by configured signer and policy")
        fingerprint = _fingerprint(method, url, body, requirement, challenge.resource)
        reservation = self.ledger.reserve_once(
            request.request_id,
            request.amount,
            currency=request.currency_key,
            limit=self.policy.total_limit,
            daily_limit=self.policy.daily_limit,
            request_fingerprint=fingerprint,
            metadata={
                "origin": origin,
                "resource_sha256": hashlib.sha256(url.encode()).hexdigest(),
                "method": method,
            },
        )
        if not reservation.created:
            raise ValidationError(
                "request ID already has a ledger reservation; payment will not be resent"
            )
        try:
            self._audit("payment_reserved", request)
            payload = self.signer.sign(requirement, challenge.resource)
            if not isinstance(payload, PaymentPayload):
                raise ValidationError("signer must return PaymentPayload")
            if payload.accepted != requirement or payload.resource != challenge.resource:
                raise ValidationError("signer returned payload for a different offer or resource")
            signature_header = encode_payment_signature_header(payload)
            self._audit("authorization_ready", request)
        except Exception:
            self.ledger.release(request.request_id, reason="payment preparation failed before send")
            try:
                self._audit("payment_unsent_failed", request, reason="preparation_failed")
            except ClientError:
                pass
            raise ClientError("payment preparation failed") from None
        paid_headers = dict(headers)
        paid_headers[PAYMENT_SIGNATURE_HEADER] = signature_header
        try:
            response = _validate_response(
                self.transport.send(
                    method, url, paid_headers, body, self.timeout, self.max_response_bytes
                ),
                self.max_response_bytes,
            )
        except Exception:
            self.ledger.mark_uncertain(
                request.request_id, reason="paid retry transport outcome unknown"
            )
            self._audit("payment_uncertain", request, reason="transport_outcome_unknown")
            raise TransportError("paid retry outcome is unknown") from None
        receipt_value = _maybe_header(response.headers, PAYMENT_RESPONSE_HEADER)
        if receipt_value is None:
            self.ledger.mark_uncertain(
                request.request_id, reason="paid response omitted settlement receipt"
            )
            self._audit("payment_uncertain", request, reason="missing_receipt")
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
            _validate_receipt(
                receipt, requirement, payload, expected_payer=getattr(self.signer, "address", None)
            )
        except ValidationError:
            self.ledger.mark_uncertain(request.request_id, reason="paid response receipt invalid")
            self._audit("payment_uncertain", request, reason="invalid_receipt")
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
        if not receipt.success:
            self.ledger.mark_uncertain(
                request.request_id, reason="facilitator reported unsuccessful settlement"
            )
            self._audit("payment_uncertain", request, reason="settlement_unsuccessful")
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
        try:
            self._audit(
                "payment_settled",
                request,
                reason="valid_settlement_receipt",
                transaction=receipt.transaction,
            )
        except Exception:
            self.ledger.mark_uncertain(
                request.request_id, reason="audit failed after paid response"
            )
            raise
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

    def _audit(
        self,
        event: str,
        request: PaymentRequest,
        *,
        reason: str | None = None,
        transaction: str | None = None,
    ) -> None:
        if self.audit_log is None:
            return
        data: dict[str, str] = {
            "network": request.network.value,
            "asset": request.asset.currency_key,
            "amount": str(request.amount),
        }
        if request.origin is not None:
            data["origin"] = request.origin
        if reason is not None:
            data["reason"] = reason
        if transaction is not None:
            data["transaction_id"] = transaction
        try:
            self.audit_log.append(event, request_id=request.request_id, data=data)
        except Exception:
            raise ClientError("audit write failed") from None

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


class AsyncBuyerClient:
    """Async facade that runs synchronous buyer I/O in worker threads."""

    def __init__(self, client: BuyerClient) -> None:
        if not isinstance(client, BuyerClient):
            raise ValidationError("AsyncBuyerClient requires a BuyerClient")
        client._ensure_open()
        self.client = client
        self._closed = False

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        request_id: str | None = None,
    ) -> ClientResponse:
        self._ensure_open()
        return await asyncio.to_thread(self.client.get, url, headers=headers, request_id=request_id)

    async def post(
        self,
        url: str,
        body: bytes,
        *,
        headers: Mapping[str, str] | None = None,
        request_id: str | None = None,
        replayable: bool = False,
    ) -> ClientResponse:
        self._ensure_open()
        return await asyncio.to_thread(
            self.client.post,
            url,
            body,
            headers=headers,
            request_id=request_id,
            replayable=replayable,
        )

    async def request(
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
        return await asyncio.to_thread(
            self.client.request,
            method,
            url,
            body=body,
            headers=headers,
            request_id=request_id,
            replayable=replayable,
        )

    async def close(self) -> None:
        if not self._closed:
            await asyncio.to_thread(self.client.close)
            self._closed = True

    async def __aenter__(self) -> AsyncBuyerClient:
        self._ensure_open()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("AsyncBuyerClient is closed")
        self.client._ensure_open()


def _offer_supported(signer: object, offer: PaymentRequirements) -> bool:
    supports = getattr(signer, "supports", None)
    if callable(supports):
        try:
            return bool(supports(offer))
        except Exception:
            raise ClientError("signer capability check failed") from None
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
    receipt: SettlementResponse,
    offer: PaymentRequirements,
    payload: PaymentPayload,
    *,
    expected_payer: str | None = None,
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
    if (
        not isinstance(url, str)
        or len(url) > 8192
        or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in url)
        or "\\" in url
    ):
        raise ValidationError("URL must be bounded absolute HTTP(S) URL")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ValidationError("URL must be absolute HTTP(S)") from None
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


def _validate_response(response: HTTPResponse, max_bytes: int) -> HTTPResponse:
    if not isinstance(response, HTTPResponse):
        raise ClientError("transport returned an invalid HTTP response")
    if type(response.status_code) is not int or not 100 <= response.status_code <= 599:
        raise ClientError("transport returned an invalid HTTP status")
    if not isinstance(response.headers, Mapping) or not isinstance(response.body, bytes):
        raise ClientError("transport returned invalid response fields")
    if len(response.body) > max_bytes:
        raise ClientError("transport response exceeds configured size limit")
    seen: set[str] = set()
    normalized: dict[str, str] = {}
    protected = {
        PAYMENT_REQUIRED_HEADER.lower(),
        PAYMENT_RESPONSE_HEADER.lower(),
        "location",
        "content-length",
    }
    for key, value in response.headers.items():
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or any(ord(char) < 33 or char in ":\r\n" for char in key)
            or "\r" in value
            or "\n" in value
        ):
            raise ClientError("transport returned an invalid HTTP header")
        lowered = key.lower()
        if lowered in protected and lowered in seen:
            raise ClientError(f"transport returned duplicate {key} header")
        seen.add(lowered)
        normalized[lowered] = value
    return HTTPResponse(response.status_code, normalized, response.body)


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _response_headers(pairs: list[tuple[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    seen: set[str] = set()
    protected = {
        PAYMENT_REQUIRED_HEADER.lower(),
        PAYMENT_RESPONSE_HEADER.lower(),
        "content-length",
        "location",
    }
    for key, value in pairs:
        normalized = key.lower()
        if normalized in protected and normalized in seen:
            raise ValidationError(f"duplicate {key} response header")
        seen.add(normalized)
        result[normalized] = value
    return result


def _has_header(headers: Mapping[str, str], name: str) -> bool:
    return any(key.lower() == name.lower() for key in headers)


def _header(headers: Mapping[str, str], name: str) -> str:
    matches = [value for key, value in headers.items() if key.lower() == name.lower()]
    if len(matches) != 1:
        raise ValidationError(f"expected exactly one {name} header")
    return matches[0]


def _maybe_header(headers: Mapping[str, str], name: str) -> str | None:
    try:
        return _header(headers, name)
    except ValidationError:
        return None


def _policy_error(decision: PolicyDecision) -> bytes:
    return json.dumps(
        {"error": "payment denied by local policy", "reason": decision.reason_code}
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
