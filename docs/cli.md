# Command line reference

Install Substrata to get the `substrata` command. Every command also works through `python -m substrata`. Use `--help` on a command or subcommand to see its full argument list.

```sh
substrata --version
substrata --help
substrata demo --database demo.sqlite3
```

Commands print JSON records suitable for scripts. Exit code `0` indicates success, `1` indicates an unsuccessful check or request, and `2` indicates invalid arguments or another error. Inspect payment and HTTP fields when using `fetch`.

## Configuration and diagnostics

```sh
substrata config show
substrata doctor --init --database payments.sqlite3
substrata doctor --database payments.sqlite3
```

`config show` prints validated runtime settings with sensitive fields redacted. `doctor` checks database access; `--init` creates the file if needed. Most database commands default to `substrata.sqlite3`, while `fetch` requires an explicit database path.

## Policies

```sh
substrata policy validate examples/spend-policy.json
substrata policy intersect organization.json task.json > effective.json
substrata policy evaluate examples/spend-policy.json \
  --recipient GRECIPIENT --amount 0.01 \
  --network stellar_testnet --asset-code XLM \
  --request-id inspect-001 --database payments.sqlite3
```

`evaluate` reads existing budget totals and reports a policy decision without signing a payment. Match `--recipient`, `--origin`, and asset identity to your policy. Issued and contract assets accept `--asset-issuer` and `--asset-contract-id`. See [spend policies](policies.md).

## Ledger inspection and recovery

```sh
substrata ledger list --database payments.sqlite3 --status uncertain
substrata ledger show --database payments.sqlite3 request-001
substrata ledger totals --database payments.sqlite3 --currency stellar_testnet:XLM
substrata ledger export --database payments.sqlite3 > ledger.jsonl
```

`list` accepts `--currency`, `--status`, `--limit`, and `--offset`. `totals` accepts `--day YYYY-MM-DD` to inspect a specific UTC day. `export` streams newline-delimited JSON.

Reconcile a reservation only after independently checking its outcome:

```sh
substrata ledger reconcile-settled --database payments.sqlite3 \
  request-001 --settlement-id confirmed-transaction-hash
substrata ledger reconcile-failed --database payments.sqlite3 \
  request-002 --reason "Authorization expired; transfer did not execute"
```

These commands update local accounting. They cannot cancel an on-chain transaction or revoke an authorization. Read [ledger and recovery](ledger.md) before releasing budget.

## Audit records

```sh
substrata audit verify --database payments.sqlite3
substrata audit list --database payments.sqlite3 --request-id request-001
substrata audit export --database payments.sqlite3 > audit.jsonl
```

`verify` checks the append-only hash chain. `list` supports `--limit` and `--offset`; `list` and `export` support `--request-id`.

## Inspect x402 data

```sh
substrata protocol inspect required --file challenge.json
substrata protocol inspect signature --format header --file payment-header.txt
substrata protocol inspect response --file receipt.json
```

Omit `--file` to read stdin. `--format header` expects an encoded HTTP payment header; the default expects JSON. Signed payload details are redacted unless you explicitly pass `--show-payload`.

## Fetch a paid testnet resource

```sh
substrata fetch http://127.0.0.1:8091/paid \
  --policy policy.json --database payments.sqlite3 \
  --request-id fetch-001 --rpc-url https://soroban-testnet.stellar.org
```

This command requires `.[stellar]`, a funded Stellar testnet account, a matching policy, and a compatible paid provider. Set the seed in `SUBSTRATA_STELLAR_SECRET_SEED` through your secret manager or shell environment. Pass a different environment variable name with `--secret-env-var`; never pass the seed itself as a command argument.

See [Stellar signing](stellar.md) for setup and [testnet settlement](testnet-settlement.md) for the complete test procedure.
