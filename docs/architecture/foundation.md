# Package layout

## Entrypoints

- `python -m substrata` runs [the package entrypoint](../../substrata/__main__.py).
- `python main.py` keeps the compatibility entrypoint and delegates to [the CLI](../../substrata/cli.py).
- `python examples/basic_app.py` runs the package's basic application example.

`substrata.cli.main()` with no arguments keeps its original programmatic behavior. The module entrypoint forwards command line arguments through `entrypoint()`.

## Runtime configuration

- [Settings](../../substrata/config.py) validate environment, network, log level, Horizon URL, and default asset.
- [Constants](../../substrata/constants.py) define supported environments and Stellar networks.
- [Logging](../../substrata/logging.py) configures application logging.
- [Errors](../../substrata/errors.py) defines typed package errors.

Runtime settings hold no signing keys or facilitator credentials.

## Payment models and policy

- [Domain models](../../substrata/models/domain.py) define assets, payment requests, results, and signing values.
- [Amount validation](../../substrata/validation.py) validates amounts and converts Stellar's seven decimal places to integer units.
- [Spend policy](../../substrata/policy.py) checks network, recipient, asset, origin, payment limits, and recorded spend without network access.

Policy evaluation does not reserve a payment. The payment workflow must reserve funds before signing.

## Local records and x402

- [Ledger](../../substrata/storage/ledger.py) records budget reservations and settlement states in SQLite.
- [Audit log](../../substrata/audit.py) stores redacted events and verifies an append-only hash chain.
- [x402 models and codecs](../../substrata/x402) define bounded protocol data handling.
- [Local demo facilitator](../../substrata/x402/demo.py) verifies and settles a loopback-only demonstration scheme. It does not broadcast to Stellar.

## Tests and builds

- [Tests](../../tests) cover package behavior and protocol boundaries.
- [Project metadata](../../pyproject.toml) defines dependencies, build settings, pytest, and Ruff configuration.
- [CI workflow](../../.github/workflows/ci.yml) runs tests on supported Python versions and checks the built wheel.
