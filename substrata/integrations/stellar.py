"""Optional Stellar SEP-41 x402 authorization-entry signer."""

from __future__ import annotations

import base64
import math
from typing import TYPE_CHECKING

from substrata.errors import ValidationError
from substrata.x402.models import PaymentPayload, PaymentRequirements, ResourceInfo

if TYPE_CHECKING:
    from stellar_sdk.client.base_sync_client import BaseSyncClient
    from stellar_sdk.keypair import Keypair


class StellarAuthSigner:
    """Sign Soroban transfer authorization entries through Stellar SDK 17.

    This signer never signs the transaction envelope or submits it. Simulation
    uses the configured Soroban RPC endpoint; callers provide the facilitator.
    """

    def __init__(
        self,
        secret_seed: str,
        *,
        rpc_url: str,
        network: str = "stellar:testnet",
        ledger_seconds: int = 5,
        request_client: BaseSyncClient | None = None,
    ) -> None:
        if not isinstance(secret_seed, str) or not secret_seed:
            raise ValidationError("Stellar secret seed is required")
        if not isinstance(rpc_url, str) or not rpc_url.startswith(
            ("https://", "http://localhost", "http://127.0.0.1")
        ):
            raise ValidationError("Soroban RPC URL must use HTTPS except loopback development")
        if network not in {"stellar:testnet", "stellar:pubnet"}:
            raise ValidationError("Stellar signer supports testnet or pubnet")
        if type(ledger_seconds) is not int or not 1 <= ledger_seconds <= 60:
            raise ValidationError("ledger_seconds must be from 1 to 60")
        self._secret_seed = secret_seed
        self.rpc_url = rpc_url
        self.network = network
        self.ledger_seconds = ledger_seconds
        self.request_client = request_client
        self._keypair: Keypair | None = None

    @property
    def address(self) -> str:
        return self._load_keypair().public_key

    def supports(self, requirements: PaymentRequirements) -> bool:
        return (
            isinstance(requirements, PaymentRequirements)
            and requirements.scheme == "exact"
            and requirements.network == self.network
            and requirements.extra is not None
            and requirements.extra.get("areFeesSponsored") is True
        )

    def sign(self, requirements: PaymentRequirements, resource: ResourceInfo) -> PaymentPayload:
        if not self.supports(requirements):
            raise ValidationError("Stellar signer does not support this offer")
        if not isinstance(resource, ResourceInfo):
            raise ValidationError("resource must be ResourceInfo")
        try:
            from stellar_sdk import Address, scval
            from stellar_sdk.client.base_sync_client import BaseSyncClient
            from stellar_sdk.contract import ContractClient
            from stellar_sdk.network import Network
            from stellar_sdk.soroban_server import SorobanServer
        except ImportError as exc:
            raise ValidationError(
                "Stellar signing requires optional dependency stellar-sdk>=17,<18"
            ) from exc

        if self.request_client is not None and not isinstance(self.request_client, BaseSyncClient):
            raise ValidationError("request_client must be a Stellar SDK BaseSyncClient")
        keypair = self._load_keypair()
        network_passphrase = (
            Network.TESTNET_NETWORK_PASSPHRASE
            if self.network == "stellar:testnet"
            else Network.PUBLIC_NETWORK_PASSPHRASE
        )
        atomic_amount = int(requirements.amount)
        if atomic_amount <= 0 or atomic_amount > (1 << 127) - 1:
            raise ValidationError("Stellar amount is outside signed 128-bit range")
        timeout_ledgers = math.ceil(requirements.max_timeout_seconds / self.ledger_seconds)
        server = SorobanServer(self.rpc_url, client=self.request_client)
        try:
            ledger = server.get_latest_ledger()
            latest_sequence = ledger.sequence
            client = ContractClient(
                requirements.asset,
                self.rpc_url,
                network_passphrase,
                request_client=self.request_client,
            )
            decimals_call = client.invoke(
                "decimals",
                parameters=[],
                parse_result_xdr_fn=scval.from_uint32,
                simulate=True,
                restore=False,
                use_upgraded_auth=True,
            )
            decimals = decimals_call.result
            if type(decimals) is not int or decimals != 7:
                raise ValidationError("Stellar token precision must be exactly seven decimals")
            assembled = client.invoke(
                "transfer",
                parameters=[
                    Address(keypair.public_key).to_scval(),
                    Address(requirements.pay_to).to_scval(),
                    scval.to_int128(atomic_amount),
                ],
                simulate=True,
                restore=False,
                use_upgraded_auth=True,
            )
            envelope = assembled.built_transaction.to_xdr_object()
            _validate_transfer_envelope(envelope, requirements, keypair.public_key)
            expiration_ledger = latest_sequence + timeout_ledgers
            assembled.sign_auth_entries(
                keypair,
                address=keypair.public_key,
                valid_until_ledger_sequence=expiration_ledger,
            )
            envelope = assembled.built_transaction.to_xdr_object()
            _validate_transfer_envelope(
                envelope,
                requirements,
                keypair.public_key,
                current_ledger=latest_sequence,
                max_expiration_ledger=expiration_ledger,
            )
            xdr = assembled.to_xdr()
            if not isinstance(xdr, str) or not xdr:
                raise ValidationError("Stellar SDK returned empty transaction XDR")
            encoded = base64.b64encode(base64.b64decode(xdr, validate=True)).decode("ascii")
            if encoded != xdr:
                raise ValidationError("Stellar SDK returned noncanonical transaction XDR")
        except ValidationError:
            raise
        except Exception as exc:
            # SDK exceptions may include RPC endpoint details or signed material.
            raise ValidationError(
                f"Stellar authorization simulation or signing failed: {exc!r}"
            ) from exc
        return PaymentPayload(
            accepted=requirements,
            resource=resource,
            payload={"transaction": xdr},
        )

    def _load_keypair(self) -> Keypair:
        if self._keypair is None:
            try:
                from stellar_sdk import Keypair

                self._keypair = Keypair.from_secret(self._secret_seed)
            except ImportError as exc:
                raise ValidationError(
                    "Stellar signing requires optional dependency stellar-sdk>=17,<18"
                ) from exc
            except Exception:
                raise ValidationError("invalid Stellar secret seed") from None
        return self._keypair


