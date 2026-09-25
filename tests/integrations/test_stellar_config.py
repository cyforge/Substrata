import pytest

from substrata.errors import ValidationError
from substrata.integrations.stellar import StellarAuthSigner


@pytest.mark.parametrize(
    "rpc_url",
    [
        "http://remote.example/rpc",
        "http://localhost.evil.example/rpc",
        "http://user:secret@127.0.0.1/rpc",
        "https://rpc.example/rpc#fragment",
        "https://rpc.example:/rpc",
        "https://[not-ipv6]/rpc",
        "https://rpc.example/rpc\n",
    ],
)
def test_signer_rejects_unsafe_rpc_urls_without_sdk_import(rpc_url):
    with pytest.raises(ValidationError, match="RPC URL"):
        StellarAuthSigner("unused-seed", rpc_url=rpc_url)


@pytest.mark.parametrize(
    "rpc_url", ["https://rpc.example/rpc", "http://127.0.0.1:8000/rpc", "http://[::1]/rpc"]
)
def test_signer_accepts_tls_or_exact_loopback_rpc_urls(rpc_url):
    signer = StellarAuthSigner("unused-seed", rpc_url=rpc_url)
    assert signer.rpc_url == rpc_url
