import pytest

from substrata import load_settings
from substrata.config import SubstrataSettings
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


def test_empty_mapping_does_not_read_process_environment(monkeypatch):
    monkeypatch.setenv("SUBSTRATA_NETWORK", Network.STELLAR_MAINNET.value)

    settings = load_settings({})

    assert settings.network is DEFAULT_NETWORK


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


@pytest.mark.parametrize("level", ["TRACE", "VERBOSE", "INFO;DROP TABLE"])
def test_load_settings_rejects_unknown_log_level(level):
    with pytest.raises(ConfigurationError, match="log_level"):
        load_settings({"SUBSTRATA_LOG_LEVEL": level})


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/horizon",
        "https://user:password@example.test",
        "https://example.test?token=secret",
        "https://example.test#secret",
        "https://example.test:99999/horizon",
        "https://bad host/horizon",
        "https://[invalid/horizon",
        "//example.test/path",
    ],
)
def test_load_settings_rejects_unsafe_horizon_urls(url):
    with pytest.raises(ConfigurationError, match="horizon_url") as error:
        load_settings({"SUBSTRATA_HORIZON_URL": url})

    assert "password" not in str(error.value)
    assert "secret" not in str(error.value)


def test_direct_settings_constructor_validates_log_level():
    with pytest.raises(ConfigurationError, match="log_level"):
        SubstrataSettings(log_level="TRACE")


def test_direct_settings_constructor_validates_enum_types():
    with pytest.raises(ConfigurationError, match="network"):
        SubstrataSettings(network="stellar_testnet")


def test_direct_settings_constructor_derives_horizon_from_network():
    settings = SubstrataSettings(network=Network.STELLAR_MAINNET)

    assert settings.horizon_url == "https://horizon.stellar.org"


def test_invalid_environment_values_are_not_echoed():
    sentinel = "credential-leak-sentinel"

    for name in ("SUBSTRATA_NETWORK", "SUBSTRATA_DEBUG"):
        with pytest.raises(ConfigurationError) as error:
            load_settings({name: sentinel})

        assert sentinel not in str(error.value)
