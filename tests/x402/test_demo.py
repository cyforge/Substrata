from concurrent.futures import ThreadPoolExecutor

import pytest

from substrata.errors import ValidationError
from substrata.x402.demo import (
    DEMO_NETWORK,
    DEMO_SCHEME,
    LocalDemoFacilitator,
    LocalDemoKey,
    LocalDemoSigner,
)
from substrata.x402.models import PaymentPayload, PaymentRequirements, ResourceInfo

REQUIREMENTS = PaymentRequirements(
    scheme=DEMO_SCHEME,
    network=DEMO_NETWORK,
    amount="25",
    asset="SUBSTRATA-DEMO",
    pay_to="demo-merchant",
    max_timeout_seconds=60,
)
RESOURCE = ResourceInfo(url="http://127.0.0.1:43123/data", description="Local fixture")


def test_local_demo_sign_verify_settle_and_replay_guard() -> None:
    key = LocalDemoKey.generate()
    signer = LocalDemoSigner(key)
    facilitator = LocalDemoFacilitator(key)
    payload = signer.sign(REQUIREMENTS, RESOURCE)

    assert facilitator.verify(payload, REQUIREMENTS).is_valid
    settled = facilitator.settle(payload, REQUIREMENTS)
    assert settled.success
    assert settled.transaction.startswith("demo:")
    assert settled.network == DEMO_NETWORK
    assert facilitator.verify(payload, REQUIREMENTS).invalid_reason == "replay_detected"
    duplicate = facilitator.settle(payload, REQUIREMENTS)
    assert not duplicate.success
    assert duplicate.error_reason == "replay_detected"


def test_demo_signer_advertises_offer_support_without_signing() -> None:
    signer = LocalDemoSigner(LocalDemoKey.generate())
    assert signer.supports(REQUIREMENTS)
    assert not signer.supports(object())  # type: ignore[arg-type]
    unsupported = PaymentRequirements(
        scheme=DEMO_SCHEME,
        network="stellar:pubnet",
        amount="25",
        asset="SUBSTRATA-DEMO",
        pay_to="demo-merchant",
        max_timeout_seconds=60,
    )
    assert not signer.supports(unsupported)


def test_demo_signature_binds_offer_and_resource() -> None:
    key = LocalDemoKey.generate()
    payload = LocalDemoSigner(key).sign(REQUIREMENTS, RESOURCE)
    other_offer = PaymentRequirements(
        scheme=DEMO_SCHEME,
        network=DEMO_NETWORK,
        amount="26",
        asset="SUBSTRATA-DEMO",
        pay_to="demo-merchant",
        max_timeout_seconds=60,
    )
    facilitator = LocalDemoFacilitator(key)
    assert facilitator.verify(payload, other_offer).invalid_reason == "invalid_payload"

    moved = PaymentPayload(
        accepted=payload.accepted,
        resource=ResourceInfo(url="http://localhost:43123/other"),
        payload=payload.payload,
    )
    assert facilitator.verify(moved, REQUIREMENTS).invalid_reason == "invalid_signature"

    forged_data = dict(payload.payload)
    forged_data["signature"] = "0" * 64
    forged = PaymentPayload(payload.accepted, forged_data, payload.resource)
    assert facilitator.verify(forged, REQUIREMENTS).invalid_reason == "invalid_signature"


def test_demo_rejects_non_loopback_resources_and_live_network_offers() -> None:
    signer = LocalDemoSigner(LocalDemoKey.generate())
    with pytest.raises(ValidationError, match="loopback"):
        signer.sign(REQUIREMENTS, ResourceInfo(url="https://api.example.com/data"))
    live_offer = PaymentRequirements(
        scheme="exact",
        network="stellar:pubnet",
        amount="25",
        asset="USDC",
        pay_to="GABC",
        max_timeout_seconds=60,
        extra={"areFeesSponsored": True},
    )
    with pytest.raises(ValidationError, match="only substrata-demo"):
        signer.sign(live_offer, RESOURCE)


