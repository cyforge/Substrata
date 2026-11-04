# Stellar authorization signer

See the [verified testnet settlement](testnet-settlement.md) for a confirmed live
XLM transfer, transaction hash, independent balance checks, and tested components.

`StellarAuthSigner` supports x402 v2 `exact` payments for SEP-41 Soroban token contracts on Stellar testnet and public network. Install the optional `stellar-sdk` dependency to use it. The base Substrata package imports no Stellar SDK modules until signing starts.

```python
from substrata.integrations.stellar import StellarAuthSigner

signer = StellarAuthSigner(
    "S...wallet secret seed...",
    rpc_url="https://soroban-testnet.stellar.org",
    network="stellar:testnet",
)
```

The signer reads the current ledger, simulates the token's `decimals()` function, and accepts only contracts that report seven decimals. This matches Substrata's exact seven-place amount model. It then simulates one SEP-41 `transfer(from, to, amount)` invocation and checks that the token, payer, recipient, amount, authorization credentials, and root invocation match the x402 offer. It rejects source-account and delegated credentials, extra operations, and nested authorizations.

Authorization expires after the smaller of the offer timeout and `max_authorization_seconds`, which defaults to 60 seconds. Operators can lower or raise that local cap up to 3600 seconds. If a paid request has an unknown outcome, the ledger keeps its budget reserved. Reconcile it only after checking whether its signed authorization can still be used; a timeout does not cancel the authorization.

The signer signs Soroban authorization entries only. It does not sign the transaction envelope or submit a transaction. Its XDR goes to the resource server in the official Stellar `exact` payload shape, with `payload.transaction` containing base64 XDR. The resource server's facilitator handles verification and settlement.

The buyer client requires HTTPS for this live scheme. A successful `PAYMENT-RESPONSE` is the resource server's settlement claim; the client checks its network, transaction hash, amount when supplied, and payer when supplied. It does not independently query Stellar to confirm settlement. No live facilitator or network settlement is bundled with the signer.

For offline demonstrations, use the separate `substrata-demo` scheme from `substrata.x402.demo`. That signer accepts loopback resource URLs only and returns visibly marked `demo:` receipts without broadcasting transactions.
