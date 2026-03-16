"""Typed configuration for local development and future SDK services."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .constants import DEFAULT_ENVIRONMENT, DEFAULT_LOG_LEVEL, DEFAULT_NETWORK, Environment, Network
from .errors import ConfigurationError


def _parse_enum(name: str, value: str, enum_type: type[Environment] | type[Network]):
    try:
        return enum_type(value)
    except ValueError as exc:
        valid_values = ", ".join(item.value for item in enum_type)
        message = f"Invalid {name!s} '{value}'. Expected one of: {valid_values}."
        raise ConfigurationError(message, details={"field": name, "value": value}) from exc


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    truthy = {"1", "true", "yes", "on"}
    falsy = {"0", "false", "no", "off"}

    if normalized in truthy:
        return True
    if normalized in falsy:
        return False

    raise ConfigurationError(
        f"Invalid {name!s} '{value}'. Expected a boolean-like value.",
        details={"field": name, "value": value},
    )


@dataclass(frozen=True, slots=True)
class SubstrataSettings:
    environment: Environment = DEFAULT_ENVIRONMENT
    network: Network = DEFAULT_NETWORK
    log_level: str = DEFAULT_LOG_LEVEL
    debug: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "SubstrataSettings":
        source = environ or os.environ

        environment_value = source.get("SUBSTRATA_ENVIRONMENT", DEFAULT_ENVIRONMENT.value)
        network_value = source.get("SUBSTRATA_NETWORK", DEFAULT_NETWORK.value)
        log_level_value = source.get("SUBSTRATA_LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
        debug_value = source.get("SUBSTRATA_DEBUG", "false")

        return cls(
            environment=_parse_enum("SUBSTRATA_ENVIRONMENT", environment_value, Environment),
            network=_parse_enum("SUBSTRATA_NETWORK", network_value, Network),
            log_level=log_level_value,
            debug=_parse_bool("SUBSTRATA_DEBUG", debug_value),
        )


def load_settings(environ: Mapping[str, str] | None = None) -> SubstrataSettings:
    """Load project settings from the environment or a provided mapping."""

    return SubstrataSettings.from_env(environ)
