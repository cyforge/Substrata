"""Substrata package exports."""

from importlib import metadata

from .config import SubstrataSettings, load_settings
from .constants import DEFAULT_ENVIRONMENT, DEFAULT_LOG_LEVEL, DEFAULT_NETWORK, Environment, Network

try:
    __version__ = metadata.version("substrata")
except metadata.PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = [
    "DEFAULT_ENVIRONMENT",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_NETWORK",
    "Environment",
    "Network",
    "SubstrataSettings",
    "__version__",
    "load_settings",
]
