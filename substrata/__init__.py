"""Substrata package exports."""

from importlib import metadata

from .app import create_example_application, run_example_application
from .config import SubstrataSettings, load_settings
from .constants import DEFAULT_ENVIRONMENT, DEFAULT_LOG_LEVEL, DEFAULT_NETWORK, Environment, Network
from .errors import ConfigurationError, IntegrationError, SubstrataError, ValidationError

try:
    __version__ = metadata.version("substrata")
except metadata.PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = [
    "ConfigurationError",
    "DEFAULT_ENVIRONMENT",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_NETWORK",
    "Environment",
    "IntegrationError",
    "Network",
    "SubstrataSettings",
    "SubstrataError",
    "ValidationError",
    "__version__",
    "create_example_application",
    "load_settings",
    "run_example_application",
]
