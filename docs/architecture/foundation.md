# Package Foundation Notes

This note captures the current package shape after the initial Substrata
foundation work landed.

## Entrypoints

- `python -m substrata` runs [substrata/__main__.py](/Users/Apple/dev/os/mann/Substrata/substrata/__main__.py)
  and routes into the package CLI.
- `python main.py` stays available as a compatibility entrypoint and delegates
  to [substrata/cli.py](/Users/Apple/dev/os/mann/Substrata/substrata/cli.py).
- `python examples/basic_app.py` exercises the package from an external example
  path instead of using ad hoc root-level script logic.

## Core Modules

- [substrata/constants.py](/Users/Apple/dev/os/mann/Substrata/substrata/constants.py)
  defines shared environment and network values.
- [substrata/config.py](/Users/Apple/dev/os/mann/Substrata/substrata/config.py)
  loads typed runtime settings from environment variables.
- [substrata/logging.py](/Users/Apple/dev/os/mann/Substrata/substrata/logging.py)
  centralizes logging bootstrap behavior.
- [substrata/errors.py](/Users/Apple/dev/os/mann/Substrata/substrata/errors.py)
  defines the base error taxonomy used by foundation code.
- [substrata/app.py](/Users/Apple/dev/os/mann/Substrata/substrata/app.py)
  contains the minimal runnable application surface.

## Testing and Tooling

- [tests/test_foundations.py](/Users/Apple/dev/os/mann/Substrata/tests/test_foundations.py)
  covers public exports, typed errors, and logging setup.
- [tests/test_config.py](/Users/Apple/dev/os/mann/Substrata/tests/test_config.py)
  covers settings loading and validation behavior.
- [pyproject.toml](/Users/Apple/dev/os/mann/Substrata/pyproject.toml)
  defines the package metadata, pytest configuration, and Ruff rules used in CI.

## Follow-on Architecture Notes

- Add ADRs when wallet, signer, policy, and x402 subsystems introduce
  cross-cutting decisions that should not live only in issue threads.
- Prefer one short note per subsystem or decision so later contributors can
  locate rationale without reading a large design document.
