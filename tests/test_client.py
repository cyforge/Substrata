from __future__ import annotations

from pathlib import Path

import pytest

from substrata.client import BuyerClient, HTTPResponse
from substrata.constants import Network
from substrata.errors import ValidationError
from substrata.models import Asset, PaymentStatus
from substrata.policy import SpendPolicy
from substrata.storage import Ledger, LedgerStatus
from substrata.x402.codec import encode_payment_required_header, encode_payment_response_header
from substrata.x402.demo import create_local_demo_pair
from substrata.x402.models import (
    PaymentRequired,
    PaymentRequirements,
    ResourceInfo,
    SettlementResponse,
)

URL = "http://127.0.0.1:8020/weather?city=Accra"
RESOURCE = ResourceInfo(URL)
OFFER = PaymentRequirements(
    "substrata-demo",
    "stellar:testnet",
    "12500000",
    "XLM",
    "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF",
    45,
)
POLICY = SpendPolicy(
    allowed_networks=frozenset({Network.STELLAR_TESTNET}),
    allowed_recipients=frozenset({OFFER.pay_to}),
    allowed_assets=frozenset({Asset("XLM")}),
    total_limit="20",
    daily_limit="20",
    allowed_origins=frozenset({"http://127.0.0.1:8020"}),
)


class QueueTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def send(self, method, url, headers, body, timeout, max_response_bytes):
        self.calls.append((method, url, dict(headers), body))
        return self.responses.pop(0)


def challenge(offer=OFFER, url=URL):
    return PaymentRequired(resource=ResourceInfo(url), accepts=(offer,))


def test_get_pays_once_and_commits_valid_demo_receipt(tmp_path: Path):
    pair = create_local_demo_pair()
    signed = pair.signer.sign(OFFER, RESOURCE)
    receipt = pair.facilitator.settle(signed, OFFER)
    transport = QueueTransport(
        [
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(challenge())}, b"pay"
            ),
            HTTPResponse(200, {"payment-response": encode_payment_response_header(receipt)}, b"ok"),
        ]
    )
    ledger = Ledger(str(tmp_path / "ledger.sqlite"))
    client = BuyerClient(POLICY, pair.signer, ledger=ledger, transport=transport)
    result = client.get(URL, request_id="get-once")
    assert result.status_code == 200
    assert result.payment is not None and result.payment.status is PaymentStatus.SETTLED
    assert len(transport.calls) == 2
    assert "payment-signature" in {key.lower() for key in transport.calls[1][2]}
    assert ledger.get("get-once").status is LedgerStatus.SETTLED
    client.close()
    assert ledger.get("get-once").status is LedgerStatus.SETTLED


def test_missing_receipt_keeps_budget_uncertain():
    pair = create_local_demo_pair()
    transport = QueueTransport(
        [
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(challenge())}, b"pay"
            ),
            HTTPResponse(200, {}, b"ok"),
        ]
    )
    ledger = Ledger(":memory:")
    client = BuyerClient(POLICY, pair.signer, ledger=ledger, transport=transport)
    result = client.get(URL, request_id="missing-receipt")
    assert result.payment is not None and result.payment.status is PaymentStatus.UNCERTAIN
    assert ledger.get("missing-receipt").status is LedgerStatus.UNCERTAIN
    assert ledger.spent("stellar_testnet:XLM") > 0


def test_resource_mismatch_stops_before_signing_or_reservation():
    pair = create_local_demo_pair()
    transport = QueueTransport(
        [
            HTTPResponse(
                402,
                {
                    "payment-required": encode_payment_required_header(
                        challenge(url="http://127.0.0.1:8020/other")
                    )
                },
                b"pay",
            ),
        ]
    )
    ledger = Ledger(":memory:")
    client = BuyerClient(POLICY, pair.signer, ledger=ledger, transport=transport)
    with pytest.raises(ValidationError, match="exactly match"):
        client.get(URL, request_id="resource-mismatch")
    assert ledger.get("resource-mismatch") is None
    assert len(transport.calls) == 1


