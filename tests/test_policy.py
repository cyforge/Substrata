from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

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
    assert result.budget.spent_today == Decimal("3")
    assert result.budget.remaining_today == Decimal("7.0000000")
    assert result.budget.remaining_total == Decimal("15.0000000")


def test_policy_normalizes_request_and_configured_origins():
    request = PaymentRequest("GRECIPIENT", 1, Asset("XLM"), origin="HTTPS://SHOP.EXAMPLE:443/")
    policy = SpendPolicy(
        allowed_networks=frozenset({Network.STELLAR_TESTNET}),
        allowed_recipients=frozenset({"GRECIPIENT"}),
        allowed_assets=frozenset({Asset("XLM")}),
        allowed_origins=frozenset({"https://shop.example"}),
    )

    assert request.origin == "https://shop.example"
    assert PolicyEngine(policy).evaluate(request).allowed


def test_policy_denies_origin_context_when_allowlist_is_missing_or_mismatched():
    request = PaymentRequest("GRECIPIENT", 1, Asset("XLM"), origin="https://shop.example")
    base = {
        "allowed_networks": frozenset({Network.STELLAR_TESTNET}),
        "allowed_recipients": frozenset({"GRECIPIENT"}),
        "allowed_assets": frozenset({Asset("XLM")}),
    }
    no_origin_policy = SpendPolicy(**base)
    missing_request_origin_policy = SpendPolicy(
        **base,
        allowed_origins=frozenset({"https://shop.example"}),
    )

    assert PolicyEngine(no_origin_policy).evaluate(request).reason_code == "origin_denied"
    assert (
        PolicyEngine(missing_request_origin_policy)
        .evaluate(PaymentRequest("GRECIPIENT", 1, Asset("XLM")))
        .reason_code
        == "origin_denied"
    )


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
                allowed_origins=frozenset({"https://shop.example"}),
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
                allowed_origins=frozenset({"https://shop.example"}),
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
                allowed_origins=frozenset({"https://shop.example"}),
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


def test_denied_policy_decision_includes_current_budget(payment_request):
    policy = SpendPolicy(
        allowed_networks=frozenset({Network.STELLAR_TESTNET}),
        allowed_recipients=frozenset({"GRECIPIENT"}),
        allowed_assets=frozenset({Asset("XLM")}),
        daily_limit=3,
    )

    result = PolicyEngine(policy).evaluate(payment_request, spent_today=2)

    assert not result.allowed
    assert result.budget.daily_limit == Decimal("3")
    assert result.budget.remaining_today == Decimal("1.0000000")


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


def test_policy_limit_comparison_stays_exact_under_low_decimal_precision():
    amount = Asset("XLM")
    request = PaymentRequest("GRECIPIENT", Decimal("0.0000001"), amount)
    large_limit = Decimal("9999999999999999999999999999999999999")
    policy = SpendPolicy(
        allowed_networks=frozenset({Network.STELLAR_TESTNET}),
        allowed_recipients=frozenset({"GRECIPIENT"}),
        allowed_assets=frozenset({amount}),
        daily_limit=large_limit,
    )

    with localcontext() as context:
        context.prec = 2
        decision = PolicyEngine(policy).evaluate(request, spent_today=large_limit)

    assert decision.reason_code == "daily_limit_exceeded"


