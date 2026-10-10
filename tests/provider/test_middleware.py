from __future__ import annotations

import asyncio

from substrata.errors import ValidationError
from substrata.provider.gate import PaidRoute, PaymentGate
from substrata.provider.middleware import ASGIMiddleware, WSGIMiddleware
from substrata.storage import ReplayStore
from substrata.x402.codec import encode_header
from substrata.x402.demo import create_local_demo_pair
from substrata.x402.models import PaymentRequirements, ResourceInfo


def route_fixture():
    pair = create_local_demo_pair()
    requirements = PaymentRequirements("substrata-demo", "stellar:testnet", "7", "XLM", "GTEST", 60)
    url = "http://127.0.0.1:8091/paid"
    route = PaidRoute("/paid", "GET", requirements, url)
    payload = pair.signer.sign(requirements, ResourceInfo(url=url))
    return pair, route, payload, PaymentGate(pair.facilitator, ReplayStore(":memory:"))


def test_wsgi_challenge_and_free_routes() -> None:
    _, route, _, gate = route_fixture()
    calls: list[str] = []

    def app(environ, start_response):
        calls.append(environ["PATH_INFO"])
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"free"]

    wrapped = WSGIMiddleware(app, gate, [route], trusted_origin="http://127.0.0.1:8091")
    started: list[tuple[str, list[tuple[str, str]]]] = []

    def start_response(status, headers, exc_info=None):
        started.append((status, headers))
        return lambda body: None

    challenge = list(wrapped({"REQUEST_METHOD": "GET", "PATH_INFO": "/paid"}, start_response))
    assert started[-1][0].startswith("402")
    assert any(name == "PAYMENT-REQUIRED" for name, _ in started[-1][1])
    assert challenge and calls == []

    free = list(wrapped({"REQUEST_METHOD": "GET", "PATH_INFO": "/public"}, start_response))
    assert free == [b"free"]
    assert calls == ["/public"]

    options = list(wrapped({"REQUEST_METHOD": "OPTIONS", "PATH_INFO": "/paid"}, start_response))
    assert options == [b"free"]
    assert calls[-1] == "/paid"


def test_wsgi_paid_response_adds_receipt_no_store_and_closes_iterator() -> None:
    _, route, payload, gate = route_fixture()
    closed = False

    class Body:
        def __iter__(self):
            yield b"protected"

        def close(self):
            nonlocal closed
            closed = True

    def app(environ, start_response):
        start_response(
            "200 OK",
            [
                ("Content-Type", "text/plain"),
                ("Cache-Control", "public"),
                ("Vary", "Accept-Encoding"),
            ],
        )
        return Body()

    wrapped = WSGIMiddleware(app, gate, [route], trusted_origin="http://127.0.0.1:8091")
    started: list[tuple[str, list[tuple[str, str]]]] = []

    def start_response(status, headers, exc_info=None):
        started.append((status, headers))
        return lambda body: None

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/paid",
        "HTTP_PAYMENT_SIGNATURE": __import__(
            "substrata.x402.codec", fromlist=["encode_header"]
        ).encode_header(payload),
    }
    body = wrapped(environ, start_response)
    assert list(body) == [b"protected"]
    assert any(name == "PAYMENT-RESPONSE" for name, _ in started[-1][1])
    assert ("Cache-Control", "private, no-store") in started[-1][1]
    assert ("Vary", "Accept-Encoding, PAYMENT-SIGNATURE") in started[-1][1]
    body.close()
    assert closed


def test_wsgi_signed_query_mismatch_never_runs_paid_handler() -> None:
    _, route, _, gate = route_fixture()
    calls = 0

    def app(environ, start_response):
        nonlocal calls
        calls += 1
        start_response("200 OK", [])
        return [b"content"]

    wrapped = WSGIMiddleware(app, gate, [route], trusted_origin="http://127.0.0.1:8091")
    status: list[str] = []
    response = list(
        wrapped(
            {"REQUEST_METHOD": "GET", "PATH_INFO": "/paid", "QUERY_STRING": "secret=true"},
            lambda value, headers, exc_info=None: status.append(value) or (lambda data: None),
        )
    )
    assert status[0].startswith("400")
    assert calls == 0
    assert b"resource_mismatch" in response[0]


