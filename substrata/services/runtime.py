"""Minimal runtime service used by package entrypoints and examples."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import SubstrataSettings, load_settings
from ..logging import configure_logging


@dataclass(slots=True)
class ExampleApplication:
    settings: SubstrataSettings

    def startup_summary(self) -> str:
        return (
            "Substrata foundation ready "
            "("
            f"environment={self.settings.environment.value}, "
            f"network={self.settings.network.value}, "
            f"signer={self.settings.signer_backend}, "
            f"default_asset={self.settings.default_asset_reference}"
            ")"
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
