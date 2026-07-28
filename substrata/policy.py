"""Pure spend policy evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from .constants import Network
from .errors import ValidationError
from .models import (
    Asset,
    BudgetMetadata,
    PaymentRequest,
    PolicyDecision,
    PolicyReasonCode,
    serialize_model,
)
from .validation import (
    AmountInput,
    amount_from_atomic_units,
    amount_to_atomic_units,
    normalize_origin,
    validate_amount,
)


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
    deny_all_origins: bool = False

    def __post_init__(self) -> None:
        for name in ("allowed_networks", "allowed_recipients", "allowed_assets", "allowed_origins"):
            object.__setattr__(self, name, frozenset(getattr(self, name)))
        object.__setattr__(
            self,
            "allowed_origins",
            frozenset(normalize_origin(origin) for origin in self.allowed_origins),
        )
        for value in self.allowed_networks:
            if not isinstance(value, Network):
                raise ValidationError("Allowed networks must contain Network values")
        for value in self.allowed_assets:
            if not isinstance(value, Asset):
                raise ValidationError("Allowed assets must contain Asset values")
        for value in self.allowed_recipients | self.allowed_origins:
            if not isinstance(value, str) or not value.strip():
                raise ValidationError("Allowed recipients and origins must be non-empty strings")
        if not isinstance(self.deny_all_origins, bool):
            raise ValidationError("deny_all_origins must be a bool")
        for name in ("max_payment", "daily_limit", "total_limit"):
            value = getattr(self, name)
            if value is not None:
                amount = validate_amount(value)
                object.__setattr__(self, name, amount)
        if self.expires_at is not None and (
            not isinstance(self.expires_at, datetime) or self.expires_at.tzinfo is None
        ):
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
            "deny_all_origins": self.deny_all_origins,
        }

    def intersect(self, other: SpendPolicy) -> SpendPolicy:
        """Combine restrictions so result permits only actions allowed by both."""
        if not isinstance(other, SpendPolicy):
            raise ValidationError("Policy intersection requires another SpendPolicy")
        expiries = [value for value in (self.expires_at, other.expires_at) if value is not None]
        return SpendPolicy(
            allowed_networks=self.allowed_networks & other.allowed_networks,
            allowed_recipients=self.allowed_recipients & other.allowed_recipients,
            allowed_assets=self.allowed_assets & other.allowed_assets,
            max_payment=_minimum_limit(self.max_payment, other.max_payment),
            daily_limit=_minimum_limit(self.daily_limit, other.daily_limit),
            total_limit=_minimum_limit(self.total_limit, other.total_limit),
            expires_at=min(expiries) if expiries else None,
            allowed_origins=self.allowed_origins & other.allowed_origins,
            deny_all_origins=(
                self.deny_all_origins
                or other.deny_all_origins
                or (
                    bool(self.allowed_origins) != bool(other.allowed_origins)
                    or (
                        bool(self.allowed_origins)
                        and not (self.allowed_origins & other.allowed_origins)
                    )
                )
            ),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> SpendPolicy:
        """Parse strict policy data; unknown fields fail closed."""
        if not isinstance(value, Mapping):
            raise ValidationError("Policy data must be a mapping")
        if len(value) > 9:
            raise ValidationError("Policy data exceeds supported field count")
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
            "deny_all_origins",
        }
        extra = set(value) - allowed_fields
        if extra:
            raise ValidationError(f"Unknown policy fields: {', '.join(sorted(extra))}")

        networks = _string_list(value.get("allowed_networks", []), "allowed_networks")
        recipients = _string_list(value.get("allowed_recipients", []), "allowed_recipients")
        origins = _string_list(value.get("allowed_origins", []), "allowed_origins")
        raw_assets = value.get("allowed_assets", [])
        if not isinstance(raw_assets, list) or len(raw_assets) > 10_000:
            raise ValidationError("allowed_assets must be a list")
        assets = []
        for raw_asset in raw_assets:
            if not isinstance(raw_asset, Mapping):
                raise ValidationError("Each allowed asset must be a mapping")
            assets.append(Asset.from_dict(raw_asset))

        expiry_value = value.get("expires_at")
        expires_at = None
        if expiry_value is not None:
            if (
                not isinstance(expiry_value, str)
                or len(expiry_value) > 64
                or re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
                    expiry_value,
                )
                is None
            ):
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
            deny_all_origins=_optional_bool(value, "deny_all_origins", default=False),
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
                PolicyReasonCode.INVALID_SPEND,
                "Recorded spend must be a finite non-negative amount",
                request,
            )
        if total < 0 or today < 0:
            return self._deny(
                PolicyReasonCode.INVALID_SPEND, "Recorded spend cannot be negative", request
            )

        policy = self.policy
        today_units = amount_to_atomic_units(today)
        total_units = amount_to_atomic_units(total)
        daily_limit_units = (
            amount_to_atomic_units(policy.daily_limit) if policy.daily_limit is not None else None
        )
        total_limit_units = (
            amount_to_atomic_units(policy.total_limit) if policy.total_limit is not None else None
        )
        budget = BudgetMetadata(
            spent_today=today,
            spent_total=total,
            daily_limit=policy.daily_limit,
            total_limit=policy.total_limit,
            remaining_today=(
                amount_from_atomic_units(max(0, daily_limit_units - today_units))
                if daily_limit_units is not None
                else None
            ),
            remaining_total=(
                amount_from_atomic_units(max(0, total_limit_units - total_units))
                if total_limit_units is not None
                else None
            ),
        )
        if now is not None and not isinstance(now, datetime):
            raise ValidationError("Evaluation time must be a datetime")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValidationError("Evaluation time must be timezone-aware")
        if policy.expires_at is not None and current >= policy.expires_at:
            return self._deny(
                PolicyReasonCode.POLICY_EXPIRED, "Spend policy has expired", request, budget
            )
        if request.network not in policy.allowed_networks:
            return self._deny(
                PolicyReasonCode.NETWORK_DENIED, "Network is not allowed", request, budget
            )
        if request.recipient not in policy.allowed_recipients:
            return self._deny(
                PolicyReasonCode.RECIPIENT_DENIED, "Recipient is not allowed", request, budget
            )
        if request.asset not in policy.allowed_assets:
            return self._deny(
                PolicyReasonCode.ASSET_DENIED, "Asset is not allowed", request, budget
            )
        request_origin = origin if origin is not None else request.origin
        if origin is not None:
            request_origin = normalize_origin(origin)
        if policy.deny_all_origins or (
            (request_origin is not None or policy.allowed_origins)
            and (request_origin not in policy.allowed_origins)
        ):
            return self._deny(
                PolicyReasonCode.ORIGIN_DENIED, "Origin is not allowed", request, budget
            )
        request_units = amount_to_atomic_units(request.amount)
        if policy.max_payment is not None and request_units > amount_to_atomic_units(
            policy.max_payment
        ):
            return self._deny(
                PolicyReasonCode.PAYMENT_LIMIT_EXCEEDED,
                "Payment exceeds per-payment limit",
                request,
                budget,
            )
        if policy.daily_limit is not None and today_units + request_units > amount_to_atomic_units(
            policy.daily_limit
        ):
            return self._deny(
                PolicyReasonCode.DAILY_LIMIT_EXCEEDED,
                "Payment exceeds remaining daily limit",
                request,
                budget,
            )
        if policy.total_limit is not None and total_units + request_units > amount_to_atomic_units(
            policy.total_limit
        ):
            return self._deny(
                PolicyReasonCode.TOTAL_LIMIT_EXCEEDED,
                "Payment exceeds remaining total limit",
                request,
                budget,
            )
        return PolicyDecision(
            True, PolicyReasonCode.ALLOWED, "Payment is allowed", request.request_id, budget
        )

    @staticmethod
    def _deny(
        code: PolicyReasonCode,
        reason: str,
        request: PaymentRequest,
        budget: BudgetMetadata | None = None,
    ) -> PolicyDecision:
        return PolicyDecision(False, code, reason, request.request_id, budget)


__all__ = ["PolicyEngine", "SpendPolicy"]


def _string_list(value: object, field_name: str) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > 10_000
        or any(not isinstance(item, str) for item in value)
    ):
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


def _optional_bool(value: Mapping[str, object], key: str, *, default: bool) -> bool:
    result = value.get(key, default)
    if not isinstance(result, bool):
        raise ValidationError(f"{key} must be a bool")
    return result


def _minimum_limit(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)
