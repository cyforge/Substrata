"""Minimal application wiring used by the package entrypoint and examples."""

from __future__ import annotations

from dataclasses import dataclass

from .config import SubstrataSettings, load_settings
from .logging import configure_logging


@dataclass(slots=True)
class ExampleApplication:
    settings: SubstrataSettings

    def startup_summary(self) -> str:
        return (
            "Substrata foundation ready "
            f"(environment={self.settings.environment.value}, network={self.settings.network.value})"
        )

    def run(self) -> str:
        logger = configure_logging(self.settings.log_level)
        summary = self.startup_summary()
        logger.info(summary)
        return summary


def create_example_application(settings: SubstrataSettings | None = None) -> ExampleApplication:
    return ExampleApplication(settings=settings or load_settings())


def run_example_application(settings: SubstrataSettings | None = None) -> str:
    return create_example_application(settings).run()