def test_reused_request_id_never_retries_existing_reservation():
    pair = create_local_demo_pair()
    transport = QueueTransport(
        [
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(challenge())}, b"pay"
            ),
            HTTPResponse(200, {}, b"ok"),
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(challenge())}, b"pay"
            ),
        ]
    )
    ledger = Ledger(":memory:")
    client = BuyerClient(POLICY, pair.signer, ledger=ledger, transport=transport)
    client.get(URL, request_id="reuse")
    with pytest.raises(ValidationError, match="will not be resent"):
        client.get(URL, request_id="reuse")
    assert len(transport.calls) == 3


def test_ephemeral_budget_requires_explicit_opt_in():
    pair = create_local_demo_pair()
    with pytest.raises(ValidationError, match="ephemeral ledger"):
        BuyerClient(POLICY, pair.signer)
    client = BuyerClient(POLICY, pair.signer, allow_ephemeral_ledger=True)
    client.close()


def test_policy_skips_first_supported_offer_and_selects_later_allowed_offer():
    pair = create_local_demo_pair()
    expensive = PaymentRequirements(
        "substrata-demo", "stellar:testnet", "30000000", "XLM", OFFER.pay_to, 45
    )
    affordable = PaymentRequirements(
        "substrata-demo", "stellar:testnet", "15000000", "XLM", OFFER.pay_to, 45
    )
    paid_payload = pair.signer.sign(affordable, RESOURCE)
    receipt = pair.facilitator.settle(paid_payload, affordable)
    multi = PaymentRequired(resource=RESOURCE, accepts=(expensive, affordable))
    transport = QueueTransport(
        [
            HTTPResponse(402, {"payment-required": encode_payment_required_header(multi)}, b"pay"),
            HTTPResponse(200, {"payment-response": encode_payment_response_header(receipt)}, b"ok"),
        ]
    )
    policy = SpendPolicy(
        allowed_networks=POLICY.allowed_networks,
        allowed_recipients=POLICY.allowed_recipients,
        allowed_assets=POLICY.allowed_assets,
        max_payment="2",
        total_limit="5",
        allowed_origins=POLICY.allowed_origins,
    )
    client = BuyerClient(policy, pair.signer, ledger=Ledger(":memory:"), transport=transport)
    result = client.get(URL, request_id="later-offer")
    assert result.payment is not None and result.payment.status is PaymentStatus.SETTLED
    payload = decode_signature(transport.calls[1][2]["PAYMENT-SIGNATURE"])
    assert payload.accepted == affordable


def test_post_requires_explicit_replay_permission():
    pair = create_local_demo_pair()
    transport = QueueTransport(
        [
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(challenge())}, b"pay"
            ),
        ]
    )
    client = BuyerClient(POLICY, pair.signer, ledger=Ledger(":memory:"), transport=transport)
    result = client.post(URL, b'{"x":1}', request_id="post-denied")
    assert result.status_code == 402 and result.payment is None
    assert len(transport.calls) == 1


def test_caller_cannot_preload_payment_header():
    pair = create_local_demo_pair()
    transport = QueueTransport([])
    client = BuyerClient(POLICY, pair.signer, ledger=Ledger(":memory:"), transport=transport)
    with pytest.raises(ValidationError, match="cannot be supplied"):
        client.get(URL, headers={"payment-signature": "stolen"})
    assert transport.calls == []


def test_paid_retry_exception_marks_uncertain_without_leaking_error_text():
    pair = create_local_demo_pair()

    class BrokenTransport(QueueTransport):
        def send(self, method, url, headers, body, timeout, max_response_bytes):
            self.calls.append((method, url, dict(headers), body))
            if len(self.calls) == 1:
                return HTTPResponse(
                    402,
                    {"payment-required": encode_payment_required_header(challenge())},
                    b"pay",
                )
            raise RuntimeError("private seed leaked")

    ledger = Ledger(":memory:")
    client = BuyerClient(POLICY, pair.signer, ledger=ledger, transport=BrokenTransport([]))
    with pytest.raises(Exception, match="paid retry outcome is unknown") as error:
        client.get(URL, request_id="transport-unknown")
    assert "private seed" not in str(error.value)
    assert ledger.get("transport-unknown").status is LedgerStatus.UNCERTAIN


