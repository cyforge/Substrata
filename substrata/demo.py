"""Run one signed payment over loopback HTTP without external accounts."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from uuid import uuid4
from wsgiref.simple_server import WSGIRequestHandler, make_server

from .audit import AuditLog
from .client import BuyerClient, ClientError, ClientResponse
from .constants import Network
from .models import Asset, PaymentStatus
from .policy import SpendPolicy
from .provider import PaidRoute, PaymentGate, WSGIMiddleware
from .storage import Ledger, ReplayStore
from .x402 import PaymentRequirements, create_local_demo_pair


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def run_local_demo(database: str | Path) -> ClientResponse:
    """Pay 0.1 demo XLM and retain the result in a local database.

    This function opens an ephemeral loopback port. Its HMAC proof and demo
    receipt do not represent a Stellar transaction or a transfer of value.
    """
    pair = create_local_demo_pair()

    def application(environ, start_response):
        if environ.get("REQUEST_METHOD") != "GET" or environ.get("PATH_INFO") != "/paid":
            start_response("404 Not Found", [("Content-Length", "0")])
            return [b""]
        body = json.dumps({"message": "paid content", "mode": "local-demo"}).encode()
        start_response(
            "200 OK",
            [("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
        )
        return [body]

    with Ledger(database) as ledger, AuditLog(database) as audit, ReplayStore(database) as replay:
        with make_server("127.0.0.1", 0, application, handler_class=_QuietHandler) as server:
            origin = f"http://127.0.0.1:{server.server_port}"
            url = f"{origin}/paid"
            requirements = PaymentRequirements(
                "substrata-demo", "stellar:testnet", "1000000", "XLM", "demo-merchant", 60
            )
            route = PaidRoute("/paid", "GET", requirements, url)
            gate = PaymentGate(pair.facilitator, replay, audit_log=audit)
            server.set_app(WSGIMiddleware(application, gate, [route], trusted_origin=origin))
            policy = SpendPolicy(
                allowed_networks=frozenset({Network.STELLAR_TESTNET}),
                allowed_assets=frozenset({Asset("XLM")}),
                allowed_recipients=frozenset({"demo-merchant"}),
                allowed_origins=frozenset({origin}),
                max_payment="0.1",
                daily_limit="1",
                total_limit="2",
            )
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with BuyerClient(policy, pair.signer, ledger=ledger, audit_log=audit) as buyer:
                    response = buyer.get(url, request_id=f"demo-{uuid4()}")
                if (
                    response.status_code != 200
                    or response.payment is None
                    or response.payment.status != PaymentStatus.SETTLED
                ):
                    raise ClientError("local demo payment did not settle; inspect the ledger")
                return response
            finally:
                server.shutdown()
                worker.join()
