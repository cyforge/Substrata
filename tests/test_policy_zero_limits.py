from decimal import Decimal

from substrata.constants import Network
from substrata.models import Asset, PaymentRequest
from substrata.policy import PolicyEngine, SpendPolicy


def test_zero_limits_are_valid_and_deny_all_positive_payments():
    policy = SpendPolicy(
        allowed_networks=frozenset({Network.STELLAR_TESTNET}),
        allowed_recipients=frozenset({"GRECIPIENT"}),
        allowed_assets=frozenset({Asset("XLM")}),
        allowed_origins=frozenset({"https://shop.example"}),
        max_payment=0,
        daily_limit=0,
        total_limit=0,
    )
    request = PaymentRequest(
        "GRECIPIENT", 1, Asset("XLM"), origin="https://shop.example"
    )

    decision = PolicyEngine(policy).evaluate(request)
    restored = SpendPolicy.from_dict(policy.to_dict())

    assert decision.reason_code == "payment_limit_exceeded"
    assert decision.budget.daily_limit == Decimal(0)
    assert restored.max_payment == Decimal(0)