def decode_signature(value: str):
    from substrata.x402.codec import decode_payment_signature_header

    return decode_payment_signature_header(value)


def test_valid_receipt_records_payment_even_when_resource_returns_server_error():
    pair = create_local_demo_pair()
    payload = pair.signer.sign(OFFER, RESOURCE)
    receipt = pair.facilitator.settle(payload, OFFER)
    transport = QueueTransport(
        [
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(challenge())}, b"pay"
            ),
            HTTPResponse(
                500, {"payment-response": encode_payment_response_header(receipt)}, b"error"
            ),
        ]
    )
    ledger = Ledger(":memory:")
    client = BuyerClient(POLICY, pair.signer, ledger=ledger, transport=transport)
    result = client.get(URL, request_id="settled-upfront")
    assert result.status_code == 500
    assert result.payment is not None and result.payment.status is PaymentStatus.SETTLED
    assert ledger.get("settled-upfront").status is LedgerStatus.SETTLED


def test_paid_preparation_audit_failure_releases_reservation_before_retry(tmp_path: Path):
    from substrata.audit import AuditLog
    from substrata.client import ClientError

    class BrokenAudit(AuditLog):
        def append(self, event_type, *, request_id=None, data=None):
            raise RuntimeError("audit write contains secret key")

    pair = create_local_demo_pair()
    audit = BrokenAudit(str(tmp_path / "audit.sqlite"))
    ledger = Ledger(":memory:")
    transport = QueueTransport(
        [
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(challenge())}, b"pay"
            ),
        ]
    )
    client = BuyerClient(POLICY, pair.signer, ledger=ledger, audit_log=audit, transport=transport)
    with pytest.raises(ClientError, match="payment preparation failed") as error:
        client.get(URL, request_id="audit-stops-send")
    assert "secret key" not in str(error.value)
    assert ledger.get("audit-stops-send").status is LedgerStatus.FAILED
    assert len(transport.calls) == 1


def test_paid_response_with_wrong_demo_payer_is_uncertain():
    pair = create_local_demo_pair()
    signed = pair.signer.sign(OFFER, RESOURCE)
    valid = pair.facilitator.settle(signed, OFFER)
    wrong_payer = SettlementResponse(
        True, valid.transaction, valid.network, payer="someone-else", amount=valid.amount
    )
    transport = QueueTransport(
        [
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(challenge())}, b"pay"
            ),
            HTTPResponse(
                200, {"payment-response": encode_payment_response_header(wrong_payer)}, b"ok"
            ),
        ]
    )
    ledger = Ledger(":memory:")
    client = BuyerClient(POLICY, pair.signer, ledger=ledger, transport=transport)
    result = client.get(URL, request_id="wrong-payer")
    assert result.payment is not None and result.payment.status is PaymentStatus.UNCERTAIN
    assert ledger.get("wrong-payer").status is LedgerStatus.UNCERTAIN


def test_client_rejects_blank_ledger_path_and_invalid_ledger():
    pair = create_local_demo_pair()
    with pytest.raises(ValidationError, match="ledger_path"):
        BuyerClient(POLICY, pair.signer, ledger_path=" ")
    with pytest.raises(ValidationError, match="ledger must be"):
        BuyerClient(POLICY, pair.signer, ledger=object())


