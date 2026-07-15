from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from substrata.constants import Network
from substrata.errors import ValidationError
from substrata.models import Asset, PaymentRequest
from substrata.policy import PolicyEngine, SpendPolicy


@pytest.fixture
def payment_request():
    return PaymentRequest("GRECIPIENT", Decimal("2"), Asset("XLM"), origin="https://shop.example")


def test_policy_allows_request_with_explicit_rules_and_headroom(payment_request):
    policy = SpendPolicy(
        allowed_networks=frozenset({Network.STELLAR_TESTNET}),
        allowed_recipients=frozenset({"GRECIPIENT"}),
        allowed_assets=frozenset({Asset("XLM")}),
        max_payment=3,
        daily_limit=10,
        total_limit=20,
        allowed_origins=frozenset({"https://shop.example"}),
    )

    result = PolicyEngine(policy).evaluate(payment_request, spent_today=3, spent_total=5)

    assert result.allowed
    assert result.reason_code == "allowed"


@pytest.mark.parametrize(
    ("policy", "kwargs", "code"),
    [
        (SpendPolicy(), {}, "network_denied"),
        (
            SpendPolicy(allowed_networks=frozenset({Network.STELLAR_TESTNET})),
            {},
            "recipient_denied",
        ),
        (
            SpendPolicy(
                allowed_networks=frozenset({Network.STELLAR_TESTNET}),
                allowed_recipients=frozenset({"GRECIPIENT"}),
            ),
            {},
            "asset_denied",
        ),
        (
            SpendPolicy(
                allowed_networks=frozenset({Network.STELLAR_TESTNET}),
                allowed_recipients=frozenset({"GRECIPIENT"}),
                allowed_assets=frozenset({Asset("XLM")}),
                max_payment=1,
            ),
            {},
            "payment_limit_exceeded",
        ),
        (
            SpendPolicy(
                allowed_networks=frozenset({Network.STELLAR_TESTNET}),
                allowed_recipients=frozenset({"GRECIPIENT"}),
                allowed_assets=frozenset({Asset("XLM")}),
                daily_limit=3,
            ),
            {"spent_today": 2},
            "daily_limit_exceeded",
        ),
        (
            SpendPolicy(
                allowed_networks=frozenset({Network.STELLAR_TESTNET}),
                allowed_recipients=frozenset({"GRECIPIENT"}),
                allowed_assets=frozenset({Asset("XLM")}),
                total_limit=3,
            ),
            {"spent_total": 2},
            "total_limit_exceeded",
        ),
        (
            SpendPolicy(
                allowed_networks=frozenset({Network.STELLAR_TESTNET}),
                allowed_recipients=frozenset({"GRECIPIENT"}),
                allowed_assets=frozenset({Asset("XLM")}),
                allowed_origins=frozenset({"https://other.example"}),
            ),
            {},
            "origin_denied",
        ),
    ],
)
def test_policy_denies_by_reason(payment_request, policy, kwargs, code):
    result = PolicyEngine(policy).evaluate(payment_request, **kwargs)
    assert not result.allowed
    assert result.reason_code == code


def test_policy_expiry_and_spend_inputs_are_checked(payment_request):
    policy = SpendPolicy(
        allowed_networks=frozenset({Network.STELLAR_TESTNET}),
        allowed_recipients=frozenset({"GRECIPIENT"}),
        allowed_assets=frozenset({Asset("XLM")}),
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    engine = PolicyEngine(policy)

    assert engine.evaluate(payment_request).reason_code == "policy_expired"
    assert engine.evaluate(payment_request, spent_total=-1).reason_code == "invalid_spend"
    with pytest.raises(ValidationError):
        SpendPolicy(expires_at=datetime.now())
