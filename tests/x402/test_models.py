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
    assert payload.stellar_transaction_xdr == b"abc"


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
    generic_requirement = PaymentRequirements(
        "exact", "eip155:8453", "25", "0xToken", "0xRecipient", 60
    )
    payload = PaymentPayload(generic_requirement, source)
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


def test_mutable_inputs_are_snapshotted_and_dict_outputs_are_mutable_copies() -> None:
    tags = ["one"]
    resource = ResourceInfo("http://localhost:8080/data", tags=tags)  # type: ignore[arg-type]
    tags.append("two")
    assert resource.tags == ("one",)
    requirements_data = {"name": "demo"}
    requirements = PaymentRequirements(
        "exact",
        "eip155:8453",
        "1",
        "token",
        "recipient",
        60,
        extra={"areFeesSponsored": True, **requirements_data},
    )
    requirements_data["name"] = "changed"
    wire_copy = requirements.to_dict()
    wire_copy["extra"]["name"] = "mutated"
    assert requirements.extra["name"] == "demo"


def test_url_validation_rejects_credentials_fragments_bad_ports_and_controls() -> None:
    for url in (
        "https://user:password@example.test/data",
        "https://example.test:port/data",
        "https://example.test:/data",
        "https://[::1/data",
        "https://example.test/data#",
        "https://example.test/bad%2",
        "https:\\example.test\\data",
        "https://example.test/data\nX-Injected: yes",
    ):
        with pytest.raises(ValidationError):
            ResourceInfo(url)


def test_caip2_and_unknown_wire_fields_are_validated() -> None:
    for network in ("stellar", "ab:testnet", "Stellar:testnet", "stellar:"):
        with pytest.raises(ValidationError):
            PaymentRequirements("exact", network, "1", "asset", "payee", 60)
    with pytest.raises(ValidationError, match="unsupported fields"):
        PaymentRequirements.from_dict({**REQ, "ignored": True})


def test_resource_spec_metadata_limits_are_enforced() -> None:
    with pytest.raises(ValidationError):
        ResourceInfo("https://example.test", service_name="x" * 33)
    with pytest.raises(ValidationError):
        ResourceInfo("https://example.test", tags=["x"] * 6)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ResourceInfo("https://example.test", tags=["bad\nvalue"])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("builder", "value"),
    [
        (
            ResourceInfo.from_dict,
            {"url": "https://example.test", "description": None},
        ),
        (
            ResourceInfo.from_dict,
            {"url": "https://example.test", "tags": None},
        ),
        (
            PaymentRequirements.from_dict,
            {**REQ, "extra": None},
        ),
        (
            PaymentRequired.from_dict,
            {
                "x402Version": 2,
                "resource": {"url": "https://example.test"},
                "accepts": [REQ],
                "error": None,
            },
        ),
        (
            PaymentPayload.from_dict,
            {"x402Version": 2, "accepted": REQ, "payload": {}, "resource": None},
        ),
        (
            VerifyResponse.from_dict,
            {"isValid": True, "payer": None},
        ),
        (
            SettlementResponse.from_dict,
            {
                "success": True,
                "transaction": "abc",
                "network": "stellar:testnet",
                "amount": None,
            },
        ),
    ],
)
def test_wire_optional_fields_reject_explicit_null(builder, value) -> None:
    with pytest.raises(ValidationError):
        builder(value)


def test_stellar_atomic_amount_uses_signed_128_bit_bound() -> None:
    maximum = str((1 << 127) - 1)
    accepted = PaymentRequirements.from_dict({**REQ, "amount": maximum})
    assert accepted.amount == maximum
    with pytest.raises(ValidationError, match="Stellar amount"):
        PaymentRequirements.from_dict({**REQ, "amount": str(1 << 127)})


@pytest.mark.parametrize("extra", [None, {}, {"areFeesSponsored": False}, {"areFeesSponsored": 1}])
def test_exact_stellar_requirements_require_sponsored_fees(extra) -> None:
    with pytest.raises(ValidationError, match="fee sponsorship"):
        PaymentRequirements("exact", "stellar:testnet", "1", "CABC", "GABC", 60, extra=extra)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"transaction": ""},
        {"transaction": "not-base64"},
        {"transaction": "eA==="},
        {"transaction": "eA==", "nonce": "unexpected"},
    ],
)
def test_exact_stellar_payload_requires_only_canonical_bounded_xdr(payload) -> None:
    with pytest.raises(ValidationError, match="Stellar|transaction"):
        PaymentPayload(PaymentRequirements.from_dict(REQ), payload)


def test_non_stellar_scheme_payload_remains_scheme_specific() -> None:
    requirements = PaymentRequirements("exact", "eip155:8453", "1", "0xToken", "0xRecipient", 60)
    payload = PaymentPayload(requirements, {"signature": "opaque", "authorization": {}})
    assert payload.to_dict()["payload"] == {"signature": "opaque", "authorization": {}}
    with pytest.raises(ValidationError, match="exact Stellar"):
        _ = payload.stellar_transaction_xdr


