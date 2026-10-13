# Paid API providers

`substrata.provider` protects configured HTTP routes with x402 v2 payment offers. Each route fixes its method, path, one or more current `PaymentRequirements`, and canonical resource URL. Put alternate offers in `additional_requirements`; the challenge lists every configured offer. Middleware ignores request `Host` and forwarded-host headers. Set `trusted_origin` to the origin clients should see, then use that same origin in each route URL. External origins require HTTPS; plain HTTP works only for loopback development.

## Payment flow

An unpaid request receives status 402 with a `PAYMENT-REQUIRED` challenge. The middleware decodes one bounded `PAYMENT-SIGNATURE`, checks that its accepted offer and resource URL match the configured route, and calls the facilitator's `verify`. After verification, it atomically claims the signed proof identity in `ReplayStore`; then it calls `settle` once. The protected application runs only after a valid settlement receipt. Paid responses include `PAYMENT-RESPONSE` and `Cache-Control: private, no-store`.

Use a shared file-backed replay database for all processes that serve the same network and payee. A claim remains consumed after an uncertain settlement result. Reconcile uncertain payments with your facilitator before operating a replacement service against a fresh replay database.

The middleware settles before running the protected handler. A handler error after settlement does not refund the payment. WSGI error responses retain the settlement receipt when possible; streamed response failures keep already-sent receipt headers.

## WSGI example

Run the local demo server:

```sh
PYTHONPATH=. python examples/paid_api.py
```

It listens on `127.0.0.1:8091/paid`. The `substrata-demo` scheme signs a short-lived HMAC proof for loopback use. It never submits a Stellar transaction. The example stores replay claims in `substrata-provider-demo.sqlite3`.

```python
from substrata.provider import PaidRoute, PaymentGate, WSGIMiddleware
from substrata.storage import ReplayStore

route = PaidRoute(
    path="/weather",
    method="GET",
    requirements=requirements,
    resource_url="https://api.example.test/weather",
)
replay = ReplayStore("provider.sqlite3")
gate = PaymentGate(facilitator, replay)
app = WSGIMiddleware(app, gate, [route], trusted_origin="https://api.example.test")
```

## ASGI example

```python
from substrata.provider import ASGIMiddleware

app = ASGIMiddleware(asgi_app, gate, [route], trusted_origin="https://api.example.test")
```

ASGI passes lifespan and WebSocket scopes to the wrapped app. Synchronous verification, replay storage, and settlement run in worker threads. `OPTIONS` and routes absent from the configured table pass through unchanged. Query strings must match the query configured in the route URL; other queries fail with `resource_mismatch`.

## Stellar proofs

For `exact` on Stellar testnet or pubnet, configure `StellarProofIdentityExtractor` on `PaymentGate`. It lazily imports `stellar-sdk`, parses the transaction, and derives replay identity from the authorization address and nonce. It checks one contract transfer and an address-based authorization rooted at the same transfer. The facilitator still verifies signatures, expiry, and ledger state. Install the optional Stellar SDK dependency for this parser.

```python
from substrata.provider import PaymentGate, StellarProofIdentityExtractor

gate = PaymentGate(
    facilitator,
    replay,
    proof_identity=StellarProofIdentityExtractor(),
)
```

Custom proof identity adapters must parse the signed authorization from a trusted format. Hashing a payment header, payload, or transaction envelope does not prevent replay across envelopes.

## Response and failure behavior

Challenges, malformed headers, and payment failures use machine-readable error codes. Middleware rejects duplicate x402 payment headers and oversized headers before calling the facilitator. Verify or replay-store outages and uncertain settlement return 503; no protected content is sent. A rejected receipt also blocks the handler. Configured audit logging must succeed before settlement proceeds. The gate records verify, replay, settlement, uncertainty, and handler outcomes without proof data. An audit write failure after settlement cannot undo the payment, so the paid response is retained.
