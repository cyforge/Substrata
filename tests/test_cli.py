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
    settings = SubstrataSettings(environment=Environment.LOCAL, network=Network.STELLAR_MAINNET)
    application = create_example_application(settings)

    assert "stellar_mainnet" in application.startup_summary()
