"""Pure spend policy evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from .constants import Network
from .errors import ValidationError
from .models import Asset, PaymentRequest, PolicyDecision, serialize_model
from .validation import AmountInput, amount_to_atomic_units, validate_amount


@dataclass(frozen=True, slots=True)
class SpendPolicy:
    allowed_networks: frozenset[Network] = frozenset()
    allowed_recipients: frozenset[str] = frozenset()
    allowed_assets: frozenset[Asset] = frozenset()
    max_payment: AmountInput | None = None
    daily_limit: AmountInput | None = None
    total_limit: AmountInput | None = None
    expires_at: datetime | None = None
    allowed_origins: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for name in ("allowed_networks", "allowed_recipients", "allowed_assets", "allowed_origins"):
            object.__setattr__(self, name, frozenset(getattr(self, name)))
        for value in self.allowed_networks:
            if not isinstance(value, Network):
                raise ValidationError("Allowed networks must contain Network values")
        for value in self.allowed_assets:
            if not isinstance(value, Asset):
                raise ValidationError("Allowed assets must contain Asset values")
        for value in self.allowed_recipients | self.allowed_origins:
            if not isinstance(value, str) or not value.strip():
                raise ValidationError("Allowed recipients and origins must be non-empty strings")
        for name in ("max_payment", "daily_limit", "total_limit"):
            value = getattr(self, name)
            if value is not None:
                amount = validate_amount(value)
                object.__setattr__(self, name, amount)
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValidationError("Policy expiry must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        """Return stable JSON-ready policy data with decimal strings."""
        return {
            "allowed_networks": sorted(network.value for network in self.allowed_networks),
            "allowed_recipients": sorted(self.allowed_recipients),
            "allowed_assets": [
                serialize_model(asset)
                for asset in sorted(self.allowed_assets, key=lambda item: item.currency_key)
            ],
            "max_payment": str(self.max_payment) if self.max_payment is not None else None,
            "daily_limit": str(self.daily_limit) if self.daily_limit is not None else None,
            "total_limit": str(self.total_limit) if self.total_limit is not None else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at is not None else None,
            "allowed_origins": sorted(self.allowed_origins),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> SpendPolicy:
        """Parse strict policy data; unknown fields fail closed."""
        if not isinstance(value, Mapping):
            raise ValidationError("Policy data must be a mapping")
        if not all(isinstance(key, str) for key in value):
            raise ValidationError("Policy field names must be strings")
        allowed_fields = {
            "allowed_networks",
            "allowed_recipients",
            "allowed_assets",
            "max_payment",
            "daily_limit",
            "total_limit",
            "expires_at",
            "allowed_origins",
        }
        extra = set(value) - allowed_fields
        if extra:
            raise ValidationError(f"Unknown policy fields: {', '.join(sorted(extra))}")

        networks = _string_list(value.get("allowed_networks", []), "allowed_networks")
        recipients = _string_list(value.get("allowed_recipients", []), "allowed_recipients")
        origins = _string_list(value.get("allowed_origins", []), "allowed_origins")
        raw_assets = value.get("allowed_assets", [])
        if not isinstance(raw_assets, list):
            raise ValidationError("allowed_assets must be a list")
        assets = []
        for raw_asset in raw_assets:
            if not isinstance(raw_asset, Mapping):
                raise ValidationError("Each allowed asset must be a mapping")
            assets.append(Asset.from_dict(raw_asset))

        expiry_value = value.get("expires_at")
        expires_at = None
        if expiry_value is not None:
            if not isinstance(expiry_value, str) or "T" not in expiry_value:
                raise ValidationError("expires_at must be an RFC3339 timestamp")
            try:
                expires_at = datetime.fromisoformat(expiry_value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValidationError("expires_at must be an RFC3339 timestamp") from exc
            if expires_at.tzinfo is None:
                raise ValidationError("expires_at must include a timezone")

        return cls(
            allowed_networks=frozenset(_parse_network(item) for item in networks),
            allowed_recipients=frozenset(recipients),
            allowed_assets=frozenset(assets),
            max_payment=_policy_amount(value.get("max_payment"), "max_payment"),
            daily_limit=_policy_amount(value.get("daily_limit"), "daily_limit"),
            total_limit=_policy_amount(value.get("total_limit"), "total_limit"),
            expires_at=expires_at,
            allowed_origins=frozenset(origins),
        )


class PolicyEngine:
    """Evaluate configured policy without persistence or network access."""

    def __init__(self, policy: SpendPolicy) -> None:
        self.policy = policy

    def evaluate(
        self,
        request: PaymentRequest,
        *,
        spent_total: AmountInput = Decimal(0),
        spent_today: AmountInput = Decimal(0),
        origin: str | None = None,
        now: datetime | None = None,
    ) -> PolicyDecision:
        if not isinstance(request, PaymentRequest):
            raise ValidationError("Policy evaluation requires a PaymentRequest")
        try:
            total = validate_amount(spent_total, positive=False)
            today = validate_amount(spent_today, positive=False)
        except ValidationError:
            return self._deny(
                "invalid_spend", "Recorded spend must be a finite non-negative amount", request
            )
        if total < 0 or today < 0:
            return self._deny("invalid_spend", "Recorded spend cannot be negative", request)

        policy = self.policy
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValidationError("Evaluation time must be timezone-aware")
        if policy.expires_at is not None and current >= policy.expires_at:
            return self._deny("policy_expired", "Spend policy has expired", request)
        if request.network not in policy.allowed_networks:
            return self._deny("network_denied", "Network is not allowed", request)
        if request.recipient not in policy.allowed_recipients:
            return self._deny("recipient_denied", "Recipient is not allowed", request)
        if request.asset not in policy.allowed_assets:
            return self._deny("asset_denied", "Asset is not allowed", request)
        request_origin = origin if origin is not None else request.origin
        if policy.allowed_origins and request_origin not in policy.allowed_origins:
            return self._deny("origin_denied", "Origin is not allowed", request)
        request_units = amount_to_atomic_units(request.amount)
        today_units = amount_to_atomic_units(today)
        total_units = amount_to_atomic_units(total)
        if policy.max_payment is not None and request_units > amount_to_atomic_units(
            policy.max_payment
        ):
            return self._deny(
                "payment_limit_exceeded", "Payment exceeds per-payment limit", request
            )
        if policy.daily_limit is not None and today_units + request_units > amount_to_atomic_units(
            policy.daily_limit
        ):
            return self._deny(
                "daily_limit_exceeded", "Payment exceeds remaining daily limit", request
            )
        if policy.total_limit is not None and total_units + request_units > amount_to_atomic_units(
            policy.total_limit
        ):
            return self._deny(
                "total_limit_exceeded", "Payment exceeds remaining total limit", request
            )
        return PolicyDecision(True, "allowed", "Payment is allowed", request.request_id)

    @staticmethod
    def _deny(code: str, reason: str, request: PaymentRequest) -> PolicyDecision:
        return PolicyDecision(False, code, reason, request.request_id)


__all__ = ["PolicyEngine", "SpendPolicy"]


def _string_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValidationError(f"{field_name} must be a list of strings")
    return value


def _parse_network(value: str) -> Network:
    try:
        return Network(value)
    except ValueError as exc:
        raise ValidationError(f"Unsupported policy network: {value}") from exc


def _policy_amount(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a decimal string or null")
    return validate_amount(value)
