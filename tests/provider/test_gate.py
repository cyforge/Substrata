from __future__ import annotations

from dataclasses import dataclass

from substrata.provider.gate import PaidRoute, PaymentGate
from substrata.storage import ReplayStore
from substrata.x402.demo import create_local_demo_pair
from substrata.x402.models import (
    PaymentPayload,
    PaymentRequirements,
    ResourceInfo,
    SettlementResponse,
    VerifyResponse,
)


@dataclass
class RouteFixture:
    route: PaidRoute
    payload: PaymentPayload
    facilitator: object


def fixture() -> RouteFixture:
    pair = create_local_demo_pair()
    requirements = PaymentRequirements(
        scheme="substrata-demo",
        network="stellar:testnet",
        amount="12",
        asset="XLM",
        pay_to="GTEST",
        max_timeout_seconds=60,
    )
    url = "http://127.0.0.1:8080/paid"
    route = PaidRoute("/paid", "GET", requirements, url)
    payload = pair.signer.sign(requirements, ResourceInfo(url=url))
    return RouteFixture(route, payload, pair.facilitator)


def test_gate_verifies_claims_and_settles_once(tmp_path) -> None:
    item = fixture()
    with ReplayStore(tmp_path / "replay.sqlite3") as replay:
        gate = PaymentGate(item.facilitator, replay)
        result = gate.process(item.route, item.payload)
        assert result.paid is True
        assert result.code == "paid"
        assert result.receipt is not None
        assert result.receipt.transaction.startswith("demo:")
        assert result.response_header
        assert replay.count_claims(scope="stellar:testnet") == 1
        assert gate.process(item.route, item.payload).code == "payment_invalid"


def test_gate_binds_current_offer_and_resource() -> None:
    item = fixture()
    payload = PaymentPayload(
        accepted=PaymentRequirements(
            scheme="substrata-demo",
            network="stellar:testnet",
            amount="13",
            asset="XLM",
            pay_to="GTEST",
            max_timeout_seconds=60,
        ),
        resource=item.payload.resource,
        payload=item.payload.payload,
    )
    gate = PaymentGate(item.facilitator, ReplayStore(":memory:"))
    assert gate.process(item.route, payload).code == "offer_mismatch"

    wrong_resource = PaymentPayload(
        accepted=item.payload.accepted,
        resource=ResourceInfo(url="http://127.0.0.1:8080/other"),
        payload=item.payload.payload,
    )
    assert gate.process(item.route, wrong_resource).code == "resource_mismatch"


def test_settlement_failure_keeps_claim_and_never_retries() -> None:
    item = fixture()

    class FailedSettlement:
        def __init__(self) -> None:
            self.settle_calls = 0

        def verify(self, payload, requirements):
            return VerifyResponse(True, payer="demo-payer")

        def settle(self, payload, requirements):
            self.settle_calls += 1
            raise TimeoutError("private facilitator detail")

    facilitator = FailedSettlement()
    replay = ReplayStore(":memory:")
    gate = PaymentGate(facilitator, replay)
    first = gate.process(item.route, item.payload)
    second = gate.process(item.route, item.payload)
    assert first.code == "settlement_uncertain"
    assert second.code == "payment_replayed"
    assert facilitator.settle_calls == 1


def test_invalid_receipt_never_returns_paid_result() -> None:
    item = fixture()

    class MismatchedReceipt:
        def verify(self, payload, requirements):
            return VerifyResponse(True, payer="payer-a")

        def settle(self, payload, requirements):
            return SettlementResponse(
                True,
                "demo:transaction",
                "stellar:testnet",
                payer="payer-b",
                amount="12",
            )

    result = PaymentGate(MismatchedReceipt(), ReplayStore(":memory:")).process(
        item.route, item.payload
    )
    assert result.code == "settlement_failed"
    assert result.paid is False
