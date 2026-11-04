# Installation

Substrata requires Python 3.11 or newer. The base SDK has no required third-party
Python dependencies. Stellar signing and transaction parsing use an optional extra.

## Install from the repository

Run these commands from a terminal with Git and Python available:

```bash
git clone https://github.com/cyforge/Substrata.git
cd Substrata
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

On Windows, activate the environment with `.venv\Scripts\activate` instead.

Check the installed entrypoint:

```bash
substrata --version
substrata --help
```

These instructions install the code from this repository. They do not assume a
published package on PyPI.

## Run a local payment

```bash
substrata demo --database substrata-demo.sqlite3
substrata ledger list --database substrata-demo.sqlite3
substrata audit verify --database substrata-demo.sqlite3
```

The demo starts a local HTTP server, negotiates a payment, and returns protected
content. Its HMAC authorization and `demo:` receipt are local test data. The
command uses no external accounts and does not transfer Stellar funds.

## Enable Stellar signing

```bash
python -m pip install -e ".[stellar]"
```

This installs `stellar-sdk>=17,<18`. `StellarAuthSigner` supports x402 v2 `exact`
payments for SEP-41 contracts that report seven decimals. It needs a wallet seed,
a Soroban RPC endpoint, and a compatible payment provider. See the
[Stellar signer guide](stellar.md) and [live testnet evidence](testnet-settlement.md).

## Install development tools

```bash
python -m pip install -e ".[dev,stellar]"
python -m pytest
python -m ruff check .
```

The [development guide](development.md) lists settings, package checks, and the
documentation website workflow.
