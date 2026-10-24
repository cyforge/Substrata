# Quickstart

## Install

Use Python 3.11 or newer. Install Substrata in a virtual environment:

```bash
python -m pip install -e ".[dev]"
```

## Inspect settings

The CLI reads settings from `SUBSTRATA_` environment variables. `config show` prints the validated non-secret settings. `doctor` checks the configuration and opens the local SQLite database at the selected path.

```bash
python -m substrata config show
python -m substrata doctor --database /tmp/substrata.sqlite3
```

## Inspect local records

Ledger and audit commands create the selected SQLite database if it does not exist. The ledger stores payment amounts and their current state. Audit records include redacted event data and an integrity chain.

```bash
python -m substrata ledger list --database /tmp/substrata.sqlite3
python -m substrata ledger export --database /tmp/substrata.sqlite3
python -m substrata audit verify --database /tmp/substrata.sqlite3
python -m substrata audit export --database /tmp/substrata.sqlite3
```

The CLI prints ledger and audit exports as JSON Lines. Add `--currency`, `--status`, or `--request-id` to filter records. Use `--limit` and `--offset` to page through list results.

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

These commands change the local ledger only. They do not submit, retry, or verify a Stellar transaction.
