import logging

from substrata import Environment, Network, SubstrataSettings, __version__
from substrata.errors import ConfigurationError, IntegrationError, SubstrataError, ValidationError
from substrata.logging import configure_logging


def test_public_exports_are_available():
    settings = SubstrataSettings()

    assert settings.environment is Environment.LOCAL
    assert settings.network is Network.STELLAR_TESTNET
    assert settings.default_asset_reference == "XLM"
    assert __version__ == "0.1.0"


def test_typed_errors_inherit_from_base_error():
    base_error = SubstrataError("base")

    assert isinstance(ConfigurationError("config"), SubstrataError)
    assert isinstance(ValidationError("validation"), SubstrataError)
    assert isinstance(IntegrationError("integration"), SubstrataError)
    assert str(base_error) == "base"


def test_configure_logging_sets_requested_level():
    logger = configure_logging("debug", logger_name="substrata.tests")

    assert logger.level == logging.DEBUG
