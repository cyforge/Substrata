from __future__ import annotations

from dataclasses import dataclass

import pytest

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


def _stellar_payload(memo: bytes = b""):
    pytest.importorskip("stellar_sdk")
    from stellar_sdk import Address, Keypair, scval, xdr

    payer_key = Keypair.random()
    payer_address = Address.from_raw_account(payer_key.raw_public_key())
    payee_address = Address.from_raw_account(Keypair.random().raw_public_key())
    contract_address = Address.from_raw_contract(bytes(range(32)))
    call = xdr.InvokeContractArgs(
        contract_address=contract_address.to_xdr_sc_address(),
        function_name=xdr.SCSymbol(b"transfer"),
        args=[
            scval.to_address(payer_address),
            scval.to_address(payee_address),
            scval.to_int128(12),
        ],
    )
    auth_entry = xdr.SorobanAuthorizationEntry(
        credentials=xdr.SorobanCredentials(
            type=xdr.SorobanCredentialsType.SOROBAN_CREDENTIALS_ADDRESS,
            address=xdr.SorobanAddressCredentials(
                payer_address.to_xdr_sc_address(), xdr.Int64(9001), xdr.Uint32(500), scval.to_void()
            ),
        ),
        root_invocation=xdr.SorobanAuthorizedInvocation(
            xdr.SorobanAuthorizedFunction(
                xdr.SorobanAuthorizedFunctionType.SOROBAN_AUTHORIZED_FUNCTION_TYPE_CONTRACT_FN,
                contract_fn=call,
            ),
            [],
        ),
    )
    invoke_op = xdr.InvokeHostFunctionOp(
        xdr.HostFunction(
            xdr.HostFunctionType.HOST_FUNCTION_TYPE_INVOKE_CONTRACT,
            invoke_contract=call,
        ),
        [auth_entry],
    )
    transaction = xdr.Transaction(
        source_account=xdr.MuxedAccount(
            xdr.CryptoKeyType.KEY_TYPE_ED25519,
            ed25519=xdr.Uint256(payer_key.raw_public_key()),
        ),
        fee=xdr.Uint32(100),
        seq_num=xdr.SequenceNumber(xdr.Int64(1)),
        cond=xdr.Preconditions(xdr.PreconditionType.PRECOND_NONE),
        memo=(
            xdr.Memo(xdr.MemoType.MEMO_TEXT, text=memo)
            if memo
            else xdr.Memo(xdr.MemoType.MEMO_NONE)
        ),
        operations=[
            xdr.Operation(
                None,
                xdr.OperationBody(
                    xdr.OperationType.INVOKE_HOST_FUNCTION,
                    invoke_host_function_op=invoke_op,
                ),
            )
        ],
        ext=xdr.TransactionExt(0),
    )
    envelope = xdr.TransactionEnvelope(
        xdr.EnvelopeType.ENVELOPE_TYPE_TX,
        v1=xdr.TransactionV1Envelope(transaction, []),
    )
    encoded = envelope.to_xdr()
    requirements = PaymentRequirements(
        "exact",
        "stellar:testnet",
        "12",
        contract_address.address,
        payee_address.address,
        60,
        extra={"areFeesSponsored": True},
    )
    payload = PaymentPayload(
        accepted=requirements,
        resource=ResourceInfo(url="https://api.example.test/paid"),
        payload={"transaction": encoded},
    )
    return payload, requirements, payer_address.address


