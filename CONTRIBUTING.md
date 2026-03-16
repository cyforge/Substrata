# Contributing to Substrata

Thanks for contributing to Substrata.

## Before You Start

- Read the [README.md](/Users/Apple/dev/os/mann/Substrata/README.md) for project context.
- Follow the setup steps in [docs/development.md](/Users/Apple/dev/os/mann/Substrata/docs/development.md).
- Read the community expectations in [CODE_OF_CONDUCT.md](/Users/Apple/dev/os/mann/Substrata/CODE_OF_CONDUCT.md).
- Check [SECURITY.md](/Users/Apple/dev/os/mann/Substrata/SECURITY.md) before reporting vulnerabilities.

## Picking Work

- Prefer issues that already have enough context, scope, and acceptance criteria to be implemented directly.
- If you want to work on an issue, leave a short note so maintainers know it is being picked up.
- If an issue is blocked or underspecified, ask for clarification before starting implementation.
- If you are new to the repository, start with `fit/good-first-issue` or documentation tasks.

## Local Setup

Use the documented setup flow in [docs/development.md](/Users/Apple/dev/os/mann/Substrata/docs/development.md).

At a minimum, contributors should be able to run:

```bash
python -m pip install -e ".[dev]"
pytest
ruff check .
```

## Pull Request Expectations

- Keep changes scoped to the issue you are solving.
- Update tests when behavior changes.
- Update docs when the public development flow or contributor workflow changes.
- Explain what changed, how it was validated, and any follow-up work still needed.
- Prefer small, reviewable pull requests over large cross-cutting rewrites.

## Commit and Review Guidance

- Use clear commit messages.
- Reference the issue in the pull request description.
- Call out breaking or non-obvious decisions directly in the pull request body.
- If you add a new abstraction, explain why it is needed now.

## Documentation and Issue Quality

- Use plain language and define uncommon terms.
- Keep examples concrete and reproducible.
- Write acceptance criteria that a reviewer can verify directly.
- Avoid issue descriptions that depend on private or local-only planning documents.

## Questions and Support

- Use repository issues for contributor-facing questions unless maintainers request a different channel.
- For security-sensitive findings, follow [SECURITY.md](/Users/Apple/dev/os/mann/Substrata/SECURITY.md) instead of opening a public issue.
