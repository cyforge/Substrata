"""CLI entrypoint for the minimal Substrata package."""

from __future__ import annotations

from .app import run_example_application


def main() -> int:
    run_example_application()
    return 0
