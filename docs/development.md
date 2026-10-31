# Development

## Requirements

- Python 3.11 or newer
- Git

## Set up

Create and activate a virtual environment, then install the project and development tools:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Run checks

```bash
python -m pytest
python -m ruff check .
```

Build wheel and source archive:

```bash
python -m build
```

CI runs these checks on Python 3.11 through 3.14, then builds and invokes the installed console command from a wheel. A separate Python 3.11 job runs tests with the optional Stellar SDK installed.

## Run the CLI

```bash
python -m substrata --help
python -m substrata config show
python -m substrata doctor --init --database /tmp/substrata.sqlite3
```

`doctor` reports whether the optional Stellar SDK package is installed. It does not load signer keys or make network requests.

See [the quickstart](quickstart.md) for ledger and audit commands.

## Settings

Settings come from environment variables:

- `SUBSTRATA_ENVIRONMENT`: `local`, `test`, `staging`, or `production`
- `SUBSTRATA_NETWORK`: `stellar_testnet` or `stellar_mainnet`
- `SUBSTRATA_LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`
- `SUBSTRATA_HORIZON_URL`: HTTP(S) URL without credentials, query, or fragment
- `SUBSTRATA_SIGNER_BACKEND`: signer backend name
- `SUBSTRATA_DEFAULT_ASSET_CODE`: asset code, default `XLM`
- `SUBSTRATA_DEFAULT_ASSET_ISSUER`: required for issued assets
- `SUBSTRATA_DEBUG`: boolean (`true`/`false`, `yes`/`no`, `on`/`off`, or `1`/`0`)

Do not put signing keys, API credentials, or other secrets in these settings. Keep them in the signer or facilitator configuration that owns them.

## Contribution notes

- Keep each change focused and update its tests or docs.
- Run the checks above before proposing a change.
- See [contribution guidance](../CONTRIBUTING.md) for repository conventions.
