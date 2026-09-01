import json
from collections.abc import Mapping

import pytest

from substrata.errors import ValidationError
from substrata.x402.facilitator import (
    FacilitatorError,
    HTTPFacilitator,
    SupportedKind,
    TransportResponse,
)
from substrata.x402.models import (
    PaymentPayload,
    PaymentRequirements,
    SettlementResponse,
    VerifyResponse,
)

REQUIREMENTS = PaymentRequirements(
    scheme="exact",
    network="stellar:testnet",
    amount="1",
    asset="CABC",
    pay_to="GABC",
    max_timeout_seconds=60,
    extra={"areFeesSponsored": True},
)
PAYLOAD = PaymentPayload(REQUIREMENTS, {"transaction": "eA=="})


class FakeTransport:
    def __init__(self, responses: list[TransportResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, Mapping[str, str], bytes | None, float, int]] = []

    def request(self, method, url, *, headers, body, timeout, max_response_bytes):
        self.calls.append((method, url, headers, body, timeout, max_response_bytes))
        return self.responses.pop(0)


def test_verify_sends_standard_v2_envelope_and_bearer_auth() -> None:
    transport = FakeTransport([TransportResponse(200, b'{"isValid":true,"payer":"GABC"}')])
    facilitator = HTTPFacilitator(
        "https://facilitator.example/api/", api_key="secret", transport=transport
    )
    result = facilitator.verify(PAYLOAD, REQUIREMENTS)
    method, url, headers, body, timeout, cap = transport.calls[0]
    assert result == VerifyResponse(True, payer="GABC")
    assert (method, url) == ("POST", "https://facilitator.example/api/verify")
    assert headers["Authorization"] == "Bearer secret"
    assert timeout == 10.0 and cap == 1_048_576
    request = json.loads(body or b"")
    assert request["x402Version"] == 2
    assert request["paymentPayload"]["payload"] == {"transaction": "eA=="}
    assert request["paymentRequirements"]["network"] == "stellar:testnet"


def test_settle_posts_once_and_returns_pending_outcome() -> None:
    response = {
        "success": False,
        "transaction": "abc123",
        "network": "stellar:testnet",
        "errorReason": "settlement_pending",
    }
    transport = FakeTransport([TransportResponse(200, json.dumps(response).encode())])
    result = HTTPFacilitator("https://facilitator.example", transport=transport).settle(
        PAYLOAD,
        REQUIREMENTS,
    )
    assert isinstance(result, SettlementResponse)
    assert result.error_reason == "settlement_pending"
    assert len(transport.calls) == 1
    assert transport.calls[0][1].endswith("/settle")


def test_supported_decodes_kinds() -> None:
    transport = FakeTransport(
        [
            TransportResponse(
                200,
                b'{"kinds":[{"x402Version":2,"scheme":"exact","network":"stellar:testnet","extra":{"areFeesSponsored":true}}],"extensions":["bazaar"],"signers":{"stellar:*":["GABC"]}}',
            )
        ]
    )
    result = HTTPFacilitator("https://facilitator.example", transport=transport).supported()
    assert result.kinds == (
        SupportedKind(2, "exact", "stellar:testnet", {"areFeesSponsored": True}),
    )
    assert result.extensions == ("bazaar",)
    assert result.signers == {"stellar:*": ("GABC",)}
    assert result.to_dict()["extensions"] == ["bazaar"]
    assert transport.calls[0][0] == "GET"
    assert transport.calls[0][1].endswith("/supported")


@pytest.mark.parametrize(
    "url",
    [
        "http://payments.example",
        "http://192.168.1.2",
        "ftp://facilitator.example",
        "https://user:password@facilitator.example",
        "https://facilitator.example/?token=secret",
    ],
)
def test_unsafe_facilitator_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        HTTPFacilitator(url)


def test_loopback_http_is_available_for_development() -> None:
    transport = FakeTransport(
        [TransportResponse(200, b'{"kinds":[],"extensions":[],"signers":{}}')]
    )
    assert HTTPFacilitator("http://localhost:4021", transport=transport).supported().kinds == ()


def test_failure_status_and_invalid_body_do_not_echo_response_or_key() -> None:
    private = "top-secret-value"
    transport = FakeTransport([TransportResponse(403, private.encode())])
    client = HTTPFacilitator("https://facilitator.example", api_key=private, transport=transport)
    with pytest.raises(FacilitatorError) as raised:
        client.verify(PAYLOAD, REQUIREMENTS)
    assert private not in str(raised.value)
    assert "403" not in str(raised.value)
    assert raised.value.status_code == 403

    transport = FakeTransport([TransportResponse(200, b'{"isValid":1}')])
    with pytest.raises(FacilitatorError, match="invalid verify response"):
        HTTPFacilitator("https://facilitator.example", transport=transport).verify(
            PAYLOAD, REQUIREMENTS
        )


