"""Typed payment and signing domain models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4

from ..constants import Network
from ..errors import ValidationError
from ..validation import AmountInput, normalize_origin, validate_amount, validate_required_string


def _nonempty(value: str, field_name: str) -> str:
    return validate_required_string(value, field_name=field_name)


def _freeze_value(
    value: object,
    *,
    depth: int = 0,
    ancestors: frozenset[int] = frozenset(),
    nodes: list[int] | None = None,
) -> object:
    if nodes is None:
        nodes = [10_000]
    nodes[0] -= 1
    if nodes[0] < 0 or depth > 32:
        raise ValidationError("Metadata exceeds supported size or nesting depth")
    if isinstance(value, Mapping):
        if id(value) in ancestors:
            raise ValidationError("Metadata cannot contain circular references")
        if len(value) > 10_000:
            raise ValidationError("Metadata mapping exceeds supported size")
        if not all(isinstance(key, str) for key in value):
            raise ValidationError("Metadata mapping keys must be strings")
        nested_ancestors = ancestors | {id(value)}
        return MappingProxyType(
            {
                key: _freeze_value(
                    item,
                    depth=depth + 1,
                    ancestors=nested_ancestors,
                    nodes=nodes,
                )
                for key, item in value.items()
            }
        )
    if isinstance(value, list | tuple):
        if id(value) in ancestors:
            raise ValidationError("Metadata cannot contain circular references")
        if len(value) > 10_000:
            raise ValidationError("Metadata sequence exceeds supported size")
        nested_ancestors = ancestors | {id(value)}
        return tuple(
            _freeze_value(
                item,
                depth=depth + 1,
                ancestors=nested_ancestors,
                nodes=nodes,
            )
            for item in value
        )
    if value is None or isinstance(value, (str, bool, datetime)):
        return value
    if isinstance(value, int):
        if value.bit_length() > 4096:
            raise ValidationError("Metadata integer exceeds supported size")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValidationError("Metadata cannot contain non-finite decimals")
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValidationError("Metadata cannot contain non-finite floats")
        return value
    raise ValidationError(f"Metadata cannot contain {type(value).__name__} values")


def _freeze_mapping(value: Mapping[str, object], field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{field_name} must be a mapping")
    frozen = _freeze_value(value)
    assert isinstance(frozen, Mapping)
    return frozen


@dataclass(frozen=True, slots=True, eq=False)
class Asset:
    code: str
    issuer: str | None = None
    contract_id: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.code, "Asset code")
        if re.fullmatch(r"[A-Z0-9]{1,12}", self.code) is None:
            raise ValidationError(
                "Asset code must contain 1 to 12 uppercase ASCII letters or digits"
            )
        if self.issuer is not None:
            _nonempty(self.issuer, "Asset issuer")
        if self.contract_id is not None:
            _nonempty(self.contract_id, "Asset contract ID")
        if self.issuer is not None and self.contract_id is not None:
            raise ValidationError("Asset cannot have both issuer and contract ID")
        if self.code == "XLM" and self.issuer is not None:
            raise ValidationError("Native XLM cannot have an issuer")
        if self.issuer is None and self.contract_id is None and self.code != "XLM":
            raise ValidationError("Unissued classic asset must use the native XLM code")

    @classmethod
    def soroban(cls, contract_id: str, *, code: str | None = None) -> Asset:
        """Create asset value for a Soroban token contract, including SACs."""
        _nonempty(contract_id, "Asset contract ID")
        return cls(code=code or "TOKEN", contract_id=contract_id)

    @property
    def currency_key(self) -> str:
        if self.contract_id:
            return f"contract:{self.contract_id}"
        return f"{self.code}:{self.issuer}" if self.issuer else self.code

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Asset):
            return NotImplemented
        if self.contract_id is not None or other.contract_id is not None:
            return self.contract_id is not None and self.contract_id == other.contract_id
        return (self.code, self.issuer) == (other.code, other.issuer)

    def __hash__(self) -> int:
        if self.contract_id is not None:
            return hash(("contract", self.contract_id))
        return hash((self.code, self.issuer))

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Asset:
        if not isinstance(value, Mapping):
            raise ValidationError("Asset data must be a mapping")
        if not all(isinstance(key, str) for key in value):
            raise ValidationError("Asset field names must be strings")
        extra = set(value) - {"code", "issuer", "contract_id"}
        if extra:
            raise ValidationError(f"Unknown asset fields: {', '.join(sorted(extra))}")
        code = value.get("code")
        issuer = value.get("issuer")
        contract_id = value.get("contract_id")
        if not isinstance(code, str):
            raise ValidationError("Asset code must be a string")
        if issuer is not None and not isinstance(issuer, str):
            raise ValidationError("Asset issuer must be a string or null")
        if contract_id is not None and not isinstance(contract_id, str):
            raise ValidationError("Asset contract ID must be a string or null")
        return cls(code=code, issuer=issuer, contract_id=contract_id)


@dataclass(frozen=True, slots=True)
class PaymentRequest:
    recipient: str
    amount: AmountInput
    asset: Asset
    network: Network = Network.STELLAR_TESTNET
    request_id: str = field(default_factory=lambda: str(uuid4()))
    resource: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    origin: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.recipient, "Recipient")
        if not isinstance(self.asset, Asset):
            raise ValidationError("Asset must be an Asset instance")
        if not isinstance(self.network, Network):
            raise ValidationError("Network must be a Network instance")
        _nonempty(self.request_id, "Request ID")
        object.__setattr__(self, "amount", validate_amount(self.amount))
        if self.resource is not None:
            _nonempty(self.resource, "Resource")
        if self.origin is not None:
            object.__setattr__(self, "origin", normalize_origin(self.origin))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, "Metadata"))


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SETTLED = "settled"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class PolicyReasonCode(StrEnum):
    ALLOWED = "allowed"
    NETWORK_DENIED = "network_denied"
    RECIPIENT_DENIED = "recipient_denied"
    ASSET_DENIED = "asset_denied"
    PAYMENT_LIMIT_EXCEEDED = "payment_limit_exceeded"
    DAILY_LIMIT_EXCEEDED = "daily_limit_exceeded"
    TOTAL_LIMIT_EXCEEDED = "total_limit_exceeded"
    POLICY_EXPIRED = "policy_expired"
    ORIGIN_DENIED = "origin_denied"
    INVALID_SPEND = "invalid_spend"


@dataclass(frozen=True, slots=True)
class BudgetMetadata:
    spent_today: Decimal
    spent_total: Decimal
    daily_limit: Decimal | None = None
    total_limit: Decimal | None = None
    remaining_today: Decimal | None = None
    remaining_total: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "spent_today", validate_amount(self.spent_today, positive=False))
        object.__setattr__(self, "spent_total", validate_amount(self.spent_total, positive=False))
        for name in ("daily_limit", "total_limit", "remaining_today", "remaining_total"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, validate_amount(value, positive=False))


@dataclass(frozen=True, slots=True)
class PaymentResult:
    request_id: str
    status: PaymentStatus
    transaction_id: str | None = None
    amount: Decimal | None = None
    asset: Asset | None = None
    network: Network | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.request_id, "Request ID")
        if not isinstance(self.status, PaymentStatus):
            raise ValidationError("Status must be a PaymentStatus")
        if self.transaction_id is not None:
            _nonempty(self.transaction_id, "Transaction ID")
        if self.asset is not None and not isinstance(self.asset, Asset):
            raise ValidationError("Asset must be an Asset instance")
        if self.network is not None and not isinstance(self.network, Network):
            raise ValidationError("Network must be a Network instance")
        if self.reason is not None:
            _nonempty(self.reason, "Reason")
        if self.amount is not None:
            object.__setattr__(self, "amount", validate_amount(self.amount))


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason_code: str | PolicyReasonCode
    reason: str
    request_id: str | None = None
    budget: BudgetMetadata | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise ValidationError("Allowed must be a bool")
        _nonempty(self.reason_code, "Reason code")
        reason_code = (
            self.reason_code.value
            if isinstance(self.reason_code, PolicyReasonCode)
            else self.reason_code
        )
        if self.allowed != (reason_code == PolicyReasonCode.ALLOWED.value):
            raise ValidationError("Policy decision status and reason code disagree")
        object.__setattr__(self, "reason_code", reason_code)
        _nonempty(self.reason, "Reason")
        if self.request_id is not None:
            _nonempty(self.request_id, "Request ID")
        if self.budget is not None and not isinstance(self.budget, BudgetMetadata):
            raise ValidationError("Budget must be BudgetMetadata or None")


@dataclass(frozen=True, slots=True)
class SignRequest:
    request: PaymentRequest

    def __post_init__(self) -> None:
        if not isinstance(self.request, PaymentRequest):
            raise ValidationError("Request must be a PaymentRequest")


@dataclass(frozen=True, slots=True)
class SignResponse:
    payload: Mapping[str, object]
    signature: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze_mapping(self.payload, "Signing payload"))
        if self.signature is not None:
            _nonempty(self.signature, "Signature")


@dataclass(frozen=True, slots=True)
class TransactionMetadata:
    transaction_id: str
    network: Network
    ledger: int | None = None
    fee: Decimal | None = None
    created_at: datetime | None = None
    raw: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _nonempty(self.transaction_id, "Transaction ID")
        if not isinstance(self.network, Network):
            raise ValidationError("Network must be a Network instance")
        if self.ledger is not None and (
            isinstance(self.ledger, bool) or not isinstance(self.ledger, int) or self.ledger < 0
        ):
            raise ValidationError("Ledger must be a non-negative integer")
        if self.fee is not None:
            object.__setattr__(self, "fee", validate_amount(self.fee, positive=False))
        if self.created_at is not None and (
            not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None
        ):
            raise ValidationError("Transaction timestamp must be timezone-aware")
        object.__setattr__(self, "raw", _freeze_mapping(self.raw, "Raw transaction data"))


__all__ = [
    "Asset",
    "BudgetMetadata",
    "PaymentRequest",
    "PaymentResult",
    "PaymentStatus",
    "PolicyDecision",
    "PolicyReasonCode",
    "SignRequest",
    "SignResponse",
    "TransactionMetadata",
]
