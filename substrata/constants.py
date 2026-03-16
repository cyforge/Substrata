"""Shared constants used across the Substrata package."""

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
