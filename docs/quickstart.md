# Quickstart

## Install

The distribution is `substrata-sdk`; Python imports and the CLI command use
`substrata`. Use Python 3.11 or newer.

PyPI publication is pending. Follow [installation](installation.md) to clone the
repository and create a virtual environment. The examples below use files from
that checkout. Install the SDK and development tools with:

```bash
python -m pip install -e ".[dev]"
```

After the first PyPI release, `python -m pip install substrata-sdk` installs the
SDK and CLI. See [installation](installation.md) for the Stellar extra and
isolated CLI installs.

## Run a local payment

```bash
python -m substrata demo --database /tmp/substrata-demo.sqlite3
python -m substrata ledger list --database /tmp/substrata-demo.sqlite3
python -m substrata audit verify --database /tmp/substrata-demo.sqlite3
```

The demo starts a loopback HTTP server, receives an x402 challenge, checks spend
policy, reserves 0.1 demo XLM, signs a local HMAC proof, and retries the request.
The provider verifies the proof, claims it once, and returns paid content with a
`demo:` receipt. The server shuts down before the command exits. No external
account or Stellar transaction is involved. Repeated runs keep their records in
the same database and share its daily limit of 1 and total limit of 2 demo XLM.

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

Combine an organization policy with a task policy. The command prints their restrictive intersection and leaves both input files unchanged:

```bash
python -m substrata policy intersect \
  examples/spend-policy.json \
  examples/task-spend-policy.json
```

## Fetch a paid resource

`fetch` performs a GET with the Stellar authorization signer. Select testnet or
mainnet with `--network stellar_testnet` or `--network stellar_mainnet`. If the
flag is omitted, it reads `SUBSTRATA_NETWORK`, defaulting to `stellar_testnet`.

It needs a Soroban RPC URL for that network and a secret seed stored in an
environment variable. The default seed variable is `SUBSTRATA_STELLAR_SECRET_SEED`;
`--secret-env-var` accepts a variable name when your secret manager uses another
name. The command requires an initialized database and policy file. It prints
the response body to stdout and request/payment details to stderr.

```bash
python -m substrata fetch https://api.example.test/paid \
  --network stellar_testnet \
  --policy examples/spend-policy.json \
  --database /tmp/substrata.sqlite3 \
  --request-id paid-request-123
```

Load the seed through your environment's secret manager before running the
command. Never pass a seed as a command-line argument. The selected network,
RPC endpoint, account, provider offer, and policy must agree. See the
[CLI reference](cli.md#fetch-a-paid-resource) for a mainnet example and precedence rules.

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
