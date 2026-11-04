# Spend policies

`SpendPolicy` defines where an agent may spend and how much it may authorize.
`PolicyEngine` evaluates a typed `PaymentRequest` and returns a decision with a
stable reason code. Evaluation performs no network or database writes.

## Create a policy

```python
from substrata.constants import Network
from substrata.models import Asset, PaymentRequest
from substrata.policy import PolicyEngine, SpendPolicy

asset = Asset("XLM")
policy = SpendPolicy(
    allowed_networks=frozenset({Network.STELLAR_TESTNET}),
    allowed_recipients=frozenset({"demo-merchant"}),
    allowed_assets=frozenset({asset}),
    allowed_origins=frozenset({"http://127.0.0.1:8091"}),
    max_payment="0.1",
    daily_limit="1",
    total_limit="2",
)
request = PaymentRequest(
    recipient="demo-merchant",
    amount="0.1",
    asset=asset,
    origin="http://127.0.0.1:8091",
)
decision = PolicyEngine(policy).evaluate(request)
assert decision.allowed
print(decision.reason_code)
```

Network, recipient, and asset allowlists start empty. An empty allowlist denies
that category. For HTTP payments, add the provider's origin explicitly;
origin-bearing requests cannot pass an empty origin allowlist. Origins include
scheme, host, and non-default port, and do not include a path or query string.

For an on-chain offer, use `Asset.soroban(contract_id)` and a real recipient
address. Contract identity, rather than a token's display code, determines asset
equality and its ledger currency bucket.

## Limits and exact amounts

| Field | Scope |
| --- | --- |
| `max_payment` | One payment |
| `daily_limit` | Active spend reserved on the UTC calendar day |
| `total_limit` | Active spend retained for a network/asset currency bucket |
| `expires_at` | A timezone-aware expiry for policy evaluation |

Pass monetary values as strings, integers, or `Decimal`. Floats, non-finite values,
and amounts with more than seven fractional digits are rejected. Zero is a valid
limit and blocks positive payments. An omitted limit leaves that limit unset.

When calling the engine directly, supply current `spent_today` and `spent_total`.
`BuyerClient` reads these values from its ledger and then reserves budget under
the same limits in a SQLite write transaction. That reservation protects against
concurrent overspend; a pure policy decision does not reserve funds.

Pending, uncertain, and settled entries count against limits. Failed entries do
not. Daily limits apply to the reservation date; total limits retain older holds.

## Combine organization and task rules

```python
task_policy = SpendPolicy(
    allowed_networks=policy.allowed_networks,
    allowed_recipients=policy.allowed_recipients,
    allowed_assets=policy.allowed_assets,
    allowed_origins=policy.allowed_origins,
    max_payment="0.05",
    total_limit="0.5",
)
effective = policy.intersect(task_policy)
assert effective.max_payment == task_policy.max_payment
```

The intersection permits only actions accepted by both policies. It intersects
allowlists, keeps the lower configured limits, and uses the earlier expiry.
Disjoint origin policies retain an explicit deny-all state.

## Load JSON rules

`SpendPolicy.to_dict()` produces JSON-safe values. `SpendPolicy.from_dict(data)`
rejects unknown fields and requires decimal strings for JSON limits. The CLI
also rejects duplicate JSON keys and oversized input.

```bash
substrata policy validate examples/spend-policy.json
substrata policy intersect examples/spend-policy.json examples/task-spend-policy.json
```

The sample policy uses placeholder recipients for local evaluation. Update its
recipient, contract asset, and allowed origins before using it with a live buyer.

## Denied requests

Decisions include `reason_code`, a readable `reason`, and budget details when
available. Common codes include `recipient_denied`, `asset_denied`,
`origin_denied`, `payment_limit_exceeded`, `daily_limit_exceeded`,
`total_limit_exceeded`, and `policy_expired`.

The buyer returns an HTTP 402 result when a supported offer is denied by policy.
It creates no payment authorization for that denied offer. See
[the buyer guide](client.md) for request and receipt handling.
