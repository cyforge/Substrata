"""Shared constants used across the Substrata package."""

from __future__ import annotations

from enum import StrEnum


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Network(StrEnum):
    STELLAR_TESTNET = "stellar_testnet"
    STELLAR_MAINNET = "stellar_mainnet"


DEFAULT_ENVIRONMENT = Environment.LOCAL
DEFAULT_NETWORK = Network.STELLAR_TESTNET
DEFAULT_LOG_LEVEL = "INFO"


def supported_environments() -> tuple[Environment, ...]:
    return tuple(Environment)


def supported_networks() -> tuple[Network, ...]:
    return tuple(Network)


__all__ = [
    "DEFAULT_ENVIRONMENT",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_NETWORK",
    "Environment",
    "Network",
    "supported_environments",
    "supported_networks",
]
