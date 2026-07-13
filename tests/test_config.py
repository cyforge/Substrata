import pytest

from substrata import load_settings
from substrata.constants import DEFAULT_ENVIRONMENT, DEFAULT_NETWORK, Environment, Network
from substrata.errors import ConfigurationError


def test_load_settings_uses_defaults_when_env_is_missing():
    settings = load_settings({})

    assert settings.environment is DEFAULT_ENVIRONMENT
    assert settings.network is DEFAULT_NETWORK
    assert settings.log_level == "INFO"
    assert settings.horizon_url == "https://horizon-testnet.stellar.org"
    assert settings.signer_backend == "local"
    assert settings.default_asset_reference == "XLM"
    assert settings.debug is False


def test_load_settings_parses_valid_environment_values():
    settings = load_settings(
        {
            "SUBSTRATA_ENVIRONMENT": Environment.STAGING.value,
            "SUBSTRATA_NETWORK": Network.STELLAR_MAINNET.value,
            "SUBSTRATA_LOG_LEVEL": "debug",
            "SUBSTRATA_HORIZON_URL": "https://rpc.example.test/horizon",
            "SUBSTRATA_SIGNER_BACKEND": "kms",
            "SUBSTRATA_DEFAULT_ASSET_CODE": "usdc",
            "SUBSTRATA_DEFAULT_ASSET_ISSUER": "issuer-account",
            "SUBSTRATA_DEBUG": "true",
        }
    )

    assert settings.environment is Environment.STAGING
    assert settings.network is Network.STELLAR_MAINNET
    assert settings.log_level == "DEBUG"
    assert settings.horizon_url == "https://rpc.example.test/horizon"
    assert settings.signer_backend == "kms"
    assert settings.default_asset_reference == "USDC:issuer-account"
    assert settings.debug is True


def test_load_settings_rejects_invalid_environment():
    with pytest.raises(ConfigurationError):
        load_settings({"SUBSTRATA_ENVIRONMENT": "unknown"})


def test_load_settings_rejects_invalid_debug_flag():
    with pytest.raises(ConfigurationError):
        load_settings({"SUBSTRATA_DEBUG": "maybe"})


def test_load_settings_rejects_issued_asset_without_issuer():
    with pytest.raises(ConfigurationError):
        load_settings({"SUBSTRATA_DEFAULT_ASSET_CODE": "USDC"})
