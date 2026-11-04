# Ledger and recovery

Use a file-backed `Ledger` to preserve budget history when an agent restarts.
`BuyerClient` requires a supplied ledger, a `ledger_path`, or an explicit opt-in
to ephemeral accounting.

## Payment states

| State | Meaning | Counts against budget? |
| --- | --- | --- |
| `pending` | Reserved; no final outcome recorded | Yes |
| `settled` | Settlement recorded | Yes |
| `uncertain` | Authorization may have settled or may still be usable | Yes |
| `failed` | Confirmed unsent or failed attempt released | No |

The buyer reserves before signing and sends at most one paid retry. A lost
response or invalid receipt cannot prove that settlement failed. The ledger
keeps that amount reserved until an operator establishes its outcome.

## Inspect a durable ledger

```python
from decimal import Decimal
from substrata.storage import Ledger

with Ledger("payments.sqlite3") as ledger:
    reservation = ledger.reserve_once(
        "example-request",
        Decimal("0.1"),
        currency="stellar_testnet:XLM",
        limit=Decimal("1"),
        request_fingerprint="merchant-and-resource-identity",
    )
    if reservation.created:
        ledger.release("example-request", reason="inspection example; never sent")
    print(ledger.get("example-request"))
```

`reserve_once` returns a `Reservation` with `entry` and `created`. Only the caller
that created it may authorize the payment. A repeated ID with conflicting amount,
currency, or fingerprint is rejected. `BuyerClient` supplies a fingerprint that
binds the offer and HTTP request context.

Choose a new ID for each new payment. Reusing an ID for an existing pending,
uncertain, settled, or failed attempt does not authorize another payment.

Network and asset identity belong in the currency bucket. Domain requests expose
`request.currency_key`; a Soroban token key includes its contract address.

## Reconcile an uncertain attempt

First query the responsible facilitator or network. Establish whether settlement
succeeded and whether an outstanding signed authorization can still be used.
An HTTP timeout does not revoke an authorization.

After confirming settlement:

```bash
substrata ledger reconcile-settled REQUEST_ID \
  --settlement-id TRANSACTION_HASH \
  --database payments.sqlite3
```

After confirming that the payment failed and cannot still settle:

```bash
substrata ledger reconcile-failed REQUEST_ID \
  --reason "Confirmed failure; authorization no longer usable" \
  --database payments.sqlite3
```

These commands update the local ledger and append a reconciliation audit event.
They do not query the network, retry payment, or issue a refund. Terminal settled
and failed records cannot be changed to a different status.

## Audit records

```python
from substrata.audit import AuditLog

with AuditLog("payments.sqlite3") as audit:
    audit.append("operator.inspection", data={"source": "example"})
    assert audit.inspect_chain().valid
```

The audit log redacts secret-like fields before persistence and chains events
with hashes. `inspect_chain()` reports the first detected mismatch. The chain
supports local integrity checks; it is not an independently anchored record
against a party that can replace the database and recompute every hash.

```bash
substrata ledger list --status uncertain --database payments.sqlite3
substrata audit verify --database payments.sqlite3
substrata audit export --database payments.sqlite3
```

## Provider replay storage

Provider instances serving the same payment recipient should share durable
`ReplayStore` state. Claims identify the underlying authorization and survive
process restarts. A claim remains consumed after an ambiguous settlement.
Replacing the replay database can allow an old proof to reach settlement again;
reconcile outstanding payments before replacing that state.

See [provider middleware](provider.md) for canonical proof identity and the
[payment lifecycle](architecture/payments.md) for reservation ordering.
