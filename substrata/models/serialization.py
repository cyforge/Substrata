"""Stable JSON-safe views of core domain models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from math import isfinite
from typing import Mapping, TypeAlias

from ..errors import ValidationError
from .domain import (
    Asset,
    PaymentRequest,
    PaymentResult,
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
        }
    raise ValidationError(f"Unsupported model type: {type(model).__name__}")


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


__all__ = ["DomainModel", "JsonValue", "serialize_model"]
