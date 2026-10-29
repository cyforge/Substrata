# Quickstart

## Install

Use Python 3.11 or newer. Install Substrata in a virtual environment:

```bash
python -m pip install -e ".[dev]"
```

## Inspect settings

The CLI reads settings from `SUBSTRATA_` environment variables. `config show` prints the validated non-secret settings. Use `doctor --init` to create the local SQLite database. Run `doctor` without `--init` to check an existing database.

```bash
python -m substrata config show
python -m substrata doctor --init --database /tmp/substrata.sqlite3
```

## Inspect local records

Ledger and audit read commands require an initialized database. The ledger stores payment amounts and their current state. Audit records include redacted event data and an integrity chain.

```bash
python -m substrata ledger list --database /tmp/substrata.sqlite3
python -m substrata ledger show REQUEST_ID --database /tmp/substrata.sqlite3
python -m substrata ledger totals --currency stellar_testnet:XLM --database /tmp/substrata.sqlite3
python -m substrata ledger export --database /tmp/substrata.sqlite3
python -m substrata audit verify --database /tmp/substrata.sqlite3
python -m substrata audit export --database /tmp/substrata.sqlite3
```

The CLI prints ledger and audit exports as JSON Lines. `ledger show` reports one request, including an uncertain hold. `ledger totals` reports active spend by status and selected UTC day. Add `--currency`, `--status`, or `--request-id` to filter records. Use `--limit` and `--offset` to page through list results.

## Check a spend policy

The repository includes [a sample policy](../examples/spend-policy.json). Validate its structure, then evaluate a payment against the current ledger totals:

```bash
python -m substrata policy validate examples/spend-policy.json
python -m substrata doctor --init --database /tmp/substrata.sqlite3
python -m substrata policy evaluate examples/spend-policy.json \
  --recipient GRECIPIENT \
  --amount 1.25 \
  --asset-code XLM \
  --network stellar_testnet \
  --request-id request-123 \
  --database /tmp/substrata.sqlite3
```

Evaluation is read only. It does not reserve budget or authorize a later payment by itself. The caller must reserve spend through the payment workflow before signing.

## Inspect x402 data

Decode a payment challenge from a JSON file. The same command accepts bounded input from stdin or a base64 HTTP header:

```bash
python -m substrata protocol inspect required --file examples/payment-required.json
```

Signature payloads are redacted in output by default. Add `--show-payload` only when you need to inspect signed data and can safely handle its contents.

## Reconcile a payment

Reconcile only after checking the payment result with the responsible facilitator or settlement system. Mark a payment settled with its transaction identifier:

```bash
python -m substrata ledger reconcile-settled REQUEST_ID \
  --settlement-id TRANSACTION_ID \
  --database /tmp/substrata.sqlite3
```

Release a reservation after confirming it failed:

```bash
python -m substrata ledger reconcile-failed REQUEST_ID \
  --reason "Settlement confirmed failed" \
  --database /tmp/substrata.sqlite3
```

These commands change the local ledger and append a redacted audit event. They do not submit, retry, or verify a Stellar transaction.