def _validate_transfer_envelope(
    envelope: object,
    requirements: PaymentRequirements,
    payer: str,
    *,
    current_ledger: int | None = None,
    max_expiration_ledger: int | None = None,
) -> None:
    """Reject SDK output that escapes one SEP-41 transfer auth invocation."""
    try:
        from stellar_sdk import Address, StrKey, scval, xdr

        transaction = envelope.v1.tx if envelope.v1 is not None else envelope.v0.tx
        source = transaction.source_account
        if source.type == xdr.CryptoKeyType.KEY_TYPE_ED25519:
            source_address = StrKey.encode_ed25519_public_key(source.ed25519.uint256)
            if source_address == payer:
                raise ValidationError("Stellar transaction source cannot be the payer")
        operations = transaction.operations
        if len(operations) != 1:
            raise ValidationError("Stellar invocation must contain exactly one operation")
        operation = operations[0]
        if operation.body.type.name != "INVOKE_HOST_FUNCTION":
            raise ValidationError("Stellar operation must invoke a Soroban host function")
        invoke = operation.body.invoke_host_function_op
        function = invoke.host_function
        if function.type.name != "HOST_FUNCTION_TYPE_INVOKE_CONTRACT":
            raise ValidationError("Stellar operation must invoke a contract")
        args = function.invoke_contract
        if (
            Address.from_xdr_sc_address(args.contract_address).address != requirements.asset
            or args.function_name.sc_symbol.decode("ascii") != "transfer"
        ):
            raise ValidationError("Stellar invocation does not match offered token transfer")
        if len(args.args) != 3:
            raise ValidationError("Stellar transfer must have exactly three arguments")
        if scval.from_address(args.args[0]).address != payer:
            raise ValidationError("Stellar transfer source does not match signer")
        if scval.from_address(args.args[1]).address != requirements.pay_to:
            raise ValidationError("Stellar transfer recipient does not match offer")
        if scval.from_int128(args.args[2]) != int(requirements.amount):
            raise ValidationError("Stellar transfer amount does not match offer")
        auth_entries = invoke.auth
        if not auth_entries:
            raise ValidationError("Stellar simulation returned no authorization entries")
        for entry in auth_entries:
            credentials = entry.credentials
            if credentials.type.name not in {
                "SOROBAN_CREDENTIALS_ADDRESS",
                "SOROBAN_CREDENTIALS_ADDRESS_V2",
            }:
                raise ValidationError(
                    "Stellar source-account and delegated credentials are forbidden"
                )
            address_credentials = (
                credentials.address_v2
                if credentials.type.name == "SOROBAN_CREDENTIALS_ADDRESS_V2"
                else credentials.address
            )
            if Address.from_xdr_sc_address(address_credentials.address).address != payer:
                raise ValidationError("Stellar authorization entry belongs to another payer")
            if max_expiration_ledger is not None:
                expiration = address_credentials.signature_expiration_ledger.uint32
                if expiration <= (current_ledger or 0) or expiration > max_expiration_ledger:
                    raise ValidationError("Stellar authorization expiration exceeds offer timeout")
            root = entry.root_invocation
            if root.sub_invocations:
                raise ValidationError("Stellar authorization cannot contain sub-invocations")
            if root.function.type.name != "SOROBAN_AUTHORIZED_FUNCTION_TYPE_CONTRACT_FN":
                raise ValidationError("Stellar authorization root must be contract invocation")
            root_call = root.function.contract_fn
            if (
                Address.from_xdr_sc_address(root_call.contract_address).address
                != requirements.asset
                or root_call.function_name.sc_symbol.decode("ascii") != "transfer"
                or len(root_call.args) != 3
                or scval.from_address(root_call.args[0]).address != payer
                or scval.from_address(root_call.args[1]).address != requirements.pay_to
                or scval.from_int128(root_call.args[2]) != int(requirements.amount)
            ):
                raise ValidationError("Stellar authorization does not match offered transfer")
    except ValidationError:
        raise
    except Exception:
        raise ValidationError("Stellar SDK returned an invalid authorization invocation") from None


__all__ = ["StellarAuthSigner"]