def test_spend_policy_json_round_trip_preserves_rules_and_exact_amounts():
    policy = SpendPolicy(
        allowed_networks=frozenset({Network.STELLAR_TESTNET}),
        allowed_recipients=frozenset({"GRECIPIENT"}),
        allowed_assets=frozenset({Asset.soroban("CCONTRACT", code="USD")}),
        max_payment=Decimal("1.2300000"),
        daily_limit=10,
        total_limit=Decimal("20.50"),
        expires_at=datetime(2027, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        allowed_origins=frozenset({"https://shop.example"}),
    )

    restored = SpendPolicy.from_dict(policy.to_dict())

    assert restored == policy
    assert restored.max_payment == Decimal("1.2300000")
    assert restored.allowed_assets == policy.allowed_assets


def test_policy_intersection_combines_allowlists_caps_and_earliest_expiry():
    expiry = datetime(2027, 1, 1, tzinfo=timezone.utc)
    first = SpendPolicy(
        allowed_networks=frozenset({Network.STELLAR_TESTNET, Network.STELLAR_MAINNET}),
        allowed_recipients=frozenset({"GRECIPIENT", "GOTHER"}),
        allowed_assets=frozenset({Asset("XLM"), Asset("USD", "GISSUER")}),
        max_payment=10,
        daily_limit=20,
        expires_at=expiry + timedelta(days=1),
        allowed_origins=frozenset({"https://shop.example", "https://other.example"}),
    )
    second = SpendPolicy(
        allowed_networks=frozenset({Network.STELLAR_TESTNET}),
        allowed_recipients=frozenset({"GRECIPIENT"}),
        allowed_assets=frozenset({Asset("XLM")}),
        max_payment=5,
        total_limit=7,
        expires_at=expiry,
        allowed_origins=frozenset({"https://shop.example"}),
    )

    combined = first.intersect(second)

    assert combined.allowed_networks == frozenset({Network.STELLAR_TESTNET})
    assert combined.allowed_recipients == frozenset({"GRECIPIENT"})
    assert combined.allowed_assets == frozenset({Asset("XLM")})
    assert combined.max_payment == Decimal("5")
    assert combined.daily_limit == Decimal("20")
    assert combined.total_limit == Decimal("7")
    assert combined.expires_at == expiry
    assert combined.allowed_origins == frozenset({"https://shop.example"})

    blocked = first.intersect(SpendPolicy())
    assert not blocked.allowed_networks


@pytest.mark.parametrize(
    ("left_origins", "right_origins"),
    [
        (frozenset(), frozenset()),
        (frozenset({"https://a.example"}), frozenset({"https://a.example"})),
        (frozenset({"https://a.example"}), frozenset({"https://b.example"})),
        (frozenset(), frozenset({"https://a.example"})),
    ],
)
def test_intersection_never_allows_origin_context_rejected_by_parent(left_origins, right_origins):
    baseline = {
        "allowed_networks": frozenset({Network.STELLAR_TESTNET}),
        "allowed_recipients": frozenset({"GRECIPIENT"}),
        "allowed_assets": frozenset({Asset("XLM")}),
    }
    left = SpendPolicy(**baseline, allowed_origins=left_origins)
    right = SpendPolicy(**baseline, allowed_origins=right_origins)
    combined = left.intersect(right)

    for origin in (None, "https://a.example", "https://b.example"):
        request = PaymentRequest("GRECIPIENT", 1, Asset("XLM"), origin=origin)
        combined_allowed = PolicyEngine(combined).evaluate(request).allowed
        assert not combined_allowed or PolicyEngine(left).evaluate(request).allowed
        assert not combined_allowed or PolicyEngine(right).evaluate(request).allowed


@pytest.mark.parametrize(
    "data",
    [
        {"allowd_recipients": ["GRECIPIENT"]},
        {"allowed_networks": ["stellar:unknown"]},
        {"daily_limit": 10},
        {"allowed_recipients": "GRECIPIENT"},
        {"expires_at": "2027-01-02T03:04:05"},
        {"expires_at": "2027-01-02T03:04Z"},
        {"expires_at": "2027-01-02T03:04:05+00"},
        {"allowed_assets": [{"code": "XLM", "extra": "typo"}]},
        {"deny_all_origins": "false"},
        {"allowed_recipients": ["recipient"] * 10_001},
        {"expires_at": "2027-01-02T03:04:05Z" + "0" * 65},
    ],
)
def test_spend_policy_parser_rejects_unknown_or_malformed_fields(data):
    with pytest.raises(ValidationError):
        SpendPolicy.from_dict(data)
