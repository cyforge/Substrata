from __future__ import annotations

import pytest
from stellar_sdk import Account, Address, Keypair, Network, TransactionBuilder, scval, xdr
from stellar_sdk.auth import (
    authorization_payload_hash,
    authorize_entry,
    build_authorization_preimage,
)

from substrata.errors import ValidationError
from substrata.integrations.stellar import StellarAuthSigner, _validate_transfer_envelope
from substrata.x402.models import PaymentRequirements

ASSET = "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC"
NULL_ACCOUNT = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"


def _signed_transfer():
    payer = Keypair.random()
    recipient = Keypair.random()
    requirements = PaymentRequirements(
        "exact",
        "stellar:testnet",
        "12500000",
        ASSET,
        recipient.public_key,
        45,
        {"areFeesSponsored": True},
    )
    args = [
        scval.to_address(payer.public_key),
        scval.to_address(recipient.public_key),
        scval.to_int128(12500000),
    ]
    invoke = xdr.InvokeContractArgs(
        Address(ASSET).to_xdr_sc_address(), xdr.SCSymbol(b"transfer"), args
    )
    root = xdr.SorobanAuthorizedInvocation(
        xdr.SorobanAuthorizedFunction(
            xdr.SorobanAuthorizedFunctionType.SOROBAN_AUTHORIZED_FUNCTION_TYPE_CONTRACT_FN,
            contract_fn=invoke,
        ),
        [],
    )
    credentials = xdr.SorobanAddressCredentials(
        Address(payer.public_key).to_xdr_sc_address(),
        xdr.Int64(4),
        xdr.Uint32(120),
        scval.to_vec([]),
    )
    auth_entry = xdr.SorobanAuthorizationEntry(
        xdr.SorobanCredentials(
            xdr.SorobanCredentialsType.SOROBAN_CREDENTIALS_ADDRESS_V2,
            address_v2=credentials,
        ),
        root,
    )
    auth_entry = authorize_entry(auth_entry, payer, 120, Network.TESTNET_NETWORK_PASSPHRASE)
    envelope = (
        TransactionBuilder(Account(NULL_ACCOUNT, 0), Network.TESTNET_NETWORK_PASSPHRASE)
        .append_invoke_contract_function_op(ASSET, "transfer", parameters=args, auth=[auth_entry])
        .set_timeout(30)
        .build()
        .to_xdr_object()
    )
    return payer, requirements, auth_entry, envelope


def test_real_sdk_address_v2_authorization_signature_verifies_and_xdr_roundtrips():
    payer, _, entry, _ = _signed_transfer()
    credentials = entry.credentials.address_v2
    signature_values = scval.from_vec(credentials.signature)
    signature_map = scval.from_map(signature_values[0])
    signature = scval.from_bytes(signature_map[scval.to_symbol("signature")])
    preimage = build_authorization_preimage(entry, 120, Network.TESTNET_NETWORK_PASSPHRASE)
    payer.verify(authorization_payload_hash(preimage), signature)
    restored = xdr.SorobanAuthorizationEntry.from_xdr(entry.to_xdr())
    assert restored == entry


def test_sdk_invocation_is_exact_transfer_and_auth_is_payer_bound():
    payer, requirements, _, envelope = _signed_transfer()
    _validate_transfer_envelope(envelope, requirements, payer.public_key)


def test_sdk_invocation_rejects_wrong_amount_before_signing():
    payer, requirements, _, envelope = _signed_transfer()
    wrong = PaymentRequirements(
        "exact",
        "stellar:testnet",
        "12500001",
        ASSET,
        requirements.pay_to,
        45,
        {"areFeesSponsored": True},
    )
    with pytest.raises(ValidationError, match="amount does not match"):
        _validate_transfer_envelope(envelope, wrong, payer.public_key)


def test_signer_supports_only_sponsored_exact_offer_on_configured_network():
    payer = Keypair.random()
    signer = StellarAuthSigner(
        payer.secret,
        rpc_url="https://soroban-testnet.stellar.org",
    )
    supported = PaymentRequirements(
        "exact",
        "stellar:testnet",
        "1",
        ASSET,
        payer.public_key,
        10,
        {"areFeesSponsored": True},
    )
    unsupported = PaymentRequirements(
        "exact",
        "stellar:pubnet",
        "1",
        ASSET,
        payer.public_key,
        10,
        {"areFeesSponsored": True},
    )
    assert signer.supports(supported)
    assert not signer.supports(unsupported)
    assert not signer.supports(
        PaymentRequirements("substrata-demo", "stellar:testnet", "1", "XLM", payer.public_key, 10)
    )


def test_signer_rejects_nonseven_decimal_token_using_mocked_rpc_before_transfer(monkeypatch):
    import json
    from types import SimpleNamespace

    from stellar_sdk.client.base_sync_client import BaseSyncClient
    from stellar_sdk.client.response import Response

    class MockRPC(BaseSyncClient):
        def __init__(self):
            self.methods = []

        def get(self, url, params=None, max_content_size=None):
            raise AssertionError("unexpected GET")

        def post(self, url, data=None, json_data=None):
            method = json_data["method"]
            self.methods.append(method)
            if method == "getLatestLedger":
                result = {
                    "id": json_data["id"],
                    "protocolVersion": 28,
                    "sequence": 500,
                    "closeTime": 1,
                    "headerXdr": "",
                    "metadataXdr": "",
                }
            else:
                tx_data = xdr.SorobanTransactionData(
                    xdr.SorobanTransactionDataExt(0),
                    xdr.SorobanResources(
                        xdr.LedgerFootprint([], []), xdr.Uint32(0), xdr.Uint32(0), xdr.Uint32(0)
                    ),
                    xdr.Int64(0),
                ).to_xdr()
                result = {
                    "hash": "0" * 64,
                    "latestLedger": 500,
                    "latestLedgerCloseTime": 1,
                    "transactionData": tx_data,
                    "minResourceFee": 0,
                    "results": [{"xdr": scval.to_uint32(6).to_xdr()}],
                }
            body = json.dumps({"jsonrpc": "2.0", "id": json_data["id"], "result": result})
            return Response(200, body, {}, url)

        def stream(self, url, params=None):
            yield from ()

        def close(self):
            return None

    def simulated_invoke(self, function_name, **kwargs):
        if function_name == "decimals":
            return SimpleNamespace(result=6)
        raise AssertionError("transfer authorization must not run for unsupported precision")

    monkeypatch.setattr("stellar_sdk.contract.ContractClient.invoke", simulated_invoke)
    payer = Keypair.random()
    rpc = MockRPC()
    signer = StellarAuthSigner(
        payer.secret,
        rpc_url="https://soroban-testnet.stellar.org",
        request_client=rpc,
    )
    offer = PaymentRequirements(
        "exact",
        "stellar:testnet",
        "1",
        ASSET,
        Keypair.random().public_key,
        10,
        {"areFeesSponsored": True},
    )
    from substrata.x402.models import ResourceInfo

    with pytest.raises(ValidationError, match="exactly seven decimals"):
        signer.sign(offer, ResourceInfo("https://api.example.test/resource"))
    assert rpc.methods == ["getLatestLedger"]
