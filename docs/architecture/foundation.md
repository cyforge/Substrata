# Package Foundation Notes

This note captures the current package shape after the initial Substrata
foundation work landed.

## Entrypoints

- `python -m substrata` runs [substrata/__main__.py](../../substrata/__main__.py)
  and routes into the package CLI.
- `python main.py` stays available as a compatibility entrypoint and delegates
  to [substrata/cli.py](../../substrata/cli.py).
- `python examples/basic_app.py` exercises the package from an external example
  path instead of using ad hoc root-level script logic.

## Core Modules

- [substrata/constants.py](../../substrata/constants.py)
  defines shared environment and network values.
- [substrata/config.py](../../substrata/config.py)
  loads typed runtime settings from environment variables.
- [substrata/logging.py](../../substrata/logging.py)
  centralizes logging bootstrap behavior.
- [substrata/errors.py](../../substrata/errors.py)
  defines the base error taxonomy used by foundation code.
- [substrata/app.py](../../substrata/app.py)
  contains the minimal runnable application surface.

## Testing and Tooling

- [tests/test_foundations.py](../../tests/test_foundations.py)
  covers public exports, typed errors, and logging setup.
- [tests/test_config.py](../../tests/test_config.py)
  covers settings loading and validation behavior.
- [pyproject.toml](../../pyproject.toml)
  defines the package metadata, pytest configuration, and Ruff rules used in CI.

## Follow-on Architecture Notes

- Add ADRs when wallet, signer, policy, and x402 subsystems introduce
  cross-cutting decisions that should not live only in issue threads.
- Prefer one short note per subsystem or decision so later contributors can
  locate rationale without reading a large design document.
