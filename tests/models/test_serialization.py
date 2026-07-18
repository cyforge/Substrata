import json
from decimal import Decimal

import pytest

from substrata.constants import Network
from substrata.errors import ValidationError
from substrata.models import Asset, PaymentRequest, PaymentResult, PaymentStatus, serialize_model


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