def test_stellar_identity_uses_signed_address_and_nonce_across_envelopes() -> None:
    from substrata.provider.gate import StellarProofIdentityExtractor

    payload, requirements, payer = _stellar_payload()
    changed_envelope, _, _ = _stellar_payload()
    # Reuse same auth entry while changing envelope memo; transaction hash changes.
    from stellar_sdk import xdr

    from substrata.x402.models import VerifyResponse

    envelope = xdr.TransactionEnvelope.from_xdr(payload.payload["transaction"])
    changed_tx = envelope.v1.tx
    changed_tx.memo = xdr.Memo(xdr.MemoType.MEMO_TEXT, text=b"different envelope")
    changed = xdr.TransactionEnvelope(
        xdr.EnvelopeType.ENVELOPE_TYPE_TX,
        v1=xdr.TransactionV1Envelope(changed_tx, []),
    )
    payload2 = PaymentPayload(
        accepted=requirements,
        resource=payload.resource,
        payload={"transaction": changed.to_xdr()},
    )
    verify = VerifyResponse(True, payer=payer)
    extractor = StellarProofIdentityExtractor()
    proof1 = extractor(payload, requirements, verify)
    proof2 = extractor(payload2, requirements, verify)
    assert proof1 == proof2
    assert proof1.startswith(f"stellar-exact:{payer}:")
    assert changed_envelope.payload["transaction"] != payload.payload["transaction"]


def test_replay_claims_global_across_routes_and_persistent_restart(tmp_path) -> None:
    item = fixture()
    route2 = PaidRoute(
        "/other",
        "GET",
        item.route.requirements,
        "http://127.0.0.1:8080/other",
    )
    payload2 = PaymentPayload(
        accepted=item.payload.accepted,
        resource=ResourceInfo(url=route2.resource_url),
        payload=item.payload.payload,
    )

    class FlexibleFacilitator:
        def verify(self, payload, requirements):
            return VerifyResponse(True, payer="demo-payer")

        def settle(self, payload, requirements):
            return SettlementResponse(
                True,
                "demo:" + "a" * 64,
                requirements.network,
                payer="demo-payer",
                amount=requirements.amount,
            )

    def identity(payload, requirements, verified):
        return f"signed-nonce:{payload.payload['nonce']}"

    path = tmp_path / "shared.sqlite3"
    with ReplayStore(path) as replay:
        gate = PaymentGate(FlexibleFacilitator(), replay, proof_identity=identity)
        assert gate.process(item.route, item.payload).paid
    with ReplayStore(path) as reopened:
        second = PaymentGate(FlexibleFacilitator(), reopened, proof_identity=identity)
        assert second.process(route2, payload2).code == "payment_replayed"
        assert reopened.count_claims(scope="stellar:testnet") == 1


def test_only_supported_stellar_and_local_demo_schemes_reach_facilitator() -> None:
    item = fixture()
    unsupported = PaymentRequirements("other-scheme", "stellar:testnet", "12", "XLM", "GTEST", 60)

    class CountingFacilitator:
        calls = 0

        def verify(self, payload, requirements):
            self.calls += 1
            return VerifyResponse(True)

        def settle(self, payload, requirements):
            self.calls += 1
            return SettlementResponse(True, "demo:" + "a" * 64, requirements.network)

    facilitator = CountingFacilitator()
    route = PaidRoute("/paid", "GET", unsupported, item.route.resource_url)
    payload = PaymentPayload(
        unsupported,
        item.payload.payload,
        resource=ResourceInfo(url=item.route.resource_url),
    )
    result = PaymentGate(facilitator, ReplayStore(":memory:")).process(route, payload)
    assert result.code == "unsupported_payment"
    assert facilitator.calls == 0


def test_configured_audit_failure_blocks_settlement_but_post_settle_failure_keeps_receipt() -> None:
    item = fixture()

    class AuditFailure:
        def __init__(self, fail_at: str):
            self.fail_at = fail_at
            self.calls = 0

        def __call__(self, event, data):
            self.calls += 1
            if event == self.fail_at:
                raise OSError("private audit path")

    class CountingFacilitator:
        def __init__(self):
            self.settle_calls = 0

        def verify(self, payload, requirements):
            return VerifyResponse(True, payer="demo-payer")

        def settle(self, payload, requirements):
            self.settle_calls += 1
            return SettlementResponse(
                True,
                "demo:" + "a" * 64,
                requirements.network,
                payer="demo-payer",
                amount=requirements.amount,
            )

    facilitator = CountingFacilitator()
    before = PaymentGate(
        facilitator,
        ReplayStore(":memory:"),
        proof_identity=lambda payload, requirements, verified: "nonce-a",
        audit_log=AuditFailure("verified"),
    ).process(item.route, item.payload)
    assert before.code == "audit_unavailable"
    assert facilitator.settle_calls == 0

    after = PaymentGate(
        facilitator,
        ReplayStore(":memory:"),
        proof_identity=lambda payload, requirements, verified: "nonce-b",
        audit_log=AuditFailure("settled"),
    ).process(item.route, item.payload)
    assert after.paid is True
    assert after.receipt is not None
    assert facilitator.settle_calls == 1


