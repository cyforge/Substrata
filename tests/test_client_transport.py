from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from substrata.client import BuyerClient, HTTPResponse, HTTPTransport
from substrata.constants import Network
from substrata.errors import ValidationError
from substrata.models import Asset
from substrata.policy import SpendPolicy
from substrata.storage import Ledger
from substrata.x402.demo import create_local_demo_pair


class RecordingRedirectHandler(BaseHTTPRequestHandler):
    paths: list[str] = []

    def do_GET(self):
        self.paths.append(self.path)
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "/target")
        else:
            self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format, *args):
        return


def test_stdlib_transport_does_not_follow_redirects():
    RecordingRedirectHandler.paths = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), RecordingRedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    pair = create_local_demo_pair()
    policy = SpendPolicy(
        allowed_networks=frozenset({Network.STELLAR_TESTNET}),
        allowed_recipients=frozenset({"merchant"}),
        allowed_assets=frozenset({Asset("XLM")}),
    )
    client = BuyerClient(
        policy,
        pair.signer,
        ledger=Ledger(":memory:"),
        transport=HTTPTransport(),
    )
    try:
        response = client.get(f"http://127.0.0.1:{server.server_port}/start")
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert response.status_code == 302
    assert RecordingRedirectHandler.paths == ["/start"]


def test_invalid_url_is_rejected_before_transport():
    class NoCalls:
        def __init__(self):
            self.calls = 0

        def send(self, method, url, headers, body, timeout, max_response_bytes):
            self.calls += 1
            return HTTPResponse(200, {}, b"")

    pair = create_local_demo_pair()
    transport = NoCalls()
    client = BuyerClient(
        SpendPolicy(),
        pair.signer,
        ledger=Ledger(":memory:"),
        transport=transport,
    )
    with pytest.raises(ValidationError, match="URL"):
        client.get("http://[bad-host")
    assert transport.calls == 0
