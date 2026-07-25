import json
from decimal import Decimal

import pytest

from substrata.constants import Network
from substrata.errors import ValidationError
from substrata.models import (
    Asset,
    BudgetMetadata,
    PaymentRequest,
    PaymentResult,
    PaymentStatus,
    PolicyDecision,
    SignRequest,
    SignResponse,
    TransactionMetadata,
    deserialize_model,
    payment_request_fingerprint,
    serialize_model,
)


def test_payment_request_serialization_is_json_safe_and_does_not_mutate():
    request = PaymentRequest(
        "GRECIPIENT",
        Decimal("1.2300000"),
        Asset.soroban("CCONTRACT", code="USD"),
        metadata={"decimals": Decimal("7")},
    )

    data = serialize_model(request)
    json.dumps(data)

    assert data["amount"] == "1.2300000"
    assert data["asset"] == {"code": "USD", "issuer": None, "contract_id": "CCONTRACT"}
    assert data["metadata"] == {"decimals": "7"}
    assert request.amount == Decimal("1.2300000")


def test_payment_result_serialization_keeps_network_status_and_transaction():
    result = PaymentResult(
        "req-1",
        PaymentStatus.SETTLED,
        transaction_id="tx-1",
        amount=Decimal("2"),
        asset=Asset("XLM"),
        network=Network.STELLAR_TESTNET,
    )

    assert serialize_model(result) == {
        "request_id": "req-1",
        "status": "settled",
        "transaction_id": "tx-1",
        "amount": "2",
        "asset": {"code": "XLM", "issuer": None, "contract_id": None},
        "network": "stellar_testnet",
        "reason": None,
    }


def test_serialization_rejects_non_json_numbers():
    with pytest.raises(ValidationError):
        PaymentRequest("GRECIPIENT", 1, Asset("XLM"), metadata={"bad": float("nan")})


def test_policy_decision_serializes_budget_metadata_as_exact_strings():
    decision = PolicyDecision(
        False,
        "daily_limit_exceeded",
        "Daily cap reached",
        "req-1",
        BudgetMetadata(
            spent_today=Decimal("2"),
            spent_total=Decimal("5"),
            daily_limit=Decimal("3"),
            remaining_today=Decimal("1"),
        ),
    )

    assert serialize_model(decision)["budget"] == {
        "spent_today": "2",
        "spent_total": "5",
        "daily_limit": "3",
        "total_limit": None,
        "remaining_today": "1",
        "remaining_total": None,
    }


@pytest.mark.parametrize(
    "model",
    [
        Asset("USD", "GISSUER"),
        PaymentRequest("GRECIPIENT", Decimal("1.25"), Asset("XLM")),
        PaymentResult("req", PaymentStatus.SETTLED, amount=Decimal("1"), asset=Asset("XLM")),
        PolicyDecision(
            True,
            "allowed",
            "Payment is allowed",
            "req",
            BudgetMetadata(spent_today=Decimal("2"), spent_total=Decimal("5")),
        ),
        SignRequest(PaymentRequest("GRECIPIENT", Decimal("1"), Asset("XLM"))),
        SignResponse({"memo": "hello", "nested": ["one"]}, signature="abc"),
        TransactionMetadata(
            "tx",
            Network.STELLAR_TESTNET,
            ledger=123,
            fee=Decimal("0.1"),
            status=PaymentStatus.SETTLED,
        ),
    ],
)
def test_core_model_serialization_round_trips(model):
    assert deserialize_model(type(model), serialize_model(model)) == model


def test_deserializer_rejects_unknown_fields_and_wrong_decimal_wire_types():
    request_data = serialize_model(PaymentRequest("GRECIPIENT", 1, Asset("XLM")))
    with pytest.raises(ValidationError, match="Unknown model fields"):
        deserialize_model(PaymentRequest, {**request_data, "amunt": "1"})
    with pytest.raises(ValidationError, match="decimal string"):
        deserialize_model(PaymentRequest, {**request_data, "amount": 1})


def test_payment_request_fingerprint_is_canonical_and_binds_all_request_fields():
    asset = Asset("USD", "GISSUER")
    left = PaymentRequest(
        "GRECIPIENT",
        "1.0",
        asset,
        request_id="same-id",
        metadata={"memo": "hi", "source": {"a": 1, "b": 2}},
    )
    right = PaymentRequest(
        "GRECIPIENT",
        Decimal("1.0"),
        asset,
        request_id="same-id",
        metadata={"source": {"b": 2, "a": 1}, "memo": "hi"},
    )
    changed = PaymentRequest("GRECIPIENT", 1, asset, request_id="same-id", metadata={"memo": "bye"})

    assert payment_request_fingerprint(left) == payment_request_fingerprint(right)
    assert payment_request_fingerprint(left) != payment_request_fingerprint(changed)
