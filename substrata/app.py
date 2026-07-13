"""Compatibility exports for the minimal application wiring."""

from .services.runtime import (
    ExampleApplication,
    create_example_application,
    run_example_application,
)

__all__ = [
    "ExampleApplication",
    "create_example_application",
    "run_example_application",
]