def test_demo_key_and_payload_repr_hide_secret_material() -> None:
    key = LocalDemoKey(b"k" * 32)
    assert "kkkk" not in repr(key)
    assert "<redacted>" in repr(key)
    payload = LocalDemoSigner(key).sign(REQUIREMENTS, RESOURCE)
    assert "signature" not in repr(payload)
    assert key._secret not in repr(key).encode()


def test_demo_settlement_nonce_is_consumed_atomically() -> None:
    key = LocalDemoKey.generate()
    payload = LocalDemoSigner(key).sign(REQUIREMENTS, RESOURCE)
    facilitator = LocalDemoFacilitator(key)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: facilitator.settle(payload, REQUIREMENTS), range(8)))
    assert sum(result.success for result in results) == 1
    assert sum(result.error_reason == "replay_detected" for result in results) == 7


def test_demo_payload_rejects_unbound_fields() -> None:
    key = LocalDemoKey.generate()
    payload = LocalDemoSigner(key).sign(REQUIREMENTS, RESOURCE)
    altered = dict(payload.payload)
    altered["bypass"] = True
    assert (
        LocalDemoFacilitator(key)
        .verify(PaymentPayload(payload.accepted, altered, payload.resource), REQUIREMENTS)
        .invalid_reason
        == "invalid_payload"
    )


def test_demo_expiry_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    import substrata.x402.demo as demo

    monkeypatch.setattr(demo.time, "time", lambda: 1000)
    key = LocalDemoKey.generate()
    payload = LocalDemoSigner(key, ttl_seconds=2).sign(REQUIREMENTS, RESOURCE)
    monkeypatch.setattr(demo.time, "time", lambda: 1003)
    assert (
        LocalDemoFacilitator(key).verify(payload, REQUIREMENTS).invalid_reason
        == "authorization_expired"
    )


def test_pair_factory_shares_one_ephemeral_local_key() -> None:
    from substrata.x402.demo import create_local_demo_pair

    pair = create_local_demo_pair(ttl_seconds=5)
    payload = pair.signer.sign(REQUIREMENTS, RESOURCE)
    assert pair.facilitator.verify(payload, REQUIREMENTS).is_valid
    assert pair.facilitator.settle(payload, REQUIREMENTS).success
    assert "secret" not in repr(pair)


def test_demo_key_and_signed_message_sizes_are_bounded() -> None:
    with pytest.raises(ValidationError, match="32 to 4096 bytes"):
        LocalDemoKey(b"x" * 4097)
    key = LocalDemoKey.generate()
    oversized_resource = ResourceInfo(url=RESOURCE.url, description="x" * (1_048_576 + 1))
    with pytest.raises(ValidationError, match="size limit"):
        LocalDemoSigner(key).sign(REQUIREMENTS, oversized_resource)


def test_demo_facilitator_reports_offer_scheme_and_network_failures() -> None:
    key = LocalDemoKey.generate()
    facilitator = LocalDemoFacilitator(key)
    signer = LocalDemoSigner(key)
    wrong_network = PaymentRequirements(
        scheme=DEMO_SCHEME,
        network="stellar:pubnet",
        amount="25",
        asset="SUBSTRATA-DEMO",
        pay_to="demo-merchant",
        max_timeout_seconds=60,
    )
    network_result = facilitator.verify(
        signer.sign(REQUIREMENTS, RESOURCE),
        wrong_network,
    )
    assert network_result.invalid_reason == "invalid_network"
    network_settle = facilitator.settle(signer.sign(REQUIREMENTS, RESOURCE), wrong_network)
    assert network_settle.error_reason == "invalid_network"
    assert network_settle.network == "stellar:pubnet"

    wrong_scheme = PaymentRequirements(
        scheme="exact",
        network=DEMO_NETWORK,
        amount="25",
        asset="SUBSTRATA-DEMO",
        pay_to="demo-merchant",
        max_timeout_seconds=60,
        extra={"areFeesSponsored": True},
    )
    scheme_result = facilitator.verify(signer.sign(REQUIREMENTS, RESOURCE), wrong_scheme)
    assert scheme_result.invalid_reason == "invalid_scheme"
