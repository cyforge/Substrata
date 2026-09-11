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