def test_explicit_post_retry_resends_identical_body_once():
    pair = create_local_demo_pair()
    body = b'{"lookup":"stable"}'
    payload = pair.signer.sign(OFFER, RESOURCE)
    receipt = pair.facilitator.settle(payload, OFFER)
    transport = QueueTransport(
        [
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(challenge())}, b"pay"
            ),
            HTTPResponse(200, {"payment-response": encode_payment_response_header(receipt)}, b"ok"),
        ]
    )
    client = BuyerClient(POLICY, pair.signer, ledger=Ledger(":memory:"), transport=transport)
    result = client.post(URL, body, request_id="post-repeatable", replayable=True)
    assert result.payment is not None and result.payment.status is PaymentStatus.SETTLED
    assert [call[3] for call in transport.calls] == [body, body]


def test_remote_http_offer_is_rejected_before_reservation():
    pair = create_local_demo_pair()
    remote_url = "http://api.example.test/data"
    remote_challenge = challenge(url=remote_url)
    transport = QueueTransport(
        [
            HTTPResponse(
                402,
                {"payment-required": encode_payment_required_header(remote_challenge)},
                b"pay",
            ),
        ]
    )
    ledger = Ledger(":memory:")
    client = BuyerClient(POLICY, pair.signer, ledger=ledger, transport=transport)
    with pytest.raises(ValidationError, match="require HTTPS"):
        client.get(remote_url, request_id="cleartext-payment")
    assert ledger.get("cleartext-payment") is None


def test_injected_transport_response_rejects_duplicate_payment_receipt_headers():
    from substrata.client import ClientError

    pair = create_local_demo_pair()
    transport = QueueTransport(
        [
            HTTPResponse(
                200,
                {"payment-response": "first", "PAYMENT-RESPONSE": "second"},
                b"body",
            ),
        ]
    )
    client = BuyerClient(POLICY, pair.signer, ledger=Ledger(":memory:"), transport=transport)
    with pytest.raises(ClientError, match="duplicate PAYMENT-RESPONSE"):
        client.get(URL)


def test_request_body_limit_stops_before_transport():
    pair = create_local_demo_pair()
    transport = QueueTransport([])
    client = BuyerClient(
        POLICY,
        pair.signer,
        ledger=Ledger(":memory:"),
        transport=transport,
        max_request_body_bytes=2,
    )
    with pytest.raises(ValidationError, match="body exceeds"):
        client.post(URL, b"too big", replayable=True)
    assert transport.calls == []


def test_audit_records_only_safe_payment_lifecycle_fields(tmp_path: Path):
    from substrata.audit import AuditLog

    pair = create_local_demo_pair()
    payload = pair.signer.sign(OFFER, RESOURCE)
    receipt = pair.facilitator.settle(payload, OFFER)
    audit = AuditLog(str(tmp_path / "events.sqlite"))
    transport = QueueTransport(
        [
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(challenge())}, b"pay"
            ),
            HTTPResponse(200, {"payment-response": encode_payment_response_header(receipt)}, b"ok"),
        ]
    )
    client = BuyerClient(
        POLICY, pair.signer, ledger=Ledger(":memory:"), audit_log=audit, transport=transport
    )
    client.get(URL, request_id="audit-safe")
    events = audit.list_events(request_id="audit-safe")
    assert [event["event_type"] for event in events] == [
        "payment_reserved",
        "authorization_ready",
        "payment_settled",
    ]
    exported = audit.export_jsonl()
    assert "signature" not in exported.lower()
    assert "nonce" not in exported.lower()
    audit.close()


def test_audit_failure_after_paid_response_keeps_reservation_uncertain(tmp_path: Path):
    from substrata.audit import AuditLog
    from substrata.client import ClientError

    class SettlementAudit(AuditLog):
        def append(self, event_type, *, request_id=None, data=None):
            if event_type == "payment_settled":
                raise RuntimeError("signed proof content")
            return super().append(event_type, request_id=request_id, data=data)

    pair = create_local_demo_pair()
    payload = pair.signer.sign(OFFER, RESOURCE)
    receipt = pair.facilitator.settle(payload, OFFER)
    audit = SettlementAudit(str(tmp_path / "settlement.sqlite"))
    ledger = Ledger(":memory:")
    transport = QueueTransport(
        [
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(challenge())}, b"pay"
            ),
            HTTPResponse(200, {"payment-response": encode_payment_response_header(receipt)}, b"ok"),
        ]
    )
    client = BuyerClient(POLICY, pair.signer, ledger=ledger, audit_log=audit, transport=transport)
    with pytest.raises(ClientError, match="audit write failed") as error:
        client.get(URL, request_id="audit-after-paid")
    assert "proof content" not in str(error.value)
    assert ledger.get("audit-after-paid").status is LedgerStatus.UNCERTAIN
    assert ledger.spent("stellar_testnet:XLM") > 0