def test_asgi_duplicate_header_lifespan_and_paid_execution_off_event_loop() -> None:
    pair, route, payload, gate = route_fixture()
    sync_calls = 0

    class SlowFacilitator:
        def verify(self, payload, requirements):
            nonlocal sync_calls
            import time

            sync_calls += 1
            time.sleep(0.04)
            return pair.facilitator.verify(payload, requirements)

        def settle(self, payload, requirements):
            return pair.facilitator.settle(payload, requirements)

    gate = PaymentGate(SlowFacilitator(), gate.replay_store)
    events: list[dict[str, object]] = []

    async def app(scope, receive, send):
        events.append({"type": "app", "scope": scope})
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"vary", b"Accept-Encoding")],
            }
        )
        await send({"type": "http.response.body", "body": b"content", "more_body": False})

    wrapped = ASGIMiddleware(app, gate, [route], trusted_origin="http://127.0.0.1:8091")

    async def run(scope):
        sent: list[dict[str, object]] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await wrapped(scope, receive, send)
        return sent

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/paid",
        "root_path": "/mounted",
        "query_string": b"",
        "headers": [
            (
                b"payment-signature",
                encode_header(payload).encode(),
            ),
            (
                b"payment-signature",
                encode_header(payload).encode(),
            ),
        ],
    }
    duplicate = asyncio.run(run(scope))
    assert duplicate[0]["status"] == 400
    assert sync_calls == 0

    scope["headers"] = [
        (
            b"payment-signature",
            encode_header(payload).encode(),
        )
    ]

    async def run_with_ticker():
        ticking = True
        ticks = 0

        async def ticker():
            nonlocal ticks
            while ticking:
                ticks += 1
                await asyncio.sleep(0.001)

        task = asyncio.create_task(ticker())
        sent_messages = await run(scope)
        ticking = False
        await task
        return sent_messages, ticks

    sent, ticks = asyncio.run(run_with_ticker())
    assert sent[0]["status"] == 200
    assert ticks >= 10
    response_headers = sent[0]["headers"]
    assert (b"Cache-Control", b"private, no-store") in response_headers
    assert (b"Vary", b"Accept-Encoding, PAYMENT-SIGNATURE") in response_headers
    assert any(name == b"PAYMENT-RESPONSE" for name, _ in response_headers)
    assert sync_calls == 1

    lifespan_scope = {"type": "lifespan"}
    assert len(asyncio.run(run(lifespan_scope))) == 2
    assert len(events) == 2


def test_asgi_query_and_bad_payload_fail_closed() -> None:
    _, route, payload, gate = route_fixture()
    calls = 0

    async def app(scope, receive, send):
        nonlocal calls
        calls += 1

    wrapped = ASGIMiddleware(app, gate, [route], trusted_origin="http://127.0.0.1:8091")

    async def invoke(query: bytes, payment: bytes):
        sent: list[dict[str, object]] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await wrapped(
            {
                "type": "http",
                "method": "GET",
                "path": "/paid",
                "query_string": query,
                "headers": [(b"payment-signature", payment)],
            },
            receive,
            send,
        )
        return sent

    assert asyncio.run(invoke(b"x=1", encode_header(payload).encode()))[0]["status"] == 400
    assert asyncio.run(invoke(b"", b"bad"))[0]["status"] == 400
    assert calls == 0


