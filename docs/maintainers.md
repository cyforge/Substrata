# Maintainer Guide

## Responsibilities

Maintainers are responsible for:

- keeping issues actionable and correctly labeled
- reviewing pull requests with clear feedback
- maintaining the changelog and release notes
- handling support and security routing

## Issue Grooming

- Review new issues regularly.
- Apply the label taxonomy from [docs/triage.md](/Users/Apple/dev/os/mann/Substrata/docs/triage.md).
- Close duplicates or convert vague issues into concrete follow-up tasks.
- Split oversized issues before assigning them to contributors.

## Pull Request Review

- Prefer small, scoped pull requests.
- Ask for tests when behavior changes.
- Ask for docs updates when contributor workflows or public interfaces change.
- Call out missing follow-up work explicitly instead of assuming it will be remembered later.

## Release Basics

- Update [CHANGELOG.md](/Users/Apple/dev/os/mann/Substrata/CHANGELOG.md) before cutting a release.
- Keep release notes short and user-facing.
- Record breaking changes clearly while the project is pre-1.0.

## Support and Security

- Route general support through public repository issues.
- Route vulnerabilities through [SECURITY.md](/Users/Apple/dev/os/mann/Substrata/SECURITY.md).
- If a report is sensitive, avoid discussing details in public until a fix is available.
