"""ASGI and WSGI adapters for configured paid routes."""

from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import Awaitable, Callable, Iterable
from typing import Protocol
from urllib.parse import urlsplit

from substrata.errors import ValidationError
from substrata.validation import normalize_origin
from substrata.x402.codec import decode_header, encode_header
from substrata.x402.models import PaymentPayload, PaymentRequired

from .gate import GateResult, PaidRoute, PaymentGate

_HEADER_NAME = b"payment-signature"


class ASGIReceive(Protocol):
    def __call__(self) -> Awaitable[dict[str, object]]: ...


class ASGISend(Protocol):
    def __call__(self, message: dict[str, object]) -> Awaitable[None]: ...


class ASGIApp(Protocol):
    def __call__(
        self, scope: dict[str, object], receive: ASGIReceive, send: ASGISend
    ) -> Awaitable[None]: ...


class WSGIStartResponse(Protocol):
    def __call__(
        self,
        status: str,
        response_headers: list[tuple[str, str]],
        exc_info: tuple[type[BaseException], BaseException, object] | None = None,
    ) -> Callable[[bytes], object]: ...


class WSGIApp(Protocol):
    def __call__(
        self, environ: dict[str, object], start_response: WSGIStartResponse
    ) -> Iterable[bytes]: ...


class _MiddlewareBase:
    def __init__(
        self,
        gate: PaymentGate,
        routes: Iterable[PaidRoute],
        *,
        trusted_origin: str,
        header_limit: int = 1_500_000,
        header_count_limit: int = 100,
    ) -> None:
        self.gate = gate
        self.trusted_origin = normalize_origin(trusted_origin)
        trusted_parts = urlsplit(self.trusted_origin)
        hostname = trusted_parts.hostname or ""
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname.lower() == "localhost"
        if trusted_parts.scheme != "https" and not loopback:
            raise ValidationError("trusted_origin must use HTTPS outside loopback")
        if type(header_limit) is not int or not 1024 <= header_limit <= 2_000_000:
            raise ValidationError("header_limit must be from 1024 to 2000000 bytes")
        if type(header_count_limit) is not int or not 1 <= header_count_limit <= 1000:
            raise ValidationError("header_count_limit must be from 1 to 1000")
        self.header_limit = header_limit
        self.header_count_limit = header_count_limit
        self.routes: dict[tuple[str, str], PaidRoute] = {}
        for route in routes:
            if not isinstance(route, PaidRoute):
                raise ValidationError("routes must contain PaidRoute values")
            key = (route.method, route.path)
            if key in self.routes:
                raise ValidationError("paid route method and path must be unique")
            parts = urlsplit(route.resource_url)
            if normalize_origin(f"{parts.scheme}://{parts.netloc}") != self.trusted_origin:
                raise ValidationError("paid route resource must use trusted_origin")
            if parts.path != route.path:
                raise ValidationError("paid route resource path must match route path")
            self.routes[key] = route

    def _route(self, method: str, path: str) -> PaidRoute | None:
        return self.routes.get((method.upper(), path))

    @staticmethod
    def _query_matches(route: PaidRoute, query: str) -> bool:
        configured = urlsplit(route.resource_url).query
        return query == configured

    def _parse_payment(self, value: bytes) -> PaymentPayload | None:
        if not value or len(value) > self.header_limit or b"," in value:
            raise ValidationError("invalid payment header")
        try:
            decoded = decode_header(value.decode("ascii"), PaymentPayload)
        except (UnicodeDecodeError, ValidationError):
            raise ValidationError("invalid payment header") from None
        if not isinstance(decoded, PaymentPayload):
            raise ValidationError("invalid payment header")
        return decoded

    @staticmethod
    def _response_headers(
        result: GateResult, *, vary: str = "PAYMENT-SIGNATURE"
    ) -> list[tuple[str, str]]:
        headers = [("Cache-Control", "private, no-store"), ("Vary", vary)]
        if result.response_header is not None:
            headers.append(("PAYMENT-RESPONSE", result.response_header))
        return headers

    @staticmethod
    def _merge_vary(values: Iterable[str]) -> str:
        tokens = [token.strip() for value in values for token in value.split(",") if token.strip()]
        if any(token == "*" for token in tokens):
            return "*"
        if not any(token.lower() == "payment-signature" for token in tokens):
            tokens.append("PAYMENT-SIGNATURE")
        return ", ".join(tokens)

    @staticmethod
    def _challenge(
        route: PaidRoute, error: str | None = None
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        challenge = route.challenge
        if error is not None:
            challenge = PaymentRequired(
                resource=challenge.resource,
                accepts=challenge.accepts,
                error=error,
            )
        body = json.dumps({"error": error or "payment_required"}, separators=(",", ":")).encode()
        return (
            402,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("PAYMENT-REQUIRED", encode_header(challenge)),
                ("Cache-Control", "private, no-store"),
                ("Vary", "PAYMENT-SIGNATURE"),
            ],
            body,
        )

    @staticmethod
    def _error(status: int, code: str) -> tuple[int, list[tuple[str, str]], bytes]:
        body = json.dumps({"error": code}, separators=(",", ":")).encode()
        return (
            status,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "private, no-store"),
                ("Vary", "PAYMENT-SIGNATURE"),
            ],
            body,
        )

    def _failed_gate(
        self, route: PaidRoute, result: GateResult
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        if result.code in {
            "settlement_uncertain",
            "replay_store_unavailable",
            "verification_unavailable",
            "audit_unavailable",
            "proof_identity_invalid",
        }:
            return self._error(503, result.code)
        return self._challenge(route, result.code)


class ASGIMiddleware(_MiddlewareBase):
    """Protect exact configured HTTP routes; all payment I/O runs off-loop."""

    def __init__(
        self,
        app: ASGIApp,
        gate: PaymentGate,
        routes: Iterable[PaidRoute],
        *,
        trusted_origin: str,
        header_limit: int = 1_500_000,
        header_count_limit: int = 100,
    ) -> None:
        super().__init__(
            gate,
            routes,
            trusted_origin=trusted_origin,
            header_limit=header_limit,
            header_count_limit=header_count_limit,
        )
        self.app = app

    async def __call__(
        self, scope: dict[str, object], receive: ASGIReceive, send: ASGISend
    ) -> None:
        scope_type = scope.get("type")
        if scope_type != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method")
        path = scope.get("path")
        if not isinstance(method, str) or not isinstance(path, str):
            await self.app(scope, receive, send)
            return
        if method.upper() == "OPTIONS":
            await self.app(scope, receive, send)
            return
        route = self._route(method, path)
        if route is None:
            await self.app(scope, receive, send)
            return
        raw_query = scope.get("query_string", b"")
        if not isinstance(raw_query, bytes):
            await self._send_error(send, 400, "invalid_request")
            return
        try:
            query = raw_query.decode("ascii")
        except UnicodeDecodeError:
            await self._send_error(send, 400, "invalid_request")
            return
        if not self._query_matches(route, query):
            await self._send_error(send, 400, "resource_mismatch")
            return

        headers = scope.get("headers")
        if not isinstance(headers, (list, tuple)):
            await self._send_error(send, 400, "invalid_request")
            return
        if len(headers) > self.header_count_limit or any(
            not isinstance(header, (tuple, list))
            or len(header) != 2
            or not isinstance(header[0], bytes)
            or not isinstance(header[1], bytes)
            for header in headers
        ):
            await self._send_error(send, 400, "invalid_request")
            return
        if sum(len(name) + len(value) for name, value in headers) > self.header_limit:
            await self._send_error(send, 431, "headers_too_large")
            return
        values = [value for name, value in headers if name.lower() == _HEADER_NAME]
        if len(values) > 1:
            await self._send_error(send, 400, "duplicate_payment_header")
            return
        if not values:
            await self._send_response(send, *self._challenge(route))
            return
        try:
            payload = self._parse_payment(values[0])
        except ValidationError:
            await self._send_error(send, 400, "invalid_payment_header")
            return
        assert payload is not None
        result = await asyncio.to_thread(self.gate.process, route, payload)
        if not result.paid:
            await self._send_response(send, *self._failed_gate(route, result))
            return

        response_started = False
        response_status = 500
        handler_outcome_recorded = False

        async def paid_send(message: dict[str, object]) -> None:
            nonlocal response_started, response_status, handler_outcome_recorded
            if message.get("type") == "http.response.start":
                response_started = True
                status = message.get("status")
                response_status = status if type(status) is int else 500
                existing = message.get("headers", [])
                if not isinstance(existing, (list, tuple)):
                    existing = []
                vary_values = [
                    value.decode("latin-1")
                    for name, value in existing
                    if isinstance(name, bytes)
                    and name.lower() == b"vary"
                    and isinstance(value, bytes)
                ]
                clean = [
                    (name, value)
                    for name, value in existing
                    if not (
                        isinstance(name, bytes)
                        and name.lower() in {b"payment-response", b"cache-control", b"vary"}
                    )
                ]
                clean.extend(
                    (name.encode("ascii"), value.encode("latin-1"))
                    for name, value in self._response_headers(
                        result, vary=self._merge_vary(vary_values)
                    )
                )
                message = dict(message)
                message["headers"] = clean
            await send(message)
            if (
                message.get("type") == "http.response.body"
                and message.get("more_body") is not True
                and response_started
                and not handler_outcome_recorded
            ):
                self.gate.record_handler_outcome(response_status)
                handler_outcome_recorded = True

        try:
            await self.app(scope, receive, paid_send)
        except Exception:
            self.gate.record_handler_outcome(response_status, failed=True)
            if response_started:
                raise
            await self._send_error_with_headers(
                send, 500, "application_error", self._response_headers(result)
            )

    @staticmethod
    async def _send_response(
        send: ASGISend, status: int, headers: list[tuple[str, str]], body: bytes
    ) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (name.encode("ascii"), value.encode("latin-1")) for name, value in headers
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    @classmethod
    async def _send_error(cls, send: ASGISend, status: int, code: str) -> None:
        await cls._send_response(send, *cls._error(status, code))

    @classmethod
    async def _send_error_with_headers(
        cls, send: ASGISend, status: int, code: str, extra: list[tuple[str, str]]
    ) -> None:
        status, headers, body = cls._error(status, code)
        replaced = {name.lower() for name, _ in extra}
        headers = [(name, value) for name, value in headers if name.lower() not in replaced]
        await cls._send_response(send, status, headers + extra, body)


class WSGIMiddleware(_MiddlewareBase):
    """WSGI adapter that preserves returned iterator and close lifecycle."""

    def __init__(
        self,
        app: WSGIApp,
        gate: PaymentGate,
        routes: Iterable[PaidRoute],
        *,
        trusted_origin: str,
        header_limit: int = 1_500_000,
        header_count_limit: int = 100,
    ) -> None:
        super().__init__(
            gate,
            routes,
            trusted_origin=trusted_origin,
            header_limit=header_limit,
            header_count_limit=header_count_limit,
        )
        self.app = app

    def __call__(
        self, environ: dict[str, object], start_response: WSGIStartResponse
    ) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD")
        path = environ.get("PATH_INFO")
        if not isinstance(method, str) or not isinstance(path, str):
            return self.app(environ, start_response)
        if method.upper() == "OPTIONS":
            return self.app(environ, start_response)
        route = self._route(method, path)
        if route is None:
            return self.app(environ, start_response)
        raw_query = environ.get("QUERY_STRING", "")
        if not isinstance(raw_query, str) or not raw_query.isascii():
            return self._wsgi_response(start_response, *self._error(400, "invalid_request"))
        if not self._query_matches(route, raw_query):
            return self._wsgi_response(start_response, *self._error(400, "resource_mismatch"))

        http_headers = [
            (name, value)
            for name, value in environ.items()
            if (name.startswith("HTTP_") or name in {"CONTENT_TYPE", "CONTENT_LENGTH"})
            and isinstance(value, str)
        ]
        if (
            len(http_headers) > self.header_count_limit
            or sum(len(name) + len(value.encode("utf-8")) for name, value in http_headers)
            > self.header_limit
        ):
            return self._wsgi_response(start_response, *self._error(431, "headers_too_large"))
        raw_payment = environ.get("HTTP_PAYMENT_SIGNATURE")
        if raw_payment is None:
            return self._wsgi_response(start_response, *self._challenge(route))
        if not isinstance(raw_payment, str) or not raw_payment.isascii():
            return self._wsgi_response(start_response, *self._error(400, "invalid_payment_header"))
        try:
            payload = self._parse_payment(raw_payment.encode("ascii"))
        except ValidationError:
            return self._wsgi_response(start_response, *self._error(400, "invalid_payment_header"))
        assert payload is not None
        result = self.gate.process(route, payload)
        if not result.paid:
            return self._wsgi_response(start_response, *self._failed_gate(route, result))

        started = False
        response_status = 500

        def paid_start_response(
            status: str,
            response_headers: list[tuple[str, str]],
            exc_info: tuple[type[BaseException], BaseException, object] | None = None,
        ) -> Callable[[bytes], object]:
            nonlocal started, response_status
            started = True
            try:
                response_status = int(status.split(" ", 1)[0])
            except (ValueError, IndexError):
                response_status = 500
            names = {"payment-response", "cache-control", "vary"}
            vary_values = [value for name, value in response_headers if name.lower() == "vary"]
            filtered = [
                (name, value) for name, value in response_headers if name.lower() not in names
            ]
            return_headers = filtered + self._response_headers(
                result, vary=self._merge_vary(vary_values)
            )
            return start_response(status, return_headers, exc_info)

        try:
            iterable = self.app(environ, paid_start_response)
        except Exception:
            self.gate.record_handler_outcome(response_status, failed=True)
            if started:
                raise
            status, headers, body = self._error(500, "application_error")
            headers.extend(self._response_headers(result))
            return self._wsgi_response(start_response, status, headers, body)
        return _ClosingIterator(
            iterable,
            on_complete=lambda: self.gate.record_handler_outcome(response_status),
            on_failure=lambda: self.gate.record_handler_outcome(response_status, failed=True),
        )

    @staticmethod
    def _wsgi_response(
        start_response: WSGIStartResponse,
        status: int,
        headers: list[tuple[str, str]],
        body: bytes,
    ) -> list[bytes]:
        reason = {
            400: "Bad Request",
            402: "Payment Required",
            431: "Request Header Fields Too Large",
            500: "Internal Server Error",
            503: "Service Unavailable",
        }.get(status, "Error")
        start_response(f"{status} {reason}", headers)
        return [body]


class _ClosingIterator:
    def __init__(
        self,
        iterable: Iterable[bytes],
        *,
        on_complete: Callable[[], None],
        on_failure: Callable[[], None],
    ) -> None:
        self.iterable = iterable
        self.iterator = iter(iterable)
        self.on_complete = on_complete
        self.on_failure = on_failure
        self.finished = False

    def __iter__(self) -> _ClosingIterator:
        return self

    def __next__(self) -> bytes:
        try:
            return next(self.iterator)
        except StopIteration:
            if not self.finished:
                self.finished = True
                self.on_complete()
            raise
        except Exception:
            if not self.finished:
                self.finished = True
                self.on_failure()
            raise

    def close(self) -> None:
        close = getattr(self.iterable, "close", None)
        if callable(close):
            close()
        if not self.finished:
            self.finished = True
            self.on_failure()