def test_query_credentials_are_not_saved_in_ledger_or_audit(tmp_path: Path):
    from substrata.audit import AuditLog

    url = "http://127.0.0.1:8020/weather?api_key=private-query-value"
    resource = ResourceInfo(url)
    pair = create_local_demo_pair()
    signed = pair.signer.sign(OFFER, resource)
    receipt = pair.facilitator.settle(signed, OFFER)
    audit = AuditLog(str(tmp_path / "query-audit.sqlite"))
    ledger = Ledger(str(tmp_path / "query-ledger.sqlite"))
    paid_challenge = PaymentRequired(resource=resource, accepts=(OFFER,))
    transport = QueueTransport(
        [
            HTTPResponse(
                402,
                {"payment-required": encode_payment_required_header(paid_challenge)},
                b"pay",
            ),
            HTTPResponse(200, {"payment-response": encode_payment_response_header(receipt)}, b"ok"),
        ]
    )
    client = BuyerClient(POLICY, pair.signer, ledger=ledger, audit_log=audit, transport=transport)
    client.get(url, request_id="query-is-private")
    assert "private-query-value" not in str(ledger.get("query-is-private").metadata)
    assert "private-query-value" not in audit.export_jsonl()
    assert "api_key=" not in audit.export_jsonl()


def test_async_client_runs_sync_transport_in_worker_thread():
    import asyncio
    import threading
    import time

    from substrata.client import AsyncBuyerClient

    class SlowTransport(QueueTransport):
        def send(self, method, url, headers, body, timeout, max_response_bytes):
            time.sleep(0.05)
            return super().send(method, url, headers, body, timeout, max_response_bytes)

    async def exercise():
        pair = create_local_demo_pair()
        transport = SlowTransport([HTTPResponse(200, {}, b"ok")])
        sync_client = BuyerClient(
            POLICY, pair.signer, allow_ephemeral_ledger=True, transport=transport
        )
        async_client = AsyncBuyerClient(sync_client)
        on_loop = threading.get_ident()
        ticked = False

        async def tick():
            nonlocal ticked
            await asyncio.sleep(0)
            ticked = True

        result, _ = await asyncio.gather(async_client.get(URL), tick())
        assert ticked
        assert result.body == b"ok"
        assert threading.get_ident() == on_loop
        await async_client.close()
        assert sync_client._closed

    asyncio.run(exercise())


def test_file_ledger_keeps_budget_across_client_recreation(tmp_path: Path):
    pair = create_local_demo_pair()
    path = tmp_path / "persistent-buyer.sqlite"
    exact_budget = SpendPolicy(
        allowed_networks=POLICY.allowed_networks,
        allowed_recipients=POLICY.allowed_recipients,
        allowed_assets=POLICY.allowed_assets,
        total_limit="1.25",
        allowed_origins=POLICY.allowed_origins,
    )
    payload = pair.signer.sign(OFFER, RESOURCE)
    receipt = pair.facilitator.settle(payload, OFFER)
    first_transport = QueueTransport(
        [
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(challenge())}, b"pay"
            ),
            HTTPResponse(200, {"payment-response": encode_payment_response_header(receipt)}, b"ok"),
        ]
    )
    first = BuyerClient(
        exact_budget,
        pair.signer,
        ledger_path=str(path),
        transport=first_transport,
    )
    first_result = first.get(URL, request_id="persistent-first")
    assert first_result.payment is not None
    first.close()

    second_transport = QueueTransport(
        [
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(challenge())}, b"pay"
            ),
        ]
    )
    second = BuyerClient(
        exact_budget,
        pair.signer,
        ledger_path=str(path),
        transport=second_transport,
    )
    second_result = second.get(URL, request_id="persistent-second")
    second.close()

    persisted = Ledger(str(path))
    try:
        assert second_result.status_code == 402 and second_result.payment is None
        assert len(second_transport.calls) == 1
        assert persisted.get("persistent-first").status is LedgerStatus.SETTLED
        assert persisted.get("persistent-second") is None
    finally:
        persisted.close()


