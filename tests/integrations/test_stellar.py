from __future__ import annotations

import pytest

pytest.importorskip("stellar_sdk")
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


def test_sdk_invocation_rejects_payer_as_transaction_source():
    payer, requirements, _, envelope = _signed_transfer()
    envelope.v1.tx.source_account = Keypair.from_public_key(payer.public_key).xdr_muxed_account()
    with pytest.raises(ValidationError, match="null account"):
        _validate_transfer_envelope(envelope, requirements, payer.public_key)


def test_sdk_invocation_rejects_operation_source_override():
    payer, requirements, _, envelope = _signed_transfer()
    envelope.v1.tx.operations[0].source_account = Keypair.from_public_key(
        payer.public_key
    ).xdr_muxed_account()
    with pytest.raises(ValidationError, match="cannot override"):
        _validate_transfer_envelope(envelope, requirements, payer.public_key)


def test_sdk_invocation_rejects_multiple_auth_entries():
    payer, requirements, _, envelope = _signed_transfer()
    operation = envelope.v1.tx.operations[0].body.invoke_host_function_op
    operation.auth.append(operation.auth[0])
    with pytest.raises(ValidationError, match="exactly one authorization"):
        _validate_transfer_envelope(envelope, requirements, payer.public_key)


def test_sdk_invocation_rejects_nested_auth_invocation():
    payer, requirements, _, envelope = _signed_transfer()
    auth_entry = envelope.v1.tx.operations[0].body.invoke_host_function_op.auth[0]
    auth_entry.root_invocation.sub_invocations.append(auth_entry.root_invocation)
    with pytest.raises(ValidationError, match="sub-invocations"):
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
        PaymentRequirements(
            "exact",
            "stellar:testnet",
            "1",
            "XLM",
            payer.public_key,
            10,
            {"areFeesSponsored": True},
        )
    )
    assert not signer.supports(
        PaymentRequirements("substrata-demo", "stellar:testnet", "1", "XLM", payer.public_key, 10)
    )


class MockStellarRPC:
    """BaseSyncClient-backed offline Soroban RPC fixture."""

    def __new__(cls, decimals: int):
        from stellar_sdk.client.base_sync_client import BaseSyncClient

        class Client(BaseSyncClient):
            def __init__(self):
                self.methods = []
                self.decimals = decimals

            def get(self, url, params=None, max_content_size=None):
                raise AssertionError("unexpected GET")

            def post(self, url, data=None, json_data=None):
                import json

                from stellar_sdk import Address, Keypair, Network, scval, xdr
                from stellar_sdk.transaction_envelope import TransactionEnvelope
                from stellar_sdk.xdr import LedgerEntryData

                method = json_data["method"]
                params = json_data.get("params") or {}
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
                elif method == "getLedgerEntries":
                    account_key = Keypair.from_public_key(NULL_ACCOUNT).xdr_account_id()
                    account = xdr.AccountEntry(
                        account_key,
                        xdr.Int64(0),
                        xdr.SequenceNumber(xdr.Int64(0)),
                        xdr.Uint32(0),
                        None,
                        xdr.Uint32(0),
                        xdr.String32(b""),
                        xdr.Thresholds(b"\x00\x00\x00\x00"),
                        [],
                        xdr.AccountEntryExt(0),
                    )
                    account_data = LedgerEntryData(xdr.LedgerEntryType.ACCOUNT, account=account)
                    result = {
                        "latestLedger": 500,
                        "entries": [
                            {
                                "key": params["keys"][0],
                                "xdr": account_data.to_xdr(),
                                "lastModifiedLedgerSeq": 1,
                            }
                        ],
                    }
                elif method == "simulateTransaction":
                    envelope = TransactionEnvelope.from_xdr(
                        params["transaction"], Network.TESTNET_NETWORK_PASSPHRASE
                    )
                    operation = envelope.transaction.operations[0]
                    host_function = operation.host_function
                    invocation = host_function.invoke_contract
                    function = invocation.function_name.sc_symbol.decode("ascii")
                    if function == "decimals":
                        result_xdr = scval.to_uint32(self.decimals).to_xdr()
                        auth_entries = None
                    elif function == "transfer":
                        args = invocation.args
                        payer = scval.from_address(args[0]).address
                        root_call = xdr.InvokeContractArgs(
                            Address(ASSET).to_xdr_sc_address(),
                            xdr.SCSymbol(b"transfer"),
                            args,
                        )
                        root = xdr.SorobanAuthorizedInvocation(
                            xdr.SorobanAuthorizedFunction(
                                xdr.SorobanAuthorizedFunctionType.SOROBAN_AUTHORIZED_FUNCTION_TYPE_CONTRACT_FN,
                                contract_fn=root_call,
                            ),
                            [],
                        )
                        address_credentials = xdr.SorobanAddressCredentials(
                            Address(payer).to_xdr_sc_address(),
                            xdr.Int64(42),
                            xdr.Uint32(0),
                            scval.to_void(),
                        )
                        auth_entry = xdr.SorobanAuthorizationEntry(
                            xdr.SorobanCredentials(
                                xdr.SorobanCredentialsType.SOROBAN_CREDENTIALS_ADDRESS_V2,
                                address_v2=address_credentials,
                            ),
                            root,
                        )
                        result_xdr = scval.to_void().to_xdr()
                        auth_entries = [auth_entry.to_xdr()]
                    else:
                        raise AssertionError(f"unexpected simulation function {function}")
                    transaction_data = xdr.SorobanTransactionData(
                        xdr.SorobanTransactionDataExt(0),
                        xdr.SorobanResources(
                            xdr.LedgerFootprint([], []),
                            xdr.Uint32(0),
                            xdr.Uint32(0),
                            xdr.Uint32(0),
                        ),
                        xdr.Int64(0),
                    ).to_xdr()
                    host_result = {"xdr": result_xdr}
                    if auth_entries:
                        host_result["auth"] = auth_entries
                    result = {
                        "hash": "0" * 64,
                        "latestLedger": 500,
                        "latestLedgerCloseTime": 1,
                        "transactionData": transaction_data,
                        "minResourceFee": 0,
                        "results": [host_result],
                    }
                else:
                    raise AssertionError(f"unexpected RPC method {method}")
                body = json.dumps({"jsonrpc": "2.0", "id": json_data["id"], "result": result})
                return Response(200, body, {}, url)

            def stream(self, url, params=None):
                yield from ()

            def close(self):
                return None

        from stellar_sdk.client.response import Response

        return Client()


