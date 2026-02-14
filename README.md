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

Run the current entry point:

```bash
python main.py
```

## Direction

Substrata is being designed around a few principles:

- **policy before autonomy**
- **stablecoin-first pricing**
- **protocol-native payments**
- **replaceable infrastructure for signing and settlement**
- **clear machine-readable failure semantics**
