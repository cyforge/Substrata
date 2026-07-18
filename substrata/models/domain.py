"""Typed payment and signing domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4

from ..constants import Network
from ..errors import ValidationError
from ..validation import AmountInput, validate_amount, validate_required_string


def _nonempty(value: str, field_name: str) -> str:
    return validate_required_string(value, field_name=field_name)


@dataclass(frozen=True, slots=True)
class Asset:
    code: str
    issuer: str | None = None
    contract_id: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.code, "Asset code")
        if self.issuer is not None:
            _nonempty(self.issuer, "Asset issuer")
        if self.contract_id is not None:
            _nonempty(self.contract_id, "Asset contract ID")
        if self.issuer is not None and self.contract_id is not None:
            raise ValidationError("Asset cannot have both issuer and contract ID")

    @classmethod
    def soroban(cls, contract_id: str, *, code: str | None = None) -> Asset:
        """Create asset value for a Soroban token contract, including SACs."""
        _nonempty(contract_id, "Asset contract ID")
        return cls(code=code or contract_id, contract_id=contract_id)

    @property
    def currency_key(self) -> str:
        if self.contract_id:
            return f"contract:{self.contract_id}"
        return f"{self.code}:{self.issuer}" if self.issuer else self.code


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
            _nonempty(self.origin, "Origin")
        if not isinstance(self.metadata, Mapping):
            raise ValidationError("Metadata must be a mapping")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SETTLED = "settled"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


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
    reason_code: str
    reason: str
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise ValidationError("Allowed must be a bool")
        _nonempty(self.reason_code, "Reason code")
        _nonempty(self.reason, "Reason")
        if self.request_id is not None:
            _nonempty(self.request_id, "Request ID")


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
        if not isinstance(self.payload, Mapping):
            raise ValidationError("Signing payload must be a mapping")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
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
        if not isinstance(self.raw, Mapping):
            raise ValidationError("Raw transaction data must be a mapping")
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))


__all__ = [
    "Asset",
    "PaymentRequest",
    "PaymentResult",
    "PaymentStatus",
    "PolicyDecision",
    "SignRequest",
    "SignResponse",
    "TransactionMetadata",
]
