# Architecture Notes Index

This directory is the home for committed architecture notes, ADRs, and
implementation references that explain how the Substrata package is organized.

## Purpose

- Capture stable design decisions that should survive beyond one issue or pull request.
- Keep package-boundary notes discoverable for contributors who are new to the repo.
- Provide one place to link future ADRs and implementation references without
  duplicating the README.

## Current Notes

- Package foundation overview:
  [docs/architecture/foundation.md](/Users/Apple/dev/os/mann/Substrata/docs/architecture/foundation.md)
- Package foundation and entrypoints:
  [README.md](/Users/Apple/dev/os/mann/Substrata/README.md)
- Development workflow and local commands:
  [docs/development.md](/Users/Apple/dev/os/mann/Substrata/docs/development.md)

## Future Additions

- ADRs for payment orchestration, signer integrations, and policy evaluation
- Service-layer notes for wallet, provider, and x402 flows
- Reference docs for shared models, validation, and serialization helpers

## Maintenance Notes

- Prefer one note per decision or subsystem instead of a single long design document.
- Link to canonical implementation or API docs when a note depends on active code.
- Move issue-only context here only after it is rewritten into durable repository guidance.
