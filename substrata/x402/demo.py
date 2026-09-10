"""Explicit local-only x402 payment demo. Never submits live transactions."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from substrata.errors import ValidationError
from substrata.x402.codec import MAX_JSON_BYTES
from substrata.x402.facilitator import SupportedKind, SupportedResponse
from substrata.x402.models import (
    PaymentPayload,
    PaymentRequirements,
    ResourceInfo,
    SettlementResponse,
    VerifyResponse,
)

DEMO_SCHEME = "substrata-demo"
DEMO_NETWORK = "stellar:testnet"
DEMO_TTL_SECONDS = 60


@dataclass(frozen=True, slots=True, repr=False)
class LocalDemoKey:
    """Secret shared by demo signer and local demo facilitator."""

    _secret: bytes = field(repr=False)

    def __init__(self, secret: bytes | None = None) -> None:
        if secret is None:
            secret = secrets.token_bytes(32)
        if not isinstance(secret, bytes) or not 32 <= len(secret) <= 4096:
            raise ValidationError("local demo key must contain 32 to 4096 bytes")
        object.__setattr__(self, "_secret", bytes(secret))

    @classmethod
    def generate(cls) -> LocalDemoKey:
        return cls()

    def __repr__(self) -> str:
        return "LocalDemoKey(<redacted>)"


class LocalDemoSigner:
    """Sign local demo payloads with an HMAC key; no wallet or chain access."""

    def __init__(self, key: LocalDemoKey, *, ttl_seconds: int = DEMO_TTL_SECONDS) -> None:
        if not isinstance(key, LocalDemoKey):
            raise ValidationError("LocalDemoSigner requires LocalDemoKey")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
            raise ValidationError("demo TTL must be between 1 and 300 seconds")
        self._key = key
        self._ttl_seconds = ttl_seconds

    def supports(self, requirements: PaymentRequirements) -> bool:
        """Report offer support without signing or checking the resource URL."""
        return (
            isinstance(requirements, PaymentRequirements)
            and requirements.scheme == DEMO_SCHEME
            and requirements.network == DEMO_NETWORK
        )

    def sign(self, requirements: PaymentRequirements, resource: ResourceInfo) -> PaymentPayload:
        _check_offer(requirements)
        if not isinstance(resource, ResourceInfo) or not _is_loopback_url(resource.url):
            raise ValidationError("local demo only signs loopback resource URLs")
        now = int(time.time())
        expires_at = now + min(self._ttl_seconds, requirements.max_timeout_seconds)
        nonce = secrets.token_hex(16)
        unsigned = {
            "accepted": requirements.to_dict(),
            "resource": resource.to_dict(),
            "nonce": nonce,
            "expiresAt": expires_at,
        }
        signature = _mac(self._key._secret, unsigned)
        return PaymentPayload(
            accepted=requirements,
            resource=resource,
            payload={"nonce": nonce, "expiresAt": expires_at, "signature": signature},
        )


class LocalDemoFacilitator:
    """Verify and consume demo authorizations in memory; never touches a network."""

    def __init__(self, key: LocalDemoKey) -> None:
        if not isinstance(key, LocalDemoKey):
            raise ValidationError("LocalDemoFacilitator requires LocalDemoKey")
        self._key = key
        self._consumed: set[str] = set()
        self._lock = threading.Lock()

    def supported(self) -> SupportedResponse:
        return SupportedResponse(
            kinds=(SupportedKind(2, DEMO_SCHEME, DEMO_NETWORK),),
            extensions=(),
            signers={},
        )

    def verify(self, payload: PaymentPayload, requirements: PaymentRequirements) -> VerifyResponse:
        with self._lock:
            failure = self._verify_failure(payload, requirements)
            if failure:
                return VerifyResponse(False, invalid_reason=failure)
        return VerifyResponse(True, payer="demo-payer")

    def settle(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> SettlementResponse:
        with self._lock:
            failure = self._verify_failure(payload, requirements)
            if failure:
                return SettlementResponse(
                    success=False,
                    transaction="",
                    network=(
                        requirements.network
                        if isinstance(requirements, PaymentRequirements)
                        else DEMO_NETWORK
                    ),
                    error_reason=failure,
                )
            nonce = payload.payload["nonce"]
            signature = payload.payload["signature"]
            assert isinstance(nonce, str) and isinstance(signature, str)
            self._consumed.add(nonce)
        receipt = hashlib.sha256((nonce + signature).encode("ascii")).hexdigest()
        return SettlementResponse(
            success=True,
            transaction=f"demo:{receipt}",
            network=DEMO_NETWORK,
            payer="demo-payer",
            amount=requirements.amount,
        )

    def _verify_failure(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> str | None:
        try:
            if not isinstance(requirements, PaymentRequirements):
                return "invalid_payment_requirements"
            if requirements.network != DEMO_NETWORK:
                return "invalid_network"
            if requirements.scheme != DEMO_SCHEME:
                return "invalid_scheme"
            if not isinstance(payload, PaymentPayload) or payload.accepted != requirements:
                return "invalid_payload"
            resource = payload.resource
            if resource is None or not _is_loopback_url(resource.url):
                return "invalid_payload"
            data = payload.payload
            nonce = data.get("nonce")
            expiry = data.get("expiresAt")
            signature = data.get("signature")
            if set(data) != {"nonce", "expiresAt", "signature"}:
                return "invalid_payload"
            if (
                not isinstance(nonce, str)
                or len(nonce) != 32
                or any(char not in "0123456789abcdef" for char in nonce)
                or type(expiry) is not int
                or not isinstance(signature, str)
                or len(signature) != 64
                or any(char not in "0123456789abcdef" for char in signature)
            ):
                return "invalid_payload"
            now = int(time.time())
            if expiry < now or expiry > now + requirements.max_timeout_seconds:
                return "authorization_expired"
            if nonce in self._consumed:
                return "replay_detected"
            expected = _mac(
                self._key._secret,
                {
                    "accepted": requirements.to_dict(),
                    "resource": resource.to_dict(),
                    "nonce": nonce,
                    "expiresAt": expiry,
                },
            )
            if not hmac.compare_digest(signature, expected):
                return "invalid_signature"
            return None
        except (ValidationError, TypeError, ValueError, KeyError):
            return "invalid_payload"


@dataclass(frozen=True, slots=True)
class LocalDemoPair:
    signer: LocalDemoSigner
    facilitator: LocalDemoFacilitator


def create_local_demo_pair(*, ttl_seconds: int = DEMO_TTL_SECONDS) -> LocalDemoPair:
    """Create a local signer and facilitator with one fresh key."""
    key = LocalDemoKey.generate()
    return LocalDemoPair(
        signer=LocalDemoSigner(key, ttl_seconds=ttl_seconds),
        facilitator=LocalDemoFacilitator(key),
    )


def _check_offer(requirements: PaymentRequirements) -> None:
    if not isinstance(requirements, PaymentRequirements):
        raise ValidationError("demo requires typed payment requirements")
    if requirements.scheme != DEMO_SCHEME or requirements.network != DEMO_NETWORK:
        raise ValidationError("local demo supports only substrata-demo on stellar:testnet")


def _is_loopback_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    host = parts.hostname.lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _mac(key: bytes, message: dict[str, object]) -> str:
    try:
        canonical = json.dumps(
            message, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    except (TypeError, ValueError, RecursionError):
        raise ValidationError("local demo authorization contains invalid JSON") from None
    if len(canonical) > MAX_JSON_BYTES:
        raise ValidationError("local demo authorization exceeds size limit")
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


__all__ = [
    "DEMO_NETWORK",
    "DEMO_SCHEME",
    "DEMO_TTL_SECONDS",
    "LocalDemoFacilitator",
    "LocalDemoKey",
    "LocalDemoPair",
    "LocalDemoSigner",
    "create_local_demo_pair",
]
