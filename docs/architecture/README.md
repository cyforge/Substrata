# Architecture notes

Substrata separates typed payment data, policy checks, local accounting, protocol handling, and application entrypoints. These notes describe the current package boundaries.

## Current notes

- [Package layout and entrypoints](foundation.md)
- [Quickstart](../quickstart.md)
- [Development workflow](../development.md)
- [Project overview](../../README.md)

## Package boundaries

- `models` and `validation` define typed payment values and exact amount rules.
- `policy` evaluates a request against configured limits and observed spend.
- `storage` records pending, settled, failed, or uncertain payment state.
- `audit` records redacted events and verifies their integrity chain.
- `x402` defines protocol models, codecs, facilitator calls, and a local demo scheme.
- `cli` inspects configuration, policy, and local records.

Policy checks do not perform I/O. Storage owns reservations. The local demo scheme only works with loopback resources and does not send Stellar transactions.
