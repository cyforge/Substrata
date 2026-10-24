from decimal import Decimal

import pytest

from substrata.constants import Network
from substrata.errors import ValidationError
from substrata.models import (
    Asset,
    PaymentRequest,
    PaymentResult,
    PaymentStatus,
    PolicyDecision,
    PolicyReasonCode,
    SignerFailure,
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
    metadata = {"memo": "hello", "nested": {"tags": ["initial"]}}
    request = PaymentRequest("GRECIPIENT", 1, Asset("USD", "GISSUER"), metadata=metadata)
    metadata["memo"] = "changed"
    metadata["nested"]["tags"].append("changed")

    assert request.asset.currency_key == "USD:GISSUER"
    assert request.metadata["memo"] == "hello"
    assert request.metadata["nested"]["tags"] == ("initial",)
    with pytest.raises(TypeError):
        request.metadata["memo"] = "blocked"
    with pytest.raises(TypeError):
        request.metadata["nested"]["tags"] = ("blocked",)


def test_model_metadata_rejects_cycles_excessive_depth_and_nonfinite_decimals():
    cyclic = {}
    cyclic["self"] = cyclic
    with pytest.raises(ValidationError):
        PaymentRequest("GRECIPIENT", 1, Asset("XLM"), metadata=cyclic)

    deep = 0
    for _ in range(40):
        deep = [deep]
    with pytest.raises(ValidationError):
        PaymentRequest("GRECIPIENT", 1, Asset("XLM"), metadata={"deep": deep})

    with pytest.raises(ValidationError):
        PaymentRequest("GRECIPIENT", 1, Asset("XLM"), metadata={"amount": Decimal("Infinity")})


def test_soroban_assets_keep_contract_identity_in_currency_key():
    sac = Asset.soroban("CCONTRACT", code="USD")
    same_code_different_contract = Asset.soroban("COTHER", code="USD")
    same_contract_different_display_code = Asset.soroban("CCONTRACT", code="TOKEN")

    assert sac.code == "USD"
    assert sac.currency_key == "contract:CCONTRACT"
    assert sac != same_code_different_contract
    assert sac == same_contract_different_display_code
    assert hash(sac) == hash(same_contract_different_display_code)
    with pytest.raises(ValidationError):
        Asset("USD", issuer="GISSUER", contract_id="CCONTRACT")
    assert Asset.from_dict({"code": "USD", "contract_id": "CCONTRACT"}) == sac
    with pytest.raises(ValidationError):
        Asset.from_dict({"code": "USD", "typo": "CCONTRACT"})


def test_soroban_factory_defaults_display_code_for_long_contract_ids():
    contract_id = "C" + "A" * 55

    asset = Asset.soroban(contract_id)

    assert asset.code == "TOKEN"
    assert asset.contract_id == contract_id
    assert asset.currency_key == f"contract:{contract_id}"
    with pytest.raises(ValidationError):
        Asset.soroban(contract_id, code="")


def test_payment_currency_key_is_scoped_by_network():
    asset = Asset.soroban("CCONTRACT", code="USD")
    testnet = PaymentRequest("GRECIPIENT", 1, asset, Network.STELLAR_TESTNET)
    mainnet = PaymentRequest("GRECIPIENT", 1, asset, Network.STELLAR_MAINNET)

    assert testnet.currency_key == "stellar_testnet:contract:CCONTRACT"
    assert mainnet.currency_key == "stellar_mainnet:contract:CCONTRACT"
    assert testnet.currency_key != mainnet.currency_key


@pytest.mark.parametrize("code", ["usd", "A" * 13, "A" * 100_000, "USD!", "EUR"])
def test_classic_asset_requires_valid_code_and_issuer_for_non_native(code):
    with pytest.raises(ValidationError):
        Asset(code)


def test_result_and_signing_payload_have_typed_immutable_data():
    result = PaymentResult("req", PaymentStatus.SETTLED, amount=Decimal("2"), asset=Asset("XLM"))
    payload = {"transaction": "tx"}
    response = SignResponse(payload)
    payload["transaction"] = "changed"

    assert result.status is PaymentStatus.SETTLED
    assert response.payload["transaction"] == "tx"
    with pytest.raises(TypeError):
        response.payload["transaction"] = "blocked"
    with pytest.raises(ValidationError):
        PaymentResult("req", PaymentStatus.FAILED, transaction_id=" ")
    with pytest.raises(ValidationError):
        SignResponse({"transaction": "tx"}, signature=" ")
    failed_response = SignResponse(
        {}, failure=SignerFailure("user_declined", "Signing request declined")
    )
    assert failed_response.succeeded is False
    with pytest.raises(ValidationError):
        SignResponse(
            {"transaction": "tx"},
            failure=SignerFailure("provider_error", "Remote signer failed"),
        )
    decision = PolicyDecision(True, PolicyReasonCode.ALLOWED, "Payment is allowed")
    assert decision.reason_code == "allowed"
    with pytest.raises(ValidationError):
        PolicyDecision(True, "network_denied", "Network is not allowed")
    with pytest.raises(ValidationError):
        PolicyDecision(False, PolicyReasonCode.ALLOWED, "Payment is allowed")


def test_transaction_metadata_validates_ledger_and_fee():
    assert TransactionMetadata("tx", Network.STELLAR_TESTNET, ledger=0, fee=Decimal(0)).ledger == 0
    with pytest.raises(ValidationError):
        TransactionMetadata("tx", Network.STELLAR_TESTNET, ledger=-1)
    with pytest.raises(ValidationError):
        TransactionMetadata("tx", Network.STELLAR_TESTNET, ledger="1")
    with pytest.raises(ValidationError):
        TransactionMetadata("tx", Network.STELLAR_TESTNET, created_at="yesterday")
    with pytest.raises(ValidationError):
        TransactionMetadata("tx", Network.STELLAR_TESTNET, raw=[])
    with pytest.raises(ValidationError):
        TransactionMetadata("tx", Network.STELLAR_TESTNET, status="settled")
    with pytest.raises(ValidationError):
        Asset("XLM", issuer="GISSUER")
