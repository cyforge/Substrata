# Substrata

Substrata is a Python SDK for agent payments on Stellar. It brings together spend policy, a durable payment ledger, audit records, and x402 payment flows.

## Install

Substrata supports Python 3.11 and newer. Install the SDK and development tools in a virtual environment:

```bash
python -m pip install -e ".[dev]"
```

## Quickstart

Show the CLI commands and validate local settings:

```bash
python -m substrata --help
python -m substrata config show
python -m substrata doctor --database /tmp/substrata.sqlite3
```

The CLI can list or export ledger and audit records, check audit integrity, and reconcile a recorded payment after an operator confirms its outcome. See [the quickstart](docs/quickstart.md) for command examples.

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