def test_facilitator_request_is_typed_and_round_trips() -> None:
    from substrata.x402.models import FacilitatorPaymentRequest

    payload = PaymentPayload(PaymentRequirements.from_dict(REQ), {"transaction": "eA=="})
    request = FacilitatorPaymentRequest(payload, payload.accepted)
    restored = FacilitatorPaymentRequest.from_dict(request.to_dict())
    assert restored == request
    with pytest.raises(ValidationError):
        FacilitatorPaymentRequest.from_dict(
            {
                "x402Version": True,
                "paymentPayload": payload.to_dict(),
                "paymentRequirements": payload.accepted.to_dict(),
            }
        )
    with pytest.raises(ValidationError):
        FacilitatorPaymentRequest.from_dict({**request.to_dict(), "extra": {}})


def test_wire_model_repr_hides_urls_and_extension_data() -> None:
    requirements = PaymentRequirements(
        "exact",
        "eip155:8453",
        "1",
        "token",
        "recipient",
        60,
        extra={"accessToken": "secret-requirement"},
    )
    resource = ResourceInfo(
        "https://example.test/data?token=secret-url",
        description="resource",
    )
    challenge = PaymentRequired(
        resource,
        (requirements,),
        extensions={"proof": {"token": "secret-extension"}},
    )
    payload = PaymentPayload(
        requirements,
        {"signature": "secret-signature"},
        resource=resource,
        extensions={"token": "secret-payload-extension"},
    )
    rendered = repr((requirements, resource, challenge, payload))
    for secret in (
        "secret-requirement",
        "secret-url",
        "secret-extension",
        "secret-signature",
        "secret-payload-extension",
    ):
        assert secret not in rendered


def test_reserved_payment_flow_and_transfer_method_are_typed() -> None:
    requirements = PaymentRequirements(
        "exact",
        "eip155:8453",
        "12",
        "token",
        "recipient",
        60,
        extra={"assetTransferMethod": "permit2", "paymentFlow": "upfront"},
    )
    assert requirements.asset_transfer_method == "permit2"
    assert requirements.payment_flow == "upfront"
    with pytest.raises(ValidationError, match="assetTransferMethod"):
        PaymentRequirements(
            "exact", "eip155:8453", "1", "a", "b", 60, extra={"assetTransferMethod": None}
        )
    with pytest.raises(ValidationError, match="paymentFlow"):
        PaymentRequirements(
            "exact", "eip155:8453", "1", "a", "b", 60, extra={"paymentFlow": "later"}
        )


def test_requirements_expose_parsed_network_and_atomic_amount() -> None:
    requirements = PaymentRequirements.from_dict(REQ)
    assert requirements.amount_atomic == 10_000_000
    assert requirements.network_namespace == "stellar"
    assert requirements.network_reference == "testnet"


def test_success_responses_reject_failure_diagnostics() -> None:
    with pytest.raises(ValidationError, match="valid verify response"):
        VerifyResponse.from_dict({"isValid": True, "invalidReason": "invalid_payload"})
    with pytest.raises(ValidationError, match="valid verify response"):
        VerifyResponse.from_dict({"isValid": True, "invalidMessage": "bad"})
    with pytest.raises(ValidationError, match="successful settlement"):
        SettlementResponse.from_dict(
            {
                "success": True,
                "transaction": "hash",
                "network": "stellar:testnet",
                "errorReason": "settlement_pending",
            }
        )


def test_payment_required_filters_offers_without_choosing_a_rail() -> None:
    base = PaymentRequirements(
        "exact",
        "eip155:8453",
        "100",
        "token",
        "recipient",
        60,
        extra={"assetTransferMethod": "permit2", "paymentFlow": "authorization"},
    )
    other = PaymentRequirements(
        "exact",
        "eip155:8453",
        "100",
        "token",
        "recipient",
        60,
        extra={"assetTransferMethod": "eip3009", "paymentFlow": "authorization"},
    )
    different_network = PaymentRequirements(
        "exact",
        "eip155:84532",
        "100",
        "token",
        "recipient",
        60,
    )
    challenge = PaymentRequired(
        ResourceInfo("https://example.test"),
        (base, other, different_network),
    )
    assert challenge.offers_for("exact", "eip155:8453") == (base, other)
    assert challenge.offers_for(
        "exact",
        "eip155:8453",
        asset_transfer_method="permit2",
        payment_flow="authorization",
    ) == (base,)
    assert challenge.offers_for("exact", "eip155:8453", asset_transfer_method="unknown") == ()
    assert challenge.offers_for("exact", "eip155:8453", payment_flow="future") == ()  # type: ignore[arg-type]


def test_models_reject_unpaired_unicode_surrogates() -> None:
    requirements = PaymentRequirements("exact", "eip155:8453", "1", "asset", "payee", 60)
    with pytest.raises(ValidationError, match="Unicode"):
        PaymentPayload(requirements, {"signature": "\ud800"})
    with pytest.raises(ValidationError, match="Unicode"):
        PaymentRequirements(
            "exact",
            "eip155:8453",
            "1",
            "asset",
            "payee",
            60,
            extra={"\ud800": "value"},
        )
