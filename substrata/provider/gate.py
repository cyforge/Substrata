"""Payment verification, durable replay protection, and settlement for paid routes."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

from substrata.errors import ValidationError
from substrata.storage import ReplayStore
from substrata.x402.codec import encode_header
from substrata.x402.models import (
    PaymentPayload,
    PaymentRequired,
    PaymentRequirements,
    ResourceInfo,
    SettlementResponse,
    VerifyResponse,
)


class ProviderFacilitator(Protocol):
    def verify(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> VerifyResponse: ...

    def settle(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> SettlementResponse: ...


ProofIdentity = Callable[[PaymentPayload, PaymentRequirements, VerifyResponse], str]
AuditWriter = Callable[[str, dict[str, str]], None]


@dataclass(frozen=True, slots=True)
class PaidRoute:
    """One exact paid path, method, offer, and trusted external resource URL."""

    path: str
    method: str
    requirements: PaymentRequirements
    resource_url: str
    handler: Callable[..., object] | None = field(default=None, repr=False, compare=False)
    resource: ResourceInfo | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.startswith("/"):
            raise ValidationError("route path must start with /")
        if self.path != "/" and self.path.endswith("/"):
            raise ValidationError("route path must be canonical without trailing slash")
        if not isinstance(self.method, str) or not self.method.isascii():
            raise ValidationError("route method must be ASCII")
        object.__setattr__(self, "method", self.method.upper())
        if not isinstance(self.requirements, PaymentRequirements):
            raise ValidationError("route requirements must be PaymentRequirements")
        if not isinstance(self.resource_url, str):
            raise ValidationError("route resource_url must be a string")
        if self.resource is None:
            object.__setattr__(self, "resource", ResourceInfo(url=self.resource_url))
        elif self.resource.url != self.resource_url:
            raise ValidationError("route resource URL must match resource metadata")

    @property
    def challenge(self) -> PaymentRequired:
        assert self.resource is not None
        return PaymentRequired(resource=self.resource, accepts=(self.requirements,))


@dataclass(frozen=True, slots=True)
class GateResult:
    """Outcome safe to expose to an application; never includes proof contents."""

    paid: bool
    code: str
    receipt: SettlementResponse | None = None
    proof_id: str | None = None

    @property
    def response_header(self) -> str | None:
        if self.receipt is None:
            return None
        return encode_header(self.receipt)


class PaymentGate:
    """Verify proof, claim its signed authorization once, then settle once."""

    def __init__(
        self,
        facilitator: ProviderFacilitator,
        replay_store: ReplayStore,
        *,
        proof_identity: ProofIdentity | None = None,
        audit_log: AuditWriter | object | None = None,
    ) -> None:
        self.facilitator = facilitator
        self.replay_store = replay_store
        self.proof_identity = proof_identity
        self.audit_log = audit_log

    def process(self, route: PaidRoute, payload: PaymentPayload) -> GateResult:
        if not isinstance(route, PaidRoute) or not isinstance(payload, PaymentPayload):
            return GateResult(False, "invalid_payment")
        requirement = route.requirements
        if payload.accepted != requirement:
            return GateResult(False, "offer_mismatch")
        if payload.resource is None or payload.resource.url != route.resource_url:
            return GateResult(False, "resource_mismatch")

        try:
            verified = self.facilitator.verify(payload, requirement)
        except Exception:
            self._audit("verify_failed", {})
            return GateResult(False, "verification_unavailable")
        if not isinstance(verified, VerifyResponse) or verified.is_valid is not True:
            self._audit("verify_rejected", {})
            return GateResult(False, "payment_invalid")
        self._audit("verified", {})

        try:
            proof_id = self._proof_id(payload, requirement, verified)
            if not self.replay_store.claim(requirement.network, proof_id):
                self._audit("replay_rejected", {})
                return GateResult(False, "payment_replayed")
        except Exception:
            self._audit("claim_failed", {})
            return GateResult(False, "replay_store_unavailable")
        self._audit("proof_claimed", {})

        try:
            receipt = self.facilitator.settle(payload, requirement)
        except Exception:
            # Claim remains consumed: a timeout can follow successful settlement.
            self._audit("settlement_uncertain", {})
            return GateResult(False, "settlement_uncertain", proof_id=proof_id)
        if not self._valid_receipt(receipt, requirement, verified):
            self._audit("settlement_rejected", {})
            return GateResult(False, "settlement_failed", proof_id=proof_id)
        self._audit("settled", {})
        return GateResult(True, "paid", receipt=receipt, proof_id=proof_id)

    def _proof_id(
        self,
        payload: PaymentPayload,
        requirement: PaymentRequirements,
        verified: VerifyResponse,
    ) -> str:
        if self.proof_identity is not None:
            value = self.proof_identity(payload, requirement, verified)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError("proof identity adapter returned invalid identity")
            return value.strip()
        if requirement.scheme == "substrata-demo":
            nonce = payload.payload.get("nonce")
            if not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{32}", nonce):
                raise ValidationError("demo authorization has no valid nonce")
            payer = verified.payer or "demo-payer"
            return f"substrata-demo:{payer}:{nonce}"
        # Hashing XDR/envelope is unsafe: one signed auth can occur in many envelopes.
        raise ValidationError("canonical proof identity adapter required")

    @staticmethod
    def _valid_receipt(
        receipt: object,
        requirement: PaymentRequirements,
        verified: VerifyResponse,
    ) -> bool:
        if not isinstance(receipt, SettlementResponse) or receipt.success is not True:
            return False
        if receipt.network != requirement.network or not receipt.transaction:
            return False
        if verified.payer is not None and receipt.payer != verified.payer:
            return False
        if receipt.amount is not None and receipt.amount != requirement.amount:
            return False
        if requirement.scheme == "substrata-demo":
            return receipt.transaction.startswith("demo:")
        if requirement.scheme == "exact" and requirement.network.startswith("stellar:"):
            return bool(re.fullmatch(r"[0-9a-fA-F]{64}", receipt.transaction))
        return False

    def _audit(self, event: str, data: dict[str, str]) -> None:
        try:
            sink = self.audit_log
            if sink is None:
                return
            append = getattr(sink, "append", None)
            if callable(append):
                append(f"provider.{event}", data=data)
            elif callable(sink):
                sink(event, data)
        except Exception:
            # Audit failure must not alter payment outcome or leak proof material.
            return


def challenge_header(route: PaidRoute) -> str:
    return encode_header(route.challenge)


def _proof_digest(payload: PaymentPayload) -> str:
    """Non-identity digest for optional audit/correlation; not used for replay."""
    return hashlib.sha256(repr(payload.to_dict()).encode("utf-8")).hexdigest()
