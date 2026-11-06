# Substrata

Substrata is a Python SDK for agent payments on Stellar. It brings together spend policy, a durable payment ledger, audit records, and x402 payment flows.

[Documentation](https://substrata-docs.vercel.app) · [Python API](https://substrata-docs.vercel.app/docs/reference/python-api) · [CLI reference](https://substrata-docs.vercel.app/docs/reference/cli)

## Install

The distribution is named `substrata-sdk`; Python imports and the CLI command
use `substrata`. Substrata supports Python 3.11 and newer.

PyPI publication is pending. Install from this repository in a virtual environment:

```bash
gh repo clone cyforge/Substrata
cd Substrata
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

For Stellar signing, run `python -m pip install -e ".[stellar]"`. See
[installation](docs/installation.md) for Windows setup and isolated CLI installs.

After the first PyPI release, install with `python -m pip install substrata-sdk`
or `python -m pip install "substrata-sdk[stellar]"` for the Stellar integration.
See [publishing releases](docs/publishing.md) for the first PyPI release and
trusted publisher setup.

## Quickstart

Run a signed paid request through a local HTTP server:

```bash
python -m substrata demo --database /tmp/substrata-demo.sqlite3
```

The demo needs no wallet, API key, or external service. It records a local payment
of 0.1 demo XLM, retains its ledger and audit records, and stops the server when done.
Its `demo:` receipt does not represent an on-chain transfer.

Show the CLI commands and validate local settings:

```bash
python -m substrata --help
python -m substrata config show
python -m substrata doctor --init --database /tmp/substrata.sqlite3
```

The CLI can validate policies, evaluate a request against recorded spend, fetch
paid resources on Stellar testnet or mainnet, inspect x402 messages, inspect
ledger and audit records, and reconcile a payment after an operator confirms its
outcome. Choose the fetch network with `--network stellar_testnet` or
`--network stellar_mainnet`, or set `SUBSTRATA_NETWORK`. See
[the quickstart](docs/quickstart.md) for command examples.

## Choose a payment network

`fetch` selects its network in this order: `--network`, `SUBSTRATA_NETWORK`, then
the `stellar_testnet` default. The RPC endpoint, funded account, provider offer,
and policy must match that network.

| Network | CLI, environment, and policy value | Python signer and x402 value |
| --- | --- | --- |
| Testnet | `stellar_testnet` | `stellar:testnet` |
| Mainnet | `stellar_mainnet` | `stellar:pubnet` |

After installing the Stellar extra, initialize a database and fetch from your
paid HTTPS provider. Replace the example URL and supply a policy for that provider:

```bash
substrata doctor --init --database payments.sqlite3
substrata fetch https://api.example.test/paid \
  --network stellar_testnet \
  --rpc-url https://soroban-testnet.stellar.org \
  --policy policy.json --database payments.sqlite3
```

Load `SUBSTRATA_STELLAR_SECRET_SEED` through your secret manager or shell
environment. `--secret-env-var` accepts a different variable name. Set
`--rpc-url` or `SUBSTRATA_STELLAR_RPC_URL` to the RPC endpoint for your network;
changing `--network` does not change that endpoint or grant policy permissions.

For mainnet, select `--network stellar_mainnet` and configure a mainnet RPC,
account, token contract, recipient, and policy. Python applications select the
same network with `StellarAuthSigner(..., network="stellar:pubnet", rpc_url=...)`.
See [network configuration](docs/cli.md#fetch-a-paid-resource) and
[Stellar signing](docs/stellar.md#select-a-network) for details.

## Documentation

The [documentation website](https://substrata-docs.vercel.app) covers installation,
buyer and provider flows, spend policies, payment recovery, and the Python API.
The [Markdown index](docs/index.md) links to the same guides in this repository.
To run the Vocs website locally:

```bash
npm --prefix website ci
npm --prefix website run dev
```

The website requires Node.js 24 LTS. See [website setup](website/README.md) for
build and hosting instructions.

## Development

Run the tests and lint checks:

```bash
python -m pip install -e ".[dev,stellar]"
python -m pytest
python -m ruff check .
```

See [development setup](docs/development.md) and the [documentation index](docs/index.md) for the rest of the guides.

## Project status

Substrata is pre-1.0. The base SDK has no required third-party Python dependencies;
the Stellar integration uses the optional `stellar` extra. Live payments require
an explicitly configured signer and compatible facilitator.

| Flow | Verified behavior |
| --- | --- |
| Local demo | Signed loopback requests with ledger and audit records; no on-chain transfer |
| Stellar testnet | Confirmed 0.001 test XLM settlement through the native asset contract |
| Stellar mainnet | Configurable SDK and CLI; authorization verified with offline RPC fixtures; live settlement not yet tested |

The [testnet settlement report](docs/testnet-settlement.md) includes the confirmed
transaction and independent balance checks. That run covered native XLM with a
locally hosted official facilitator; it did not exercise USDC or managed facilitators.

## Project links

- [Architecture notes](docs/architecture/README.md)
- [Contribution guide](CONTRIBUTING.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Support and versioning](docs/support.md)
- [Changelog](CHANGELOG.md)
