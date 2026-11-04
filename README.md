# Substrata

Substrata is a Python SDK for agent payments on Stellar. It brings together spend policy, a durable payment ledger, audit records, and x402 payment flows.

## Install

The distribution is named `substrata-sdk`; Python imports and the CLI command
use `substrata`. Substrata supports Python 3.11 and newer.

PyPI publication is pending. Follow [installation](docs/installation.md) to clone
the repository and create a virtual environment, then install from that checkout:

```bash
python -m pip install -e ".[dev]"
```

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
python -m pytest
python -m ruff check .
```

See [development setup](docs/development.md) and the [documentation index](docs/index.md) for the rest of the guides.

## Project status

Substrata includes local policy, ledger, audit, x402, client, and provider components. The `substrata-demo` payment scheme is for loopback development only. It does not send Stellar transactions. Production Stellar signing and live settlement require an explicitly configured signer and facilitator.

## Project links

- [Architecture notes](docs/architecture/README.md)
- [Contribution guide](CONTRIBUTING.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Support and versioning](docs/support.md)
- [Changelog](CHANGELOG.md)
