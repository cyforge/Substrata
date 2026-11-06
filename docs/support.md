# Support, Versioning, and Changelog Policy

## Project Stage

Substrata is currently a **pre-1.0** project. Expect rapid iteration while the SDK surface is still forming.

## Network support

The SDK and `substrata fetch` support Stellar testnet and mainnet configuration.
The CLI accepts `--network stellar_testnet` or `--network stellar_mainnet`, then
falls back to `SUBSTRATA_NETWORK` and finally testnet. Python signers use
`stellar:testnet` or `stellar:pubnet`. See the [CLI reference](cli.md#fetch-a-paid-resource)
for RPC, policy, and account setup.

Native XLM settlement has been [verified on testnet](testnet-settlement.md).
Mainnet authorization has offline test coverage; live mainnet settlement has not
been tested. The recorded testnet run did not cover USDC or managed facilitators.

## Versioning Policy

- Version numbers are semver-inspired.
- Breaking changes may still happen before 1.0, but they should be documented clearly.
- When behavior changes in a user-visible way, note it in the changelog.

## Support Policy

- The active default branch is the supported development line.
- Support questions and bug reports should use public repository issues unless the topic is security-sensitive.
- Maintainers may decline backports or branch support while the project is pre-1.0.

## Changelog Policy

- [CHANGELOG.md](../CHANGELOG.md) is the canonical changelog.
- Add entries under `Unreleased` until the next tagged release.
- Group entries into sections such as `Added`, `Changed`, `Fixed`, and `Removed`.

## Release Notes

- Keep release notes focused on contributor-visible changes.
- Mention follow-up migration guidance when a breaking change lands.
