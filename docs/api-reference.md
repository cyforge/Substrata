# Python API reference

Substrata exposes typed Python objects for buying paid resources, enforcing spend limits, and protecting provider routes. This reference covers the main entry points; the linked guides include complete examples.

## Buyer client

```python
from substrata.client import AsyncBuyerClient, BuyerClient, ClientResponse, HTTPResponse
```

Construct `BuyerClient(policy, signer, *, ledger=..., transport=..., audit_log=...)` with a [SpendPolicy](policies.md), a payment signer, and a persistent [Ledger](ledger.md). If you omit `ledger`, pass `ledger_path` to choose its database file. An ephemeral ledger requires `allow_ephemeral_ledger=True` and loses spend history when the client closes.

| Method | Behavior |
| --- | --- |
| `get(url, *, headers=None, request_id=None)` | Fetch a resource and handle one payment challenge. |
| `post(url, body, *, headers=None, request_id=None, replayable=False)` | Send bytes. Paid retries require explicit `replayable=True`. |
| `request(method, url, *, body=None, headers=None, request_id=None, replayable=False)` | Make a request through the same policy and payment flow. |
| `close()` | Close resources owned by the client. Externally supplied ledgers remain open. |

The client is a context manager. Its defaults are a 10-second transport timeout, a 1 MiB response limit, and a 1 MiB request body limit; configure them with `timeout`, `max_response_bytes`, and `max_request_body_bytes`.

`ClientResponse` contains `status_code`, `headers`, `body` as bytes, and optional `payment`. Inspect the payment result as well as the HTTP status: a successful HTTP response alone does not prove settlement.

`AsyncBuyerClient(client)` wraps a configured synchronous client. Its `get`, `post`, `request`, and `close` methods are awaitable, and it supports `async with`. Cancellation waits for an in-flight worker to finish recording its outcome; it cannot undo an authorization already sent.

See the [buyer client guide](client.md) for transport, retry, denial, and cancellation behavior.

## Signer and transport interfaces

Custom signers implement the `PaymentSigner` protocol from `substrata.client`:

```python
from substrata.x402.models import PaymentPayload, PaymentRequirements, ResourceInfo

class CustomSigner:
    def supports(self, requirement: PaymentRequirements) -> bool:
        ...

    def sign(self, requirement: PaymentRequirements, resource: ResourceInfo) -> PaymentPayload:
        ...
```

Return a real boolean from `supports`. A signer must validate the offer before signing and produce a payload that matches the accepted requirement. Use `StellarAuthSigner` for the supported Stellar authorization flow.

Custom transports implement `Transport.send(method, url, headers, body, timeout, max_response_bytes)` and return an `HTTPResponse(status_code, headers, body)`. Enforce the size limit while reading, and preserve payment headers on HTTP 402 responses. The default transport rejects redirects.

## Policy and payment models

```python
from substrata.models import Asset, PaymentRequest, PaymentResult, PaymentStatus
from substrata.policy import PolicyEngine, SpendPolicy
```

| Entry point | Purpose |
| --- | --- |
| `Asset(code, issuer=None, contract_id=None)` | Identify a native, issued, or contract asset. |
| `PaymentRequest` | Carry the recipient, amount, asset, network, origin, and request identity for an offer. |
| `SpendPolicy` | Define recipient, origin, asset, network, and amount restrictions. |
| `PolicyEngine(policy).evaluate(request, *, spent_total=..., spent_today=...)` | Return a typed `PolicyDecision`; evaluation itself does not reserve funds. |
| `policy.intersect(other)` | Produce a policy with the stricter permissions and limits. |
| `policy.to_dict()` / `SpendPolicy.from_dict(data)` | Serialize or validate a policy for storage. |
| `PaymentResult` | Describe the client's payment outcome and settlement identity. |

Amounts use `Decimal`; construct them from strings. The client reserves funds atomically after policy evaluation. See [spend policies](policies.md) for allowlists and budget semantics.

## Ledger and audit records

```python
from substrata.audit import AuditLog
from substrata.storage import Ledger, ReplayStore
```

These SQLite-backed classes accept a database path and support context managers. Several classes can share a database file.

| Method | Purpose |
| --- | --- |
| `ledger.reserve_once(request_id, amount, *, currency, limit=None, daily_limit=None, request_fingerprint=None, metadata=None)` | Reserve budget once. Returns a reservation with `entry` and `created`. |
| `ledger.commit(request_id, *, settlement_id=None, metadata=None)` | Mark an existing reservation settled. |
| `ledger.release(request_id, *, reason=None)` | Mark an existing reservation failed and release its budget. |
| `ledger.mark_uncertain(request_id, *, reason=None, metadata=None)` | Keep budget reserved when settlement cannot be confirmed. |
| `ledger.get(request_id)` | Retrieve one entry, or `None`. |
| `ledger.spent(currency=..., include_pending=True)` | Sum settled and reserved amounts for an asset identity. |
| `ledger.spent_today(currency=..., include_pending=True)` | Return spend for the current UTC day. |
| `ledger.list_entries(status=..., limit=100, offset=0)` | Inspect a bounded page of entries. Filters are optional. |
| `audit.append(event_type, *, request_id=None, data=None)` | Append an event to the integrity chain. |
| `audit.inspect_chain()` | Return an integrity verification result. |
| `replay_store.claim(scope, proof_id, *, digest=None)` | Claim a proof identity durably; return whether it was newly claimed. |

Use [ledger and recovery](ledger.md) before reconciling uncertain payments. Releasing a local reservation does not revoke a signed authorization.

## Provider middleware

```python
from substrata.provider import (
    ASGIMiddleware,
    PaidRoute,
    PaymentGate,
    StellarProofIdentityExtractor,
    WSGIMiddleware,
)
```

Define a `PaidRoute` with `path`, `method`, `requirements`, and `resource_url`. Its optional `additional_requirements` offer alternative payment methods. Create `PaymentGate(facilitator, replay_store, *, proof_identity=None, audit_log=None)` to verify and settle proofs before a handler runs.

Wrap an application with `ASGIMiddleware(app, gate, routes, *, trusted_origin=...)` or its WSGI equivalent. The trusted origin binds the public request URL to the configured paid route. Configure `StellarProofIdentityExtractor` for Stellar proofs; the demo proof extractor is for the local demo scheme.

See [provider middleware](provider.md) for framework examples, replay claims, and handler failures after settlement.

## Stellar and facilitator integration

```python
from substrata.integrations.stellar import StellarAuthSigner
from substrata.x402.facilitator import HTTPFacilitator
```

`StellarAuthSigner(secret_seed, *, rpc_url, network="stellar:testnet", ...)` signs Soroban authorization entries for fee-sponsored, exact-scheme SAC transfers. It requires the optional `stellar` dependency. The default authorization lifetime is at most 60 seconds. See the [Stellar guide](stellar.md) for accepted requirements and network checks.

`HTTPFacilitator(url, *, api_key=None, timeout=10.0, max_response_bytes=1048576, transport=None)` implements `supported()`, `verify(payload, requirements)`, and `settle(payload, requirements)`. It uses HTTPS except for loopback development URLs and does not automatically retry settlement.

See [x402 wire protocol](x402.md) for encoded headers and [testnet settlement](testnet-settlement.md) for a recorded on-chain payment.