def test_route_challenge_and_gate_accept_all_configured_offers() -> None:
    item = fixture()
    alternate = PaymentRequirements(
        "substrata-demo",
        "stellar:testnet",
        "24",
        "XLM",
        "GTEST",
        60,
    )
    route = PaidRoute(
        item.route.path,
        item.route.method,
        item.route.requirements,
        item.route.resource_url,
        additional_requirements=(alternate,),
    )
    assert route.challenge.accepts == (item.route.requirements, alternate)
    # Use a facilitator and signer sharing a key for the second configured offer.
    from substrata.x402.demo import LocalDemoFacilitator, LocalDemoKey, LocalDemoSigner

    key = LocalDemoKey.generate()
    facilitator = LocalDemoFacilitator(key)
    signer = LocalDemoSigner(key)
    alternate_payload = signer.sign(alternate, ResourceInfo(url=route.resource_url))
    result = PaymentGate(facilitator, ReplayStore(":memory:")).process(route, alternate_payload)
    assert result.paid is True


def test_claim_store_failure_denies_without_settlement() -> None:
    item = fixture()

    class CountingFacilitator:
        settle_calls = 0

        def verify(self, payload, requirements):
            return VerifyResponse(True, payer="demo-payer")

        def settle(self, payload, requirements):
            self.settle_calls += 1
            return SettlementResponse(
                True,
                "demo:" + "a" * 64,
                requirements.network,
                payer="demo-payer",
                amount=requirements.amount,
            )

    replay = ReplayStore(":memory:")
    replay.close()
    facilitator = CountingFacilitator()
    result = PaymentGate(facilitator, replay).process(item.route, item.payload)
    assert result.code == "replay_store_unavailable"
    assert facilitator.settle_calls == 0


def test_concurrent_requests_settle_once() -> None:
    from concurrent.futures import ThreadPoolExecutor

    item = fixture()

    class CountingFacilitator:
        def __init__(self):
            self.settle_calls = 0

        def verify(self, payload, requirements):
            return VerifyResponse(True, payer="demo-payer")

        def settle(self, payload, requirements):
            self.settle_calls += 1
            return SettlementResponse(
                True,
                "demo:" + "a" * 64,
                requirements.network,
                payer="demo-payer",
                amount=requirements.amount,
            )

    replay = ReplayStore(":memory:")
    facilitator = CountingFacilitator()
    gate = PaymentGate(facilitator, replay)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: gate.process(item.route, item.payload), range(12)))
    assert sum(result.paid for result in results) == 1
    assert sum(result.code == "payment_replayed" for result in results) == 11
    assert facilitator.settle_calls == 1


def test_stellar_receipt_requires_context_network_hash_payer_and_amount() -> None:
    from substrata.provider.gate import StellarProofIdentityExtractor

    payload, requirement, payer = _stellar_payload()

    class ReceiptFacilitator:
        def __init__(self, receipt):
            self.receipt = receipt

        def verify(self, payload, requirements):
            return VerifyResponse(True, payer=payer)

        def settle(self, payload, requirements):
            return self.receipt

    replay = ReplayStore(":memory:")
    bad = PaymentGate(
        ReceiptFacilitator(
            SettlementResponse(
                True,
                "0x" + "a" * 64,
                requirement.network,
                payer=payer,
                amount=requirement.amount,
            )
        ),
        replay,
        proof_identity=StellarProofIdentityExtractor(),
    ).process(
        PaidRoute("/stellar", "POST", requirement, "https://api.example.test/stellar"),
        PaymentPayload(
            accepted=requirement,
            resource=ResourceInfo(url="https://api.example.test/stellar"),
            payload=payload.payload,
        ),
    )
    assert bad.code == "settlement_failed"
