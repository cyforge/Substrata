# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows a pre-1.0, semver-inspired versioning policy described
in [docs/support.md](docs/support.md).

## [Unreleased]

### Added

- CLI commands for validated configuration, ledger and audit inspection, and manual ledger reconciliation.
- CLI policy intersection that combines organization and task limits without changing either file.
- Ledger lookup and per-currency spend totals for reconciliation.
- Bounded x402 message inspection with signed payment payloads redacted by default.
- Python 3.11–3.14 CI coverage with wheel and source archive build checks.
- A quickstart and CLI documentation.
- Initial repository scaffolding and package foundation for the Substrata SDK.
- Architecture notes index for ADRs and implementation references.
- Package subdirectories for future models, services, and integrations.
- Expanded runtime settings for horizon URL, signer backend, and default asset configuration.
- Explicit exports and tests for shared constants, foundation errors, logging, and example wiring.
