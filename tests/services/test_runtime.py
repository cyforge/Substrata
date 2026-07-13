from substrata import create_example_application
from substrata.config import SubstrataSettings
from substrata.constants import Environment, Network


def test_runtime_service_exposes_package_aware_application():
    settings = SubstrataSettings(
        environment=Environment.TEST,
        network=Network.STELLAR_MAINNET,
        signer_backend="kms",
        default_asset_code="USDC",
        default_asset_issuer="issuer-account",
    )

    application = create_example_application(settings)

    assert application.settings is settings
    assert "environment=test" in application.startup_summary()
    assert "default_asset=USDC:issuer-account" in application.startup_summary()
