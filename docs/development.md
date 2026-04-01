# Development Guide

## Prerequisites

- Python 3.11+
- `pip`

## Setup

```bash
python -m pip install -e ".[dev]"
```

## Common Commands

Run the package entry point:

```bash
python -m substrata
```

Run the compatibility entry point:

```bash
python main.py
```

Run the minimal example app:

```bash
python examples/basic_app.py
```

Run tests:

```bash
pytest
```

Run lint checks:

```bash
ruff check .
```

## Environment Variables

The current development surface supports these environment variables:

- `SUBSTRATA_ENVIRONMENT`
- `SUBSTRATA_NETWORK`
- `SUBSTRATA_LOG_LEVEL`
- `SUBSTRATA_HORIZON_URL`
- `SUBSTRATA_SIGNER_BACKEND`
- `SUBSTRATA_DEFAULT_ASSET_CODE`
- `SUBSTRATA_DEFAULT_ASSET_ISSUER`
- `SUBSTRATA_DEBUG`

## Contributor Notes

- Keep changes scoped to one issue where practical.
- Prefer updating tests alongside behavior changes.
- If you add a new tool or workflow dependency, document it here.
