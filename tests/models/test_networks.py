import pytest

from substrata.constants import Network
from substrata.errors import ValidationError
from substrata.models import network_from_caip, network_to_caip


@pytest.mark.parametrize(
    ("network", "caip"),
    [(Network.STELLAR_TESTNET, "stellar:testnet"), (Network.STELLAR_MAINNET, "stellar:pubnet")],
)
def test_stellar_network_caip_mapping_round_trips(network, caip):
    assert network_to_caip(network) == caip
    assert network_from_caip(caip) is network


def test_unknown_network_identifiers_raise_validation_error():
    with pytest.raises(ValidationError):
        network_from_caip("stellar:unknown")
