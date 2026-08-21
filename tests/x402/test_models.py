import pytest

from substrata.errors import ValidationError
from substrata.x402.models import (
    PaymentPayload,
    PaymentRequired,
    PaymentRequirements,
    ResourceInfo,
    SettlementResponse,
    VerifyResponse,
)

REQ = {
    "scheme": "exact",
    "network": "stellar:testnet",
    "amount": "10000000",
    "asset": "CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA",
    "payTo": "GBHEGW3KWOY2OFH767EDALFGCUTBOEVBDQMCKU4APMDLQNBW5QV3W3KO",
    "maxTimeoutSeconds": 60,
    "extra": {"areFeesSponsored": True},
}


def test_stellar_v2_wire_fields_round_trip() -> None:
    requirements = PaymentRequirements.from_dict(REQ)
    assert requirements.to_dict() == REQ
    required = PaymentRequired.from_dict(
        {
            "x402Version": 2,
            "resource": {"url": "https://example.test/data", "mimeType": "application/json"},
            "accepts": [REQ],
        }
    )
    assert required.to_dict()["accepts"] == [REQ]
    payload = PaymentPayload.from_dict(
        {
            "x402Version": 2,
            "accepted": REQ,
            "payload": {"transaction": "YWJj"},
        }
    )
    assert payload.to_dict()["payload"] == {"transaction": "YWJj"}
    assert payload.accepted.network == "stellar:testnet"


def test_requirements_reject_invalid_amount_and_boolean_timeout() -> None:
    for amount in ("0", "-1", "1.0", "１２", str(1 << 256)):
        with pytest.raises(ValidationError):
            PaymentRequirements.from_dict({**REQ, "amount": amount})
    with pytest.raises(ValidationError):
        PaymentRequirements.from_dict({**REQ, "maxTimeoutSeconds": True})


def test_version_and_required_array_are_strict() -> None:
    with pytest.raises(ValidationError):
        PaymentRequired.from_dict(
            {
                "x402Version": True,
                "resource": {"url": "x"},
                "accepts": [REQ],
            }
        )
    with pytest.raises(ValidationError):
        PaymentRequired.from_dict(
            {
                "x402Version": 2,
                "resource": {"url": "x"},
                "accepts": [],
            }
        )


def test_verify_and_settle_responses_keep_wire_names_and_validate_booleans() -> None:
    verify = VerifyResponse.from_dict({"isValid": False, "invalidReason": "invalid_payload"})
    assert verify.to_dict() == {"isValid": False, "invalidReason": "invalid_payload"}
    settle = SettlementResponse.from_dict(
        {
            "success": False,
            "transaction": "hash",
            "network": "stellar:testnet",
            "errorReason": "settlement_pending",
            "payer": "GABC",
        }
    )
    assert settle.to_dict()["errorReason"] == "settlement_pending"
    with pytest.raises(ValidationError):
        SettlementResponse.from_dict(
            {
                "success": True,
                "transaction": "",
                "network": "stellar:testnet",
            }
        ) if False else SettlementResponse.from_dict(
            {
                "success": 1,
                "transaction": "",
                "network": "stellar:testnet",
            }
        )
    with pytest.raises(ValidationError):
        SettlementResponse(False, "", "stellar:testnet", "settlement_pending")


def test_resource_metadata_is_typed() -> None:
    resource = ResourceInfo.from_dict({"url": "https://example.test", "tags": ["data"]})
    assert resource.to_dict() == {"url": "https://example.test", "tags": ["data"]}
    with pytest.raises(ValidationError):
        ResourceInfo.from_dict({"url": "https://example.test", "tags": "data"})


def test_nested_scheme_payload_is_copied_and_read_only() -> None:
    source = {"transaction": "xdr", "proof": {"siblings": ["a", "b"]}}
    payload = PaymentPayload(PaymentRequirements.from_dict(REQ), source)
    source["proof"]["siblings"].append("changed")
    assert payload.to_dict()["payload"]["proof"] == {"siblings": ["a", "b"]}
    with pytest.raises(TypeError):
        payload.payload["transaction"] = "changed"  # type: ignore[index]
    with pytest.raises(AttributeError):
        payload.payload["proof"]["siblings"].append("changed")  # type: ignore[union-attr]


def test_direct_constructors_validate_domain_types_and_resource_urls() -> None:
    with pytest.raises(ValidationError):
        PaymentPayload("requirements", {})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ResourceInfo("javascript:alert(1)")
    with pytest.raises(ValidationError):
        ResourceInfo("https://example.test", icon_url="file:///etc/passwd")
    with pytest.raises(ValidationError):
        PaymentRequirements("exact", "stellar:testnet", "1e5", "asset", "payee", 1)
