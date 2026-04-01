"""Typed configuration for local development and future SDK services."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .constants import DEFAULT_ENVIRONMENT, DEFAULT_LOG_LEVEL, DEFAULT_NETWORK, Environment, Network
from .errors import ConfigurationError

DEFAULT_HORIZON_URLS = {
    Network.STELLAR_TESTNET: "https://horizon-testnet.stellar.org",
    Network.STELLAR_MAINNET: "https://horizon.stellar.org",
}
DEFAULT_SIGNER_BACKEND = "local"
DEFAULT_ASSET_CODE = "XLM"


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


def _parse_required_text(name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ConfigurationError(
            f"Invalid {name!s} value. Expected a non-empty string.",
            details={"field": name, "value": value},
        )

    return normalized


def _parse_optional_text(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


@dataclass(frozen=True, slots=True)
class SubstrataSettings:
    environment: Environment = DEFAULT_ENVIRONMENT
    network: Network = DEFAULT_NETWORK
    log_level: str = DEFAULT_LOG_LEVEL
    horizon_url: str = DEFAULT_HORIZON_URLS[DEFAULT_NETWORK]
    signer_backend: str = DEFAULT_SIGNER_BACKEND
    default_asset_code: str = DEFAULT_ASSET_CODE
    default_asset_issuer: str | None = None
    debug: bool = False

    def __post_init__(self) -> None:
        normalized_log_level = _parse_required_text("log_level", self.log_level).upper()
        normalized_horizon_url = _parse_required_text("horizon_url", self.horizon_url)
        normalized_signer_backend = _parse_required_text(
            "signer_backend",
            self.signer_backend,
        ).lower()
        normalized_asset_code = _parse_required_text(
            "default_asset_code",
            self.default_asset_code,
        ).upper()
        normalized_asset_issuer = _parse_optional_text(self.default_asset_issuer)

        if len(normalized_asset_code) > 12 or not normalized_asset_code.isalnum():
            raise ConfigurationError(
                "Invalid default_asset_code. Expected 1-12 alphanumeric characters.",
                details={"field": "default_asset_code", "value": self.default_asset_code},
            )

        if normalized_asset_code == "XLM" and normalized_asset_issuer is not None:
            raise ConfigurationError(
                "Native asset XLM must not define a default_asset_issuer.",
                details={"field": "default_asset_issuer", "value": self.default_asset_issuer},
            )

        if normalized_asset_code != "XLM" and normalized_asset_issuer is None:
            raise ConfigurationError(
                "Issued default assets must define a default_asset_issuer.",
                details={"field": "default_asset_issuer", "value": self.default_asset_issuer},
            )

        object.__setattr__(self, "log_level", normalized_log_level)
        object.__setattr__(self, "horizon_url", normalized_horizon_url)
        object.__setattr__(self, "signer_backend", normalized_signer_backend)
        object.__setattr__(self, "default_asset_code", normalized_asset_code)
        object.__setattr__(self, "default_asset_issuer", normalized_asset_issuer)

    @property
    def default_asset_reference(self) -> str:
        if self.default_asset_issuer is None:
            return self.default_asset_code

        return f"{self.default_asset_code}:{self.default_asset_issuer}"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "SubstrataSettings":
        source = environ or os.environ

        environment = _parse_enum(
            "SUBSTRATA_ENVIRONMENT",
            source.get("SUBSTRATA_ENVIRONMENT", DEFAULT_ENVIRONMENT.value),
            Environment,
        )
        network = _parse_enum(
            "SUBSTRATA_NETWORK",
            source.get("SUBSTRATA_NETWORK", DEFAULT_NETWORK.value),
            Network,
        )

        log_level_value = source.get("SUBSTRATA_LOG_LEVEL", DEFAULT_LOG_LEVEL)
        horizon_url_value = source.get("SUBSTRATA_HORIZON_URL", DEFAULT_HORIZON_URLS[network])
        signer_backend_value = source.get("SUBSTRATA_SIGNER_BACKEND", DEFAULT_SIGNER_BACKEND)
        default_asset_code_value = source.get("SUBSTRATA_DEFAULT_ASSET_CODE", DEFAULT_ASSET_CODE)
        default_asset_issuer_value = source.get("SUBSTRATA_DEFAULT_ASSET_ISSUER")
        debug_value = source.get("SUBSTRATA_DEBUG", "false")

        return cls(
            environment=environment,
            network=network,
            log_level=log_level_value,
            horizon_url=horizon_url_value,
            signer_backend=signer_backend_value,
            default_asset_code=default_asset_code_value,
            default_asset_issuer=default_asset_issuer_value,
            debug=_parse_bool("SUBSTRATA_DEBUG", debug_value),
        )


def load_settings(environ: Mapping[str, str] | None = None) -> SubstrataSettings:
    """Load project settings from the environment or a provided mapping."""

    return SubstrataSettings.from_env(environ)