def test_local_http_challenge_payment_and_receipt_round_trip() -> None:
    import threading
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    from substrata.x402.codec import decode_header
    from substrata.x402.models import PaymentRequired, SettlementResponse

    pair = create_local_demo_pair()

    def app(environ, start_response):
        body = b'{"message":"paid over HTTP"}'
        start_response("200 OK", [("Content-Type", "application/json")])
        return [body]

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

    def placeholder(environ, start_response):
        start_response("500 Internal Server Error", [])
        return [b""]

    server = make_server("127.0.0.1", 0, placeholder, handler_class=QuietHandler)
    origin = f"http://127.0.0.1:{server.server_port}"
    requirement = PaymentRequirements("substrata-demo", "stellar:testnet", "9", "XLM", "GTEST", 60)
    route = PaidRoute("/paid", "GET", requirement, f"{origin}/paid")
    replay = ReplayStore(":memory:")
    server.set_app(
        WSGIMiddleware(app, PaymentGate(pair.facilitator, replay), [route], trusted_origin=origin)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        try:
            urlopen(Request(f"{origin}/paid"), timeout=2)
        except HTTPError as response:
            assert response.code == 402
            challenge_header = response.headers["PAYMENT-REQUIRED"]
        else:
            raise AssertionError("paid route returned content without payment")
        challenge = decode_header(challenge_header, PaymentRequired)
        offer = challenge.accepts[0]
        payload = pair.signer.sign(offer, challenge.resource)
        request = Request(
            f"{origin}/paid",
            headers={
                "PAYMENT-SIGNATURE": encode_header(payload),
                "Host": "attacker.example",
            },
        )
        with urlopen(request, timeout=2) as response:
            assert response.status == 200
            assert response.read() == b'{"message":"paid over HTTP"}'
            receipt = decode_header(response.headers["PAYMENT-RESPONSE"], SettlementResponse)
            assert receipt.success is True
            assert receipt.transaction.startswith("demo:")
            assert response.headers["Cache-Control"] == "private, no-store"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        replay.close()


def test_wsgi_handler_error_after_settlement_keeps_receipt() -> None:
    pair, route, payload, gate = route_fixture()

    def app(environ, start_response):
        raise RuntimeError("application internals must stay private")

    wrapped = WSGIMiddleware(app, gate, [route], trusted_origin="http://127.0.0.1:8091")
    response_meta: list[tuple[str, list[tuple[str, str]]]] = []

    def start_response(status, headers, exc_info=None):
        response_meta.append((status, headers))
        return lambda body: None

    body = list(
        wrapped(
            {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": "/paid",
                "HTTP_PAYMENT_SIGNATURE": encode_header(payload),
            },
            start_response,
        )
    )
    assert response_meta[0][0].startswith("500")
    assert any(name == "PAYMENT-RESPONSE" for name, _ in response_meta[0][1])
    assert b"application_error" in body[0]
    assert b"application internals" not in body[0]


def test_asgi_handler_error_after_settlement_keeps_receipt() -> None:
    _, route, payload, gate = route_fixture()

    async def app(scope, receive, send):
        raise RuntimeError("private application error")

    wrapped = ASGIMiddleware(app, gate, [route], trusted_origin="http://127.0.0.1:8091")

    async def invoke():
        sent: list[dict[str, object]] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await wrapped(
            {
                "type": "http",
                "method": "GET",
                "path": "/paid",
                "query_string": b"",
                "headers": [(b"payment-signature", encode_header(payload).encode())],
            },
            receive,
            send,
        )
        return sent

    response = asyncio.run(invoke())
    assert response[0]["status"] == 500
    assert any(name == b"PAYMENT-RESPONSE" for name, _ in response[0]["headers"])
    assert b"application_error" in response[1]["body"]
    assert b"private application error" not in response[1]["body"]


def test_wsgi_coalesced_payment_headers_and_untrusted_origin_fail_closed() -> None:
    _, route, payload, gate = route_fixture()
    called = False

    def app(environ, start_response):
        nonlocal called
        called = True
        start_response("200 OK", [])
        return [b"content"]

    wrapped = WSGIMiddleware(app, gate, [route], trusted_origin="http://127.0.0.1:8091")
    statuses: list[str] = []
    body = list(
        wrapped(
            {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": "/paid",
                "HTTP_HOST": "attacker.example",
                "HTTP_PAYMENT_SIGNATURE": encode_header(payload) + ", " + encode_header(payload),
            },
            lambda status, headers, exc_info=None: statuses.append(status) or (lambda data: None),
        )
    )
    assert statuses[0].startswith("400")
    assert b"invalid_payment_header" in body[0]
    assert called is False
    try:
        WSGIMiddleware(app, gate, [route], trusted_origin="https://api.example.test")
    except ValidationError:
        pass
    else:
        raise AssertionError("route with a different origin must be rejected")


def test_wsgi_stream_failure_is_audited_after_receipt_headers_are_set(tmp_path) -> None:
    from substrata.audit import AuditLog

    pair, route, payload, _ = route_fixture()
    audit = AuditLog(tmp_path / "provider-audit.sqlite3")
    gate = PaymentGate(pair.facilitator, ReplayStore(":memory:"), audit_log=audit)

    class BrokenBody:
        def __iter__(self):
            yield b"partial"
            raise RuntimeError("private stream detail")

        def close(self):
            return

    def app(environ, start_response):
        start_response("200 OK", [])
        return BrokenBody()

    wrapped = WSGIMiddleware(app, gate, [route], trusted_origin="http://127.0.0.1:8091")
    headers: list[tuple[str, str]] = []
    response = wrapped(
        {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/paid",
            "HTTP_PAYMENT_SIGNATURE": encode_header(payload),
        },
        lambda status, response_headers, exc_info=None: (
            headers.extend(response_headers) or (lambda data: None)
        ),
    )
    assert any(name == "PAYMENT-RESPONSE" for name, _ in headers)
    iterator = iter(response)
    assert next(iterator) == b"partial"
    try:
        next(iterator)
    except RuntimeError as exc:
        assert "private stream detail" in str(exc)
    else:
        raise AssertionError("broken stream exception should propagate")
    events = audit.list_events(event_type="provider.handler_failed")
    assert events[-1]["data"] == {"status": "200"}
    audit.close()
