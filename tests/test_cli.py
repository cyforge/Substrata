from substrata.app import create_example_application
from substrata.cli import main
from substrata.config import SubstrataSettings
from substrata.constants import Environment, Network


def test_cli_main_returns_success(monkeypatch):
    monkeypatch.setenv("SUBSTRATA_ENVIRONMENT", Environment.TEST.value)
    monkeypatch.setenv("SUBSTRATA_NETWORK", Network.STELLAR_TESTNET.value)
    monkeypatch.setenv("SUBSTRATA_DEBUG", "false")

    assert main() == 0


def test_example_application_summary_uses_settings():
    settings = SubstrataSettings(
        environment=Environment.LOCAL,
        network=Network.STELLAR_MAINNET,
        signer_backend="kms",
        default_asset_code="USDC",
        default_asset_issuer="issuer-account",
    )
    application = create_example_application(settings)

    assert "stellar_mainnet" in application.startup_summary()
    assert "signer=kms" in application.startup_summary()
    assert "default_asset=USDC:issuer-account" in application.startup_summary()