def test_challenge_with_too_many_offers_is_rejected_before_reservation():
    pair = create_local_demo_pair()
    many_offers = PaymentRequired(resource=RESOURCE, accepts=(OFFER,) * 33)
    transport = QueueTransport(
        [
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(many_offers)}, b"pay"
            ),
        ]
    )
    ledger = Ledger(":memory:")
    client = BuyerClient(POLICY, pair.signer, ledger=ledger, transport=transport)
    with pytest.raises(ValidationError, match="too many offers"):
        client.get(URL, request_id="too-many-offers")
    assert ledger.get("too-many-offers") is None


def test_invalid_caller_headers_are_rejected_before_transport():
    pair = create_local_demo_pair()
    transport = QueueTransport([])
    client = BuyerClient(POLICY, pair.signer, ledger=Ledger(":memory:"), transport=transport)
    with pytest.raises(ValidationError, match="invalid HTTP header"):
        client.get(URL, headers={"X-Debug": "private\x00value"})
    with pytest.raises(ValidationError, match="too many headers"):
        client.get(URL, headers={f"X-{index}": "v" for index in range(101)})
    assert transport.calls == []


def test_stellar_receipt_payer_must_match_signer_identity():
    import base64

    from substrata.x402.models import PaymentPayload

    class ExactSigner:
        address = "Gexpected-payer"

        def supports(self, requirements):
            return requirements.scheme == "exact" and requirements.network == "stellar:testnet"

        def sign(self, requirements, resource):
            return PaymentPayload(
                accepted=requirements,
                resource=resource,
                payload={"transaction": base64.b64encode(b"signed-auth-xdr").decode("ascii")},
            )

    url = "https://api.example.test/data"
    resource = ResourceInfo(url)
    offer = PaymentRequirements(
        "exact",
        "stellar:testnet",
        "10000000",
        "Ctoken-contract",
        "Gmerchant",
        45,
        {"areFeesSponsored": True},
    )
    payment_required = PaymentRequired(resource=resource, accepts=(offer,))
    receipt = SettlementResponse(
        True,
        "a" * 64,
        "stellar:testnet",
        payer="Gdifferent-payer",
        amount="10000000",
    )
    policy = SpendPolicy(
        allowed_networks=frozenset({Network.STELLAR_TESTNET}),
        allowed_recipients=frozenset({"Gmerchant"}),
        allowed_assets=frozenset({Asset.soroban("Ctoken-contract")}),
        total_limit="2",
        allowed_origins=frozenset({"https://api.example.test"}),
    )
    ledger = Ledger(":memory:")
    transport = QueueTransport(
        [
            HTTPResponse(
                402, {"payment-required": encode_payment_required_header(payment_required)}, b"pay"
            ),
            HTTPResponse(200, {"payment-response": encode_payment_response_header(receipt)}, b"ok"),
        ]
    )
    client = BuyerClient(policy, ExactSigner(), ledger=ledger, transport=transport)
    result = client.get(url, request_id="wrong-stellar-payer")
    assert result.payment is not None and result.payment.status is PaymentStatus.UNCERTAIN
    assert ledger.get("wrong-stellar-payer").status is LedgerStatus.UNCERTAIN
    assert ledger.spent("stellar_testnet:contract:Ctoken-contract") > 0
