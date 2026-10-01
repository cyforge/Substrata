# Buyer client

`BuyerClient` makes HTTP requests and pays only when a response contains a valid x402 v2 challenge. It checks the configured `SpendPolicy` before signing, reserves the amount in a durable ledger, then retries the same request once with `PAYMENT-SIGNATURE`.

Pass a file-backed ledger or an existing `Ledger` when constructing the client. This keeps budget history across restarts. Process-local accounting requires an explicit `allow_ephemeral_ledger=True` or a caller-owned `Ledger(":memory:")`.

```python
from substrata.client import BuyerClient
from substrata.constants import Network
from substrata.integrations.stellar import StellarAuthSigner
from substrata.models import Asset
from substrata.policy import SpendPolicy

policy = SpendPolicy(
    allowed_networks=frozenset({Network.STELLAR_TESTNET}),
    allowed_recipients=frozenset({"G...merchant address..."}),
    allowed_assets=frozenset({Asset.soroban("C...token contract...")}),
    max_payment="2",
    daily_limit="5",
    total_limit="20",
    allowed_origins=frozenset({"https://api.example.test"}),
)
signer = StellarAuthSigner(
    "S...wallet secret seed...",
    rpc_url="https://soroban-testnet.stellar.org",
)

with BuyerClient(policy, signer, ledger_path="payments.sqlite3") as client:
    response = client.get("https://api.example.test/data?id=7")
    print(response.status_code, response.body)
    if response.payment is not None:
        print(response.payment.status, response.payment.transaction_id)
```

Use `get()` for safe reads. `post()` and `request()` accept immutable `bytes`; a paid retry for POST, PUT, PATCH, or DELETE requires `replayable=True`. The client sends no payment header on the first request and never follows redirects. Paid requests require HTTPS, except the explicit local demo scheme on loopback URLs.

A challenge must name the exact URL being requested, including its path and query. The signer must advertise support for an offer before the client considers it. The client applies recipient, asset, network, amount, origin, and spend rules to each supported offer, then reserves the permitted choice atomically.

A paid retry with no usable receipt, an invalid receipt, or an unknown transport outcome leaves the reservation uncertain. That amount continues to count against the budget until an operator reconciles it. A valid success receipt marks the payment settled even if the resource server returned an HTTP error; inspect `status_code` and `payment.status` separately.

Supply an `AuditLog` with `audit_log=` to record policy denials, reservations, authorization readiness, settlement, and uncertain outcomes. The client never writes signed payloads, secret seeds, or raw exception text to the audit log. Caller-owned ledger and audit objects remain open when the client closes.

The client uses synchronous I/O. Wrap it with `AsyncBuyerClient` when an async application needs to keep its event loop available. The wrapper runs calls in worker threads and waits for them before closing the wrapped client. Cancelling an async call stops waiting for its result; it cannot stop a request already running in a worker or undo a payment already sent. The wrapper does not automatically resend a cancelled call. Keep the same request ID for any later retry so the ledger blocks a duplicate payment. The HTTP transport is injectable for offline integrations and tests.

```python
from substrata.client import AsyncBuyerClient, BuyerClient

sync_client = BuyerClient(policy, signer, ledger_path="payments.sqlite3")
async with AsyncBuyerClient(sync_client) as client:
    response = await client.get("https://api.example.test/data")
```
