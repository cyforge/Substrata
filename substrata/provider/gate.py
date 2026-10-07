"""Payment verification, durable replay protection, and settlement for paid routes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal, Protocol, TypeAlias

from substrata.audit import AuditLog
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
GateCode: TypeAlias = Literal[
    "invalid_payment",
    "unsupported_payment",
    "offer_mismatch",
    "resource_mismatch",
    "verification_unavailable",
    "payment_invalid",
    "proof_identity_invalid",
    "payment_replayed",
    "replay_store_unavailable",
    "settlement_uncertain",
    "settlement_failed",
    "audit_unavailable",
    "paid",
]
AuditWriter = Callable[[str, dict[str, str]], None]
ProviderAudit = AuditLog | AuditWriter


@dataclass(frozen=True, slots=True)
class PaidRoute:
    """One exact paid path, method, offer, and trusted external resource URL."""

    path: str
    method: str
    requirements: PaymentRequirements
    resource_url: str
    additional_requirements: tuple[PaymentRequirements, ...] = ()
    resource: ResourceInfo | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.startswith("/"):
            raise ValidationError("route path must start with /")
        if self.path != "/" and self.path.endswith("/"):
            raise ValidationError("route path must be canonical without trailing slash")
        if not isinstance(self.method, str) or not re.fullmatch(
            r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", self.method
        ):
            raise ValidationError("route method must be an HTTP token")
        object.__setattr__(self, "method", self.method.upper())
        if not isinstance(self.requirements, PaymentRequirements):
            raise ValidationError("route requirements must be PaymentRequirements")
        if not isinstance(self.additional_requirements, (tuple, list)) or any(
            not isinstance(item, PaymentRequirements) for item in self.additional_requirements
        ):
            raise ValidationError("additional_requirements must contain payment requirements")
        offers = (self.requirements, *self.additional_requirements)
        if len(set(offers)) != len(offers):
            raise ValidationError("route payment requirements must be unique")
        object.__setattr__(self, "additional_requirements", tuple(self.additional_requirements))
        if not isinstance(self.resource_url, str):
            raise ValidationError("route resource_url must be a string")
        if self.resource is None:
            object.__setattr__(self, "resource", ResourceInfo(url=self.resource_url))
        elif self.resource.url != self.resource_url:
            raise ValidationError("route resource URL must match resource metadata")

    @property
    def challenge(self) -> PaymentRequired:
        assert self.resource is not None
        return PaymentRequired(
            resource=self.resource,
            accepts=(self.requirements, *self.additional_requirements),
        )


@dataclass(frozen=True, slots=True)
class GateResult:
    """Outcome safe to expose to an application; never includes proof contents."""

    paid: bool
    code: GateCode
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
        audit_log: ProviderAudit | None = None,
    ) -> None:
        self.facilitator = facilitator
        self.replay_store = replay_store
        self.proof_identity = proof_identity
        self.audit_log = audit_log

    def process(self, route: PaidRoute, payload: PaymentPayload) -> GateResult:
        if not isinstance(route, PaidRoute) or not isinstance(payload, PaymentPayload):
            return GateResult(False, "invalid_payment")
        requirement = next(
            (
                offer
                for offer in (route.requirements, *route.additional_requirements)
                if payload.accepted == offer
            ),
            None,
        )
        if requirement is None:
            return GateResult(False, "offer_mismatch")
        if not self._supported(requirement):
            return GateResult(False, "unsupported_payment")
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
        if not self._audit("verified", {}):
            return GateResult(False, "audit_unavailable")

        try:
            proof_id = self._proof_id(payload, requirement, verified)
        except Exception:
            self._audit("proof_identity_rejected", {})
            return GateResult(False, "proof_identity_invalid")
        try:
            if not self.replay_store.claim(requirement.network, proof_id):
                self._audit("replay_rejected", {})
                return GateResult(False, "payment_replayed")
        except Exception:
            self._audit("claim_failed", {})
            return GateResult(False, "replay_store_unavailable")
        if not self._audit("proof_claimed", {}):
            return GateResult(False, "audit_unavailable", proof_id=proof_id)

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
            return bool(re.fullmatch(r"demo:[0-9a-f]{64}", receipt.transaction))
        if requirement.scheme == "exact" and requirement.network.startswith("stellar:"):
            return bool(re.fullmatch(r"[0-9a-fA-F]{64}", receipt.transaction))
        return False

    @staticmethod
    def _supported(requirement: PaymentRequirements) -> bool:
        if requirement.scheme == "substrata-demo":
            return requirement.network == "stellar:testnet"
        return requirement.scheme == "exact" and requirement.network in {
            "stellar:testnet",
            "stellar:pubnet",
        }

    def _audit(self, event: str, data: dict[str, str]) -> bool:
        sink = self.audit_log
        if sink is None:
            return True
        try:
            if isinstance(sink, AuditLog):
                sink.append(f"provider.{event}", data=data)
            else:
                sink(event, data)
            return True
        except Exception:
            return False


def challenge_header(route: PaidRoute) -> str:
    return encode_header(route.challenge)


class StellarProofIdentityExtractor:
    """Parse exact Stellar XDR and identify its signed auth nonce.

    Imports `stellar-sdk` only when called. Facilitator verification remains
    responsible for signature validity, expiration, and current-ledger checks.
    """

    def __call__(
        self,
        payload: PaymentPayload,
        requirements: PaymentRequirements,
        verified: VerifyResponse,
    ) -> str:
        if requirements.scheme != "exact" or not requirements.network.startswith("stellar:"):
            raise ValidationError("Stellar proof identity requires exact Stellar offer")
        try:
            from stellar_sdk import Address, scval, xdr
        except ImportError as exc:
            raise ValidationError(
                "stellar-sdk is required to parse Stellar proof identity"
            ) from exc

        try:
            encoded = payload.payload["transaction"]
            assert isinstance(encoded, str)
            envelope = xdr.TransactionEnvelope.from_xdr(encoded)
            if envelope.type == xdr.EnvelopeType.ENVELOPE_TYPE_TX:
                assert envelope.v1 is not None
                transaction = envelope.v1.tx
            elif envelope.type == xdr.EnvelopeType.ENVELOPE_TYPE_TX_V0:
                assert envelope.v0 is not None
                transaction = envelope.v0.tx
            else:
                raise ValidationError("unsupported Stellar transaction envelope")
            if len(transaction.operations) != 1:
                raise ValidationError("Stellar payment must contain one operation")
            operation = transaction.operations[0]
            if operation.body.type != xdr.OperationType.INVOKE_HOST_FUNCTION:
                raise ValidationError("Stellar payment operation must invoke a contract")
            invoke = operation.body.invoke_host_function_op
            assert invoke is not None
            host = invoke.host_function
            if host.type != xdr.HostFunctionType.HOST_FUNCTION_TYPE_INVOKE_CONTRACT:
                raise ValidationError("Stellar payment must invoke a contract function")
            call = host.invoke_contract
            assert call is not None
            contract = Address.from_xdr_sc_address(call.contract_address).address
            function = call.function_name.sc_symbol.decode("ascii")
            if contract != requirements.asset or function != "transfer" or len(call.args) != 3:
                raise ValidationError("Stellar contract call does not match transfer offer")
            payer = scval.from_address(call.args[0]).address
            payee = scval.from_address(call.args[1]).address
            amount = scval.from_int128(call.args[2])
            if payee != requirements.pay_to or amount != requirements.amount_atomic:
                raise ValidationError("Stellar transfer does not match offer")
            if verified.payer is not None and verified.payer != payer:
                raise ValidationError("verified payer does not match Stellar transfer")
            if len(invoke.auth) != 1:
                raise ValidationError("Stellar transfer requires one address authorization")

            entry = invoke.auth[0]
            credentials = entry.credentials
            if credentials.type == xdr.SorobanCredentialsType.SOROBAN_CREDENTIALS_ADDRESS:
                address_credentials = credentials.address
            elif credentials.type == xdr.SorobanCredentialsType.SOROBAN_CREDENTIALS_ADDRESS_V2:
                address_credentials = credentials.address_v2
            else:
                raise ValidationError("Stellar auth credentials must use an address")
            assert address_credentials is not None
            auth_address = Address.from_xdr_sc_address(address_credentials.address).address
            if auth_address != payer:
                raise ValidationError("Stellar auth address does not match transfer payer")
            root = entry.root_invocation
            if root.sub_invocations:
                raise ValidationError("Stellar auth cannot include sub-invocations")
            if (
                root.function.type
                != xdr.SorobanAuthorizedFunctionType.SOROBAN_AUTHORIZED_FUNCTION_TYPE_CONTRACT_FN
            ):
                raise ValidationError("Stellar auth must authorize contract transfer")
            root_call = root.function.contract_fn
            assert root_call is not None
            root_contract = Address.from_xdr_sc_address(root_call.contract_address).address
            root_function = root_call.function_name.sc_symbol.decode("ascii")
            if root_contract != contract or root_function != "transfer":
                raise ValidationError("Stellar auth root does not match transfer")
            if len(root_call.args) != 3 or (
                scval.from_address(root_call.args[0]).address != payer
                or scval.from_address(root_call.args[1]).address != payee
                or scval.from_int128(root_call.args[2]) != amount
            ):
                raise ValidationError("Stellar auth root arguments do not match transfer")
            nonce = int(address_credentials.nonce.int64)
            return f"stellar-exact:{payer}:{nonce}"
        except ValidationError:
            raise
        except Exception:
            # Parser exceptions may include input fragments; expose no XDR data.
            raise ValidationError("invalid Stellar payment transaction") from None