def test_oversized_response_and_redirect_are_rejected() -> None:
    transport = FakeTransport([TransportResponse(200, b"x" * 32)])
    with pytest.raises(FacilitatorError, match="exceeds size limit"):
        HTTPFacilitator(
            "https://facilitator.example", max_response_bytes=16, transport=transport
        ).supported()
    transport = FakeTransport([TransportResponse(302, b"")])
    with pytest.raises(FacilitatorError):
        HTTPFacilitator("https://facilitator.example", transport=transport).supported()


def test_default_transport_does_not_follow_redirect_with_auth() -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    seen: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            seen.append((self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", "/capture")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = HTTPFacilitator(f"http://127.0.0.1:{server.server_port}", api_key="private-token")
        with pytest.raises(FacilitatorError):
            client.verify(PAYLOAD, REQUIREMENTS)
        assert seen == [("/verify", "Bearer private-token")]
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_supported_requires_all_standard_fields() -> None:
    transport = FakeTransport([TransportResponse(200, b'{"kinds":[]}')])
    with pytest.raises(FacilitatorError, match="invalid supported response"):
        HTTPFacilitator("https://facilitator.example", transport=transport).supported()


def test_supported_rejects_wrong_extension_and_signer_types() -> None:
    malformed = b'{"kinds":[],"extensions":{},"signers":[]}'
    transport = FakeTransport([TransportResponse(200, malformed)])
    with pytest.raises(FacilitatorError, match="invalid supported response"):
        HTTPFacilitator("https://facilitator.example", transport=transport).supported()


def test_extension_responses_are_decoded_from_http_sidechannel_only() -> None:
    import base64

    header = base64.b64encode(b'{"bazaar":{"verified":true}}').decode("ascii")
    transport = FakeTransport(
        [
            TransportResponse(
                200,
                b'{"isValid":true}',
                {"eXtEnSiOn-ReSpOnSeS": header},
            )
        ]
    )
    result = HTTPFacilitator("https://facilitator.example", transport=transport).verify(
        PAYLOAD, REQUIREMENTS
    )
    assert result.extension_responses == {"bazaar": {"verified": True}}
    assert "extensionResponses" not in result.to_dict()


def test_invalid_extension_sidechannel_fails_closed() -> None:
    transport = FakeTransport(
        [TransportResponse(200, b'{"isValid":true}', {"EXTENSION-RESPONSES": "%%%"})]
    )
    with pytest.raises(FacilitatorError, match="invalid verify response"):
        HTTPFacilitator("https://facilitator.example", transport=transport).verify(
            PAYLOAD, REQUIREMENTS
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://facilitator.example:invalid",
        "https://facilitator.example:/api",
        "https://[2001:db8::1",
        "https://user@facilitator.example",
        "https://facilitator.example/path#",
        "https://facilitator.example/path?",
        "https://facilitator.example/bad%2",
        "https:\\facilitator.example\\api",
        "https://facilitator.example/path\nInjected",
    ],
)
def test_facilitator_url_parser_rejects_malformed_or_ambiguous_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        HTTPFacilitator(url)


def test_facilitator_rejects_header_injection_in_api_key() -> None:
    with pytest.raises(ValidationError):
        HTTPFacilitator("https://facilitator.example", api_key="token\r\nInjected: yes")


def test_supported_response_preserves_immutable_extra_and_rejects_bad_network() -> None:
    from substrata.x402.facilitator import SupportedResponse

    data = {
        "kinds": [
            {
                "x402Version": 2,
                "scheme": "exact",
                "network": "stellar:testnet",
                "extra": {"limits": {"networks": ["stellar:testnet"]}},
            }
        ],
        "extensions": [],
        "signers": {},
    }
    response = SupportedResponse.from_dict(data)
    data["kinds"][0]["extra"]["limits"]["networks"].append("stellar:pubnet")
    assert response.kinds[0].to_dict()["extra"]["limits"]["networks"] == ["stellar:testnet"]
    with pytest.raises(ValidationError):
        SupportedResponse.from_dict(
            {
                "kinds": [{"x402Version": 2, "scheme": "exact", "network": "bad"}],
                "extensions": [],
                "signers": {},
            }
        )


def test_transport_response_header_metadata_is_bounded_and_unambiguous() -> None:
    with pytest.raises(ValidationError, match="size limit"):
        TransportResponse(200, b"", {"x-large": "x" * 1_500_000})
    with pytest.raises(ValidationError, match="too many"):
        TransportResponse(200, b"", {f"x-{index}": "v" for index in range(129)})
    with pytest.raises(ValidationError, match="invalid header value"):
        TransportResponse(200, b"", {"x-test": "bad\r\nInjected: yes"})
    with pytest.raises(ValidationError, match="duplicate extension"):
        TransportResponse(
            200,
            b"",
            {
                "Extension-Responses": "e30=",
                "extension-responses": "e30=",
            },
        )
