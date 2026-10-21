"""Run a local WSGI endpoint protected by the explicit x402 demo scheme."""

from __future__ import annotations

import json
from wsgiref.simple_server import WSGIRequestHandler, make_server

from substrata.provider import PaidRoute, PaymentGate, WSGIMiddleware
from substrata.storage import ReplayStore
from substrata.x402.demo import create_local_demo_pair
from substrata.x402.models import PaymentRequirements

HOST = "127.0.0.1"
PORT = 8091
ORIGIN = f"http://{HOST}:{PORT}"


def application(environ, start_response):
    if environ.get("PATH_INFO") == "/paid" and environ.get("REQUEST_METHOD") != "GET":
        body = b'{"error":"method_not_allowed"}'
        start_response(
            "405 Method Not Allowed",
            [("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
        )
        return [body]
    if environ.get("PATH_INFO") == "/paid":
        body = json.dumps({"message": "paid content"}).encode("utf-8")
        start_response(
            "200 OK",
            [("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
        )
        return [body]
    body = b'{"error":"not_found"}'
    start_response(
        "404 Not Found",
        [("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
    )
    return [body]


def main() -> None:
    pair = create_local_demo_pair()
    requirements = PaymentRequirements(
        scheme="substrata-demo",
        network="stellar:testnet",
        amount="1000000",
        asset="XLM",
        pay_to="GLOCALDEMO",
        max_timeout_seconds=60,
    )
    route = PaidRoute(
        path="/paid",
        method="GET",
        requirements=requirements,
        resource_url=f"{ORIGIN}/paid",
    )
    replay = ReplayStore("substrata-provider-demo.sqlite3")
    gate = PaymentGate(pair.facilitator, replay)
    app = WSGIMiddleware(application, gate, [route], trusted_origin=ORIGIN)

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

    try:
        with make_server(HOST, PORT, app, handler_class=QuietHandler) as server:
            print(f"Local paid API listening at {ORIGIN}/paid")
            print("Demo scheme uses local HMAC signatures; it never submits Stellar transactions.")
            server.serve_forever()
    finally:
        replay.close()


if __name__ == "__main__":
    main()
