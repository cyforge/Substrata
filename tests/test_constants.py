from substrata.constants import (
    DEFAULT_ENVIRONMENT,
    DEFAULT_NETWORK,
    Environment,
    Network,
    supported_environments,
    supported_networks,
)


def test_supported_environment_values_are_exported_from_one_place():
    assert DEFAULT_ENVIRONMENT is Environment.LOCAL
    assert supported_environments() == (
        Environment.LOCAL,
        Environment.TEST,
        Environment.STAGING,
        Environment.PRODUCTION,
    )


def test_supported_network_values_are_exported_from_one_place():
    assert DEFAULT_NETWORK is Network.STELLAR_TESTNET
    assert supported_networks() == (
        Network.STELLAR_TESTNET,
        Network.STELLAR_MAINNET,
    )