def _sign_with_rpc(decimals: int, timeout: int = 10, max_authorization_seconds: int = 60):
    payer = Keypair.random()
    rpc = MockStellarRPC(decimals)
    signer = StellarAuthSigner(
        payer.secret,
        rpc_url="https://soroban-testnet.stellar.org",
        max_authorization_seconds=max_authorization_seconds,
        request_client=rpc,
    )
    offer = PaymentRequirements(
        "exact",
        "stellar:testnet",
        "12500000",
        ASSET,
        Keypair.random().public_key,
        timeout,
        {"areFeesSponsored": True},
    )
    from substrata.x402.models import ResourceInfo

    return signer, rpc, offer, signer.sign(offer, ResourceInfo("https://api.example.test/resource"))


def test_signer_rejects_nonseven_decimal_token_using_mocked_rpc_before_transfer():
    payer = Keypair.random()
    rpc = MockStellarRPC(6)
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
    assert rpc.methods.count("simulateTransaction") == 1


def test_public_signer_builds_and_signs_auth_xdr_with_mocked_rpc():
    from stellar_sdk.auth import authorization_payload_hash, build_authorization_preimage
    from stellar_sdk.transaction_envelope import TransactionEnvelope

    signer, rpc, offer, payload = _sign_with_rpc(7)
    envelope = TransactionEnvelope.from_xdr(
        payload.payload["transaction"], Network.TESTNET_NETWORK_PASSPHRASE
    )
    operation = envelope.transaction.operations[0]
    assert envelope.signatures == []
    assert len(operation.auth) == 1
    entry = operation.auth[0]
    credentials = entry.credentials.address_v2
    assert credentials is not None
    assert credentials.signature_expiration_ledger.uint32 == 502
    signature_map = scval.from_map(scval.from_vec(credentials.signature)[0])
    signature = scval.from_bytes(signature_map[scval.to_symbol("signature")])
    preimage = build_authorization_preimage(
        entry,
        credentials.signature_expiration_ledger.uint32,
        Network.TESTNET_NETWORK_PASSPHRASE,
    )
    signer._load_keypair().verify(authorization_payload_hash(preimage), signature)
    assert rpc.methods.count("simulateTransaction") == 2
    assert "sendTransaction" not in rpc.methods


def test_auth_expiration_uses_ceil_offer_timeout_ledger_conversion():
    from stellar_sdk.transaction_envelope import TransactionEnvelope

    signer, rpc, _, payload = _sign_with_rpc(7, timeout=11)
    envelope = TransactionEnvelope.from_xdr(
        payload.payload["transaction"], Network.TESTNET_NETWORK_PASSPHRASE
    )
    auth = envelope.transaction.operations[0].auth[0]
    assert auth.credentials.address_v2.signature_expiration_ledger.uint32 == 503
    assert "sendTransaction" not in rpc.methods


def test_auth_expiration_caps_untrusted_long_offer_timeout():
    from stellar_sdk.transaction_envelope import TransactionEnvelope

    signer, rpc, _, payload = _sign_with_rpc(7, timeout=86_400)
    envelope = TransactionEnvelope.from_xdr(
        payload.payload["transaction"], Network.TESTNET_NETWORK_PASSPHRASE
    )
    auth = envelope.transaction.operations[0].auth[0]
    assert auth.credentials.address_v2.signature_expiration_ledger.uint32 == 512
    assert signer.max_authorization_seconds == 60
    assert "sendTransaction" not in rpc.methods


@pytest.mark.parametrize("max_seconds", [0, 3601, True])
def test_signer_rejects_invalid_local_authorization_lifetime(max_seconds):
    payer = Keypair.random()
    with pytest.raises(ValidationError, match="max_authorization_seconds"):
        StellarAuthSigner(
            payer.secret,
            rpc_url="https://soroban-testnet.stellar.org",
            max_authorization_seconds=max_seconds,
        )
