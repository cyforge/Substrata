"""Network identifiers used by payment protocol APIs."""

from ..constants import Network
from ..errors import ValidationError

_NETWORK_TO_CAIP = {
    Network.STELLAR_TESTNET: "stellar:testnet",
    Network.STELLAR_MAINNET: "stellar:pubnet",
}
_CAIP_TO_NETWORK = {value: key for key, value in _NETWORK_TO_CAIP.items()}


def network_to_caip(network: Network) -> str:
    try:
        return _NETWORK_TO_CAIP[network]
    except (KeyError, TypeError) as exc:
        raise ValidationError(f"Unsupported Stellar network: {network!r}") from exc


def network_from_caip(value: str) -> Network:
    try:
        return _CAIP_TO_NETWORK[value]
    except (KeyError, TypeError) as exc:
        raise ValidationError(f"Unsupported CAIP network: {value!r}") from exc


__all__ = ["network_from_caip", "network_to_caip"]
