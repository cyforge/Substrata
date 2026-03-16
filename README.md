# Substrata

Substrata is a Python-first **AgentPay protocol and SDK** for autonomous software agents.
It is being built to let agents hold value, enforce spend policy, and pay for APIs, data, tools, and compute on **Stellar** using **x402**.

## Project Goal

The project aims to make machine-to-machine payments feel like normal application infrastructure.
Instead of relying on API keys, subscriptions, or manual billing setup, an agent should be able to:

1. discover or call a payable service,
2. evaluate whether the spend is allowed,
3. settle the payment programmatically,
4. and continue its task with a structured result.

## Planned Product Surface

The repository will evolve toward a few core building blocks:

- a **Python SDK** for wallet orchestration, policy checks, and x402 payment handling
- **signer abstractions** for local development and production custody
- **reference middleware** for providers who want to expose paid endpoints
- **telemetry and audit primitives** for operators
- later support for **MCP**, identity, and reputation-oriented trust layers

## Current Status

This repository is in its initial setup phase.
The current codebase is intentionally minimal and serves as a clean starting point for the SDK and reference implementation work.

## Development

Requirements:

- Python 3.11+

Install the project and development tools:

```bash
python -m pip install -e ".[dev]"
```

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

Project docs index:

- [docs/index.md](/Users/Apple/dev/os/mann/Substrata/docs/index.md)

## Open Source Standards

- Contribution guide: [CONTRIBUTING.md](/Users/Apple/dev/os/mann/Substrata/CONTRIBUTING.md)
- Code of conduct: [CODE_OF_CONDUCT.md](/Users/Apple/dev/os/mann/Substrata/CODE_OF_CONDUCT.md)
- Security policy: [SECURITY.md](/Users/Apple/dev/os/mann/Substrata/SECURITY.md)
- Support and versioning policy: [docs/support.md](/Users/Apple/dev/os/mann/Substrata/docs/support.md)
- Maintainer and triage guidance: [docs/maintainers.md](/Users/Apple/dev/os/mann/Substrata/docs/maintainers.md) and [docs/triage.md](/Users/Apple/dev/os/mann/Substrata/docs/triage.md)
- Changelog: [CHANGELOG.md](/Users/Apple/dev/os/mann/Substrata/CHANGELOG.md)

## Direction

Substrata is being designed around a few principles:

- **policy before autonomy**
- **stablecoin-first pricing**
- **protocol-native payments**
- **replaceable infrastructure for signing and settlement**
- **clear machine-readable failure semantics**
