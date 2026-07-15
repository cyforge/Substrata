from decimal import Decimal

import pytest

from substrata.constants import Network
from substrata.errors import ValidationError
from substrata.models import (
    Asset,
    PaymentRequest,
    PaymentResult,
    PaymentStatus,
    SignResponse,
    TransactionMetadata,
)


def test_payment_request_normalizes_exact_amount_and_generates_id():
    request = PaymentRequest("GRECIPIENT", "1.2300000", Asset("XLM"))

    assert request.amount == Decimal("1.2300000")
    assert request.request_id
    assert request.network is Network.STELLAR_TESTNET


@pytest.mark.parametrize("amount", [True, 1.1, 0, -1, "NaN", "Infinity", "0.00000001"])
def test_payment_request_rejects_invalid_amount(amount):
    with pytest.raises(ValidationError):
        PaymentRequest("GRECIPIENT", amount, Asset("XLM"))


def test_asset_and_payment_models_are_immutable_and_mapping_copied():
    metadata = {"memo": "hello"}
    request = PaymentRequest("GRECIPIENT", 1, Asset("USD", "GISSUER"), metadata=metadata)
    metadata["memo"] = "changed"

    assert request.asset.currency_key == "USD:GISSUER"
    assert request.metadata["memo"] == "hello"
    with pytest.raises(TypeError):
        request.metadata["memo"] = "blocked"


def test_soroban_assets_keep_contract_identity_in_currency_key():
    sac = Asset.soroban("CCONTRACT", code="USD")
    same_code_different_contract = Asset.soroban("COTHER", code="USD")

    assert sac.code == "USD"
    assert sac.currency_key == "contract:CCONTRACT"
    assert sac != same_code_different_contract
    with pytest.raises(ValidationError):
        Asset("USD", issuer="GISSUER", contract_id="CCONTRACT")


def test_result_and_signing_payload_have_typed_immutable_data():
    result = PaymentResult("req", PaymentStatus.SETTLED, amount=Decimal("2"), asset=Asset("XLM"))
    payload = {"transaction": "tx"}
    response = SignResponse(payload)
    payload["transaction"] = "changed"

    assert result.status is PaymentStatus.SETTLED
    assert response.payload["transaction"] == "tx"
    with pytest.raises(TypeError):
        response.payload["transaction"] = "blocked"


def test_transaction_metadata_validates_ledger_and_fee():
    assert TransactionMetadata("tx", Network.STELLAR_TESTNET, ledger=0, fee=Decimal(0)).ledger == 0
    with pytest.raises(ValidationError):
        TransactionMetadata("tx", Network.STELLAR_TESTNET, ledger=-1)
    with pytest.raises(ValidationError):
        TransactionMetadata("tx", Network.STELLAR_TESTNET, ledger="1")
