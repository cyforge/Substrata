"""Stable JSON-safe views of core domain models."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from math import isfinite
from typing import Mapping, TypeAlias, cast

from ..constants import Network
from ..errors import ValidationError
from ..validation import validate_amount, validate_enum, validate_required_string
from .domain import (
    Asset,
    BudgetMetadata,
    PaymentRequest,
    PaymentResult,
    PaymentStatus,
    PolicyDecision,
    SignRequest,
    SignResponse,
    TransactionMetadata,
)

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
DomainModel: TypeAlias = (
    Asset
    | PaymentRequest
    | PaymentResult
    | PolicyDecision
    | SignRequest
    | SignResponse
    | TransactionMetadata
)


def serialize_model(model: DomainModel) -> dict[str, JsonValue]:
    """Convert a core model into an independent, JSON-safe dictionary."""
    if isinstance(model, Asset):
        return {"code": model.code, "issuer": model.issuer, "contract_id": model.contract_id}
    if isinstance(model, PaymentRequest):
        return {
            "recipient": model.recipient,
            "amount": str(model.amount),
            "asset": serialize_model(model.asset),
            "network": model.network.value,
            "request_id": model.request_id,
            "resource": model.resource,
            "metadata": _json_value(dict(model.metadata)),
            "origin": model.origin,
        }
    if isinstance(model, PaymentResult):
        return {
            "request_id": model.request_id,
            "status": model.status.value,
            "transaction_id": model.transaction_id,
            "amount": str(model.amount) if model.amount is not None else None,
            "asset": serialize_model(model.asset) if model.asset is not None else None,
            "network": model.network.value if model.network is not None else None,
            "reason": model.reason,
        }
    if isinstance(model, PolicyDecision):
        return {
            "allowed": model.allowed,
            "reason_code": model.reason_code,
            "reason": model.reason,
            "request_id": model.request_id,
            "budget": _serialize_budget(model.budget),
        }
    if isinstance(model, SignRequest):
        return {"request": serialize_model(model.request)}
    if isinstance(model, SignResponse):
        return {"payload": _json_value(dict(model.payload)), "signature": model.signature}
    if isinstance(model, TransactionMetadata):
        return {
            "transaction_id": model.transaction_id,
            "network": model.network.value,
            "ledger": model.ledger,
            "fee": str(model.fee) if model.fee is not None else None,
            "created_at": model.created_at.isoformat() if model.created_at is not None else None,
            "raw": _json_value(dict(model.raw)),
            "status": model.status.value if model.status is not None else None,
        }
    raise ValidationError(f"Unsupported model type: {type(model).__name__}")


def deserialize_model(model_type: type[DomainModel], value: Mapping[str, object]) -> DomainModel:
    """Build a typed core model from its strict serialized dictionary form."""
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValidationError("Serialized model must be a mapping with string keys")
    if model_type is Asset:
        return Asset.from_dict(value)
    if model_type is PaymentRequest:
        _check_fields(
            value,
            {
                "recipient",
                "amount",
                "asset",
                "network",
                "request_id",
                "resource",
                "metadata",
                "origin",
            },
            {"recipient", "amount", "asset", "request_id"},
        )
        if not isinstance(value["asset"], Mapping):
            raise ValidationError("Payment request asset must be a mapping")
        return PaymentRequest(
            recipient=_required_string(value, "recipient"),
            amount=_decimal(value["amount"], "amount"),
            asset=Asset.from_dict(value["asset"]),
            network=_network(value.get("network", Network.STELLAR_TESTNET.value)),
            request_id=_required_string(value, "request_id"),
            resource=_optional_string(value, "resource"),
            metadata=_mapping(value.get("metadata", {}), "metadata"),
            origin=_optional_string(value, "origin"),
        )
    if model_type is PaymentResult:
        _check_fields(
            value,
            {"request_id", "status", "transaction_id", "amount", "asset", "network", "reason"},
            {"request_id", "status"},
        )
        asset_value = value.get("asset")
        if asset_value is not None and not isinstance(asset_value, Mapping):
            raise ValidationError("Payment result asset must be a mapping or null")
        return PaymentResult(
            request_id=_required_string(value, "request_id"),
            status=validate_enum(value["status"], PaymentStatus, field_name="status"),
            transaction_id=_optional_string(value, "transaction_id"),
            amount=_optional_decimal(value, "amount"),
            asset=Asset.from_dict(asset_value) if asset_value is not None else None,
            network=_optional_network(value, "network"),
            reason=_optional_string(value, "reason"),
        )
    if model_type is PolicyDecision:
        _check_fields(
            value,
            {"allowed", "reason_code", "reason", "request_id", "budget"},
            {"allowed", "reason_code", "reason"},
        )
        allowed = value["allowed"]
        if not isinstance(allowed, bool):
            raise ValidationError("Policy decision allowed must be a bool")
        budget = value.get("budget")
        if budget is not None and not isinstance(budget, Mapping):
            raise ValidationError("Policy decision budget must be a mapping or null")
        return PolicyDecision(
            allowed=allowed,
            reason_code=_required_string(value, "reason_code"),
            reason=_required_string(value, "reason"),
            request_id=_optional_string(value, "request_id"),
            budget=_deserialize_budget(budget) if budget is not None else None,
        )
    if model_type is SignRequest:
        _check_fields(value, {"request"}, {"request"})
        request = value["request"]
        if not isinstance(request, Mapping):
            raise ValidationError("Signing request data must be a mapping")
        return SignRequest(cast(PaymentRequest, deserialize_model(PaymentRequest, request)))
    if model_type is SignResponse:
        _check_fields(value, {"payload", "signature"}, {"payload"})
        return SignResponse(
            payload=_mapping(value["payload"], "payload"),
            signature=_optional_string(value, "signature"),
        )
    if model_type is TransactionMetadata:
        _check_fields(
            value,
            {"transaction_id", "network", "ledger", "fee", "created_at", "raw", "status"},
            {"transaction_id", "network"},
        )
        ledger = value.get("ledger")
        if ledger is not None and (isinstance(ledger, bool) or not isinstance(ledger, int)):
            raise ValidationError("ledger must be an integer or null")
        created_at = _datetime(value.get("created_at"))
        return TransactionMetadata(
            transaction_id=_required_string(value, "transaction_id"),
            network=_network(value["network"]),
            ledger=ledger,
            fee=_optional_decimal(value, "fee"),
            created_at=created_at,
            raw=_mapping(value.get("raw", {}), "raw"),
            status=(
                validate_enum(value["status"], PaymentStatus, field_name="status")
                if value.get("status") is not None
                else None
            ),
        )
    raise ValidationError(f"Unsupported model type: {model_type.__name__}")


def payment_request_fingerprint(request: PaymentRequest) -> str:
    """Hash canonical JSON representation of a complete payment request."""
    if not isinstance(request, PaymentRequest):
        raise ValidationError("Fingerprinting requires a PaymentRequest")
    try:
        canonical = json.dumps(
            serialize_model(request),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValidationError("Payment request cannot be fingerprinted") from exc
    return sha256(canonical).hexdigest()


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValidationError("Cannot serialize non-finite float")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValidationError("Cannot serialize non-finite decimal")
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValidationError("Serialized mapping keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    raise ValidationError(f"Cannot serialize value of type {type(value).__name__}")


__all__ = [
    "DomainModel",
    "JsonValue",
    "deserialize_model",
    "payment_request_fingerprint",
    "serialize_model",
]


def _serialize_budget(budget: BudgetMetadata | None) -> dict[str, JsonValue] | None:
    if budget is None:
        return None
    return {
        "spent_today": str(budget.spent_today),
        "spent_total": str(budget.spent_total),
        "daily_limit": str(budget.daily_limit) if budget.daily_limit is not None else None,
        "total_limit": str(budget.total_limit) if budget.total_limit is not None else None,
        "remaining_today": (
            str(budget.remaining_today) if budget.remaining_today is not None else None
        ),
        "remaining_total": (
            str(budget.remaining_total) if budget.remaining_total is not None else None
        ),
    }


def _deserialize_budget(value: Mapping[str, object]) -> BudgetMetadata:
    fields = {
        "spent_today",
        "spent_total",
        "daily_limit",
        "total_limit",
        "remaining_today",
        "remaining_total",
    }
    _check_fields(value, fields, {"spent_today", "spent_total"})
    return BudgetMetadata(
        spent_today=_decimal(value["spent_today"], "spent_today"),
        spent_total=_decimal(value["spent_total"], "spent_total"),
        daily_limit=_optional_decimal(value, "daily_limit"),
        total_limit=_optional_decimal(value, "total_limit"),
        remaining_today=_optional_decimal(value, "remaining_today"),
        remaining_total=_optional_decimal(value, "remaining_total"),
    )


def _check_fields(value: Mapping[str, object], allowed: set[str], required: set[str]) -> None:
    extra = set(value) - allowed
    missing = required - set(value)
    if extra:
        raise ValidationError(f"Unknown model fields: {', '.join(sorted(extra))}")
    if missing:
        raise ValidationError(f"Missing model fields: {', '.join(sorted(missing))}")


def _required_string(value: Mapping[str, object], key: str) -> str:
    raw = value[key]
    if not isinstance(raw, str):
        raise ValidationError(f"{key} must be a string")
    return validate_required_string(raw, field_name=key)


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    raw = value.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValidationError(f"{key} must be a string or null")
    return validate_required_string(raw, field_name=key)


def _decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be encoded as a decimal string")
    return validate_amount(value, positive=False)


def _optional_decimal(value: Mapping[str, object], key: str) -> Decimal | None:
    raw = value.get(key)
    return _decimal(raw, key) if raw is not None else None


def _network(value: object) -> Network:
    return validate_enum(value, Network, field_name="network")


def _optional_network(value: Mapping[str, object], key: str) -> Network | None:
    raw = value.get(key)
    return _network(raw) if raw is not None else None


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValidationError(f"{field_name} must be a mapping with string keys")
    return value


def _datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("created_at must be an ISO 8601 timestamp or null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("created_at must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValidationError("created_at must include a timezone")
    return parsed
