"""Command line tools for inspecting local Substrata configuration and records."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import uuid4

from . import __version__
from .audit import AuditLog
from .config import load_settings
from .constants import Network
from .errors import SubstrataError
from .models import Asset, PaymentRequest
from .policy import PolicyEngine, SpendPolicy
from .storage import Ledger, LedgerStatus
from .x402.codec import MAX_HEADER_CHARS, MAX_JSON_BYTES, decode_header, decode_json
from .x402.models import PaymentPayload, PaymentRequired, SettlementResponse

_SENSITIVE_KEY_PARTS = (
    "secret",
    "private",
    "token",
    "credential",
    "password",
    "passphrase",
    "mnemonic",
    "seed",
    "apikey",
    "accesskey",
    "authorization",
    "signature",
    "signingkey",
)


def _version() -> str:
    try:
        return version("substrata")
    except PackageNotFoundError:
        return __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="substrata", description="Inspect local Substrata state.")
    parser.add_argument("--version", action="version", version=f"substrata {_version()}")
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser(
        "demo", help="run one local signed payment without external accounts"
    )
    _add_database_argument(demo)

    config = commands.add_parser("config", help="validate and inspect runtime settings")
    config.add_subparsers(dest="config_command", required=True).add_parser(
        "show", help="print validated settings"
    )

    doctor = commands.add_parser("doctor", help="check local configuration and database access")
    _add_database_argument(doctor)
    doctor.add_argument("--init", action="store_true", help="create the database if missing")

    policy = commands.add_parser("policy", help="validate or evaluate a spend policy")
    policy_commands = policy.add_subparsers(dest="policy_command", required=True)
    validate = policy_commands.add_parser("validate", help="validate a JSON policy file")
    validate.add_argument("policy_file", type=Path)
    intersect = policy_commands.add_parser(
        "intersect", help="combine two policies using the stricter limits"
    )
    intersect.add_argument("left_policy", type=Path)
    intersect.add_argument("right_policy", type=Path)
    evaluate = policy_commands.add_parser("evaluate", help="evaluate one payment request")
    evaluate.add_argument("policy_file", type=Path)
    evaluate.add_argument("--recipient", required=True)
    evaluate.add_argument("--amount", required=True)
    evaluate.add_argument("--asset-code", default="XLM")
    evaluate.add_argument("--asset-issuer")
    evaluate.add_argument("--asset-contract-id")
    evaluate.add_argument(
        "--network", choices=[network.value for network in Network], required=True
    )
    evaluate.add_argument("--request-id", required=True)
    evaluate.add_argument("--resource")
    evaluate.add_argument("--origin")
    _add_database_argument(evaluate)

    ledger = commands.add_parser("ledger", help="inspect and reconcile payment records")
    ledger_commands = ledger.add_subparsers(dest="ledger_command", required=True)
    for action in ("list", "export"):
        child = ledger_commands.add_parser(action, help=f"{action} ledger entries")
        _add_database_argument(child)
        child.add_argument("--currency")
        child.add_argument("--status", choices=[status.value for status in LedgerStatus])
        if action == "list":
            child.add_argument("--limit", type=_positive_int, default=100)
            child.add_argument("--offset", type=_nonnegative_int, default=0)
    show = ledger_commands.add_parser("show", help="show one payment record")
    _add_database_argument(show)
    show.add_argument("request_id")
    totals = ledger_commands.add_parser("totals", help="show total and daily reserved spend")
    _add_database_argument(totals)
    totals.add_argument("--currency", required=True)
    totals.add_argument("--day", type=_calendar_date, help="UTC day in YYYY-MM-DD form")
    for action in ("settled", "failed"):
        child = ledger_commands.add_parser(f"reconcile-{action}", help=f"mark a record {action}")
        _add_database_argument(child)
        child.add_argument("request_id")
        if action == "settled":
            child.add_argument("--settlement-id", required=True)
        else:
            child.add_argument("--reason", required=True)

    audit = commands.add_parser("audit", help="inspect and verify audit records")
    audit_commands = audit.add_subparsers(dest="audit_command", required=True)
    verify = audit_commands.add_parser("verify", help="verify append-only integrity chain")
    _add_database_argument(verify)
    for action in ("list", "export"):
        child = audit_commands.add_parser(action, help=f"{action} audit events")
        _add_database_argument(child)
        child.add_argument("--request-id")
        if action == "list":
            child.add_argument("--limit", type=_positive_int, default=100)
            child.add_argument("--offset", type=_nonnegative_int, default=0)

    protocol = commands.add_parser("protocol", help="inspect bounded x402 wire data")
    protocol_commands = protocol.add_subparsers(dest="protocol_command", required=True)
    inspect = protocol_commands.add_parser("inspect", help="decode a JSON body or HTTP header")
    inspect.add_argument("kind", choices=("required", "signature", "response"))
    inspect.add_argument("--file", type=Path, help="read input from a file instead of stdin")
    inspect.add_argument("--format", choices=("json", "header"), default="json")
    inspect.add_argument(
        "--show-payload",
        action="store_true",
        help="include signed payment payload in output (signature kind only)",
    )

    fetch = commands.add_parser("fetch", help="make a policy-controlled Stellar testnet GET")
    fetch.add_argument("url")
    fetch.add_argument("--policy", type=Path, required=True)
    fetch.add_argument("--database", type=Path, required=True)
    fetch.add_argument("--request-id")
    fetch.add_argument(
        "--secret-env-var",
        type=_environment_name,
        default="SUBSTRATA_STELLAR_SECRET_SEED",
        help="environment variable name holding signer seed, never the seed itself",
    )
    fetch.add_argument("--rpc-url", help="Soroban RPC URL (or SUBSTRATA_STELLAR_RPC_URL)")
    return parser


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", type=Path, default=Path("substrata.sqlite3"))


def _require_database(path: Path) -> None:
    if not path.is_file():
        raise ValueError(
            f"Database does not exist at {path}; run `substrata doctor --init --database {path}`."
        )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def _calendar_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD") from exc


def _environment_name(value: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
        raise argparse.ArgumentTypeError("must be a valid environment variable name")
    return value


def _json_line(value: object) -> str:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    if hasattr(value, "value"):
        value = value.value
    value = _redact_sensitive_fields(value)
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def _redact_sensitive_fields(value: object) -> object:
    if isinstance(value, dict):
        clean: dict[str, object] = {}
        for key, item in value.items():
            name = "".join(character for character in str(key).lower() if character.isalnum())
            clean[str(key)] = (
                "[REDACTED]"
                if any(part in name for part in _SENSITIVE_KEY_PARTS)
                else _redact_sensitive_fields(item)
            )
        return clean
    if isinstance(value, list):
        return [_redact_sensitive_fields(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_sensitive_fields(item) for item in value]
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Policy JSON contains duplicate object keys.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Policy JSON contains unsupported constant {value}.")


def _load_policy(path: Path) -> SpendPolicy:
    with path.open("rb") as source:
        data = source.read(1_048_577)
    if len(data) > 1_048_576:
        raise ValueError("Policy file exceeds 1 MiB.")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except RecursionError as exc:
        raise ValueError("Policy JSON exceeds supported nesting depth.") from exc
    pending = [(value, 0)]
    while pending:
        node, depth = pending.pop()
        if depth > 64:
            raise ValueError("Policy JSON exceeds supported nesting depth.")
        if isinstance(node, dict):
            pending.extend((item, depth + 1) for item in node.values())
        elif isinstance(node, list):
            pending.extend((item, depth + 1) for item in node)
    return SpendPolicy.from_dict(value)


def _audit_manual_reconciliation(database: Path, request_id: str, status: LedgerStatus) -> None:
    try:
        with AuditLog(database) as audit_log:
            audit_log.append(
                "ledger.manual_reconciliation",
                request_id=request_id,
                data={"status": status.value, "source": "cli"},
            )
    except (SubstrataError, OSError, sqlite3.Error) as exc:
        raise SubstrataError(
            "Ledger changed, but reconciliation audit logging failed. "
            "Inspect ledger before retrying."
        ) from exc


def _read_protocol_input(path: Path | None, limit: int) -> bytes:
    if path is not None:
        with path.open("rb") as source:
            data = source.read(limit + 1)
    else:
        source = getattr(sys.stdin, "buffer", sys.stdin)
        value = source.read(limit + 1)
        data = value.encode("utf-8") if isinstance(value, str) else value
    if len(data) > limit:
        raise ValueError("Protocol input exceeds supported size.")
    return data


def _run(args: argparse.Namespace) -> int:
    if args.command == "demo":
        from .demo import run_local_demo

        response = run_local_demo(args.database)
        assert response.payment is not None
        print(
            json.dumps(
                {
                    "mode": "local-demo",
                    "http_status": response.status_code,
                    "request_id": response.payment.request_id,
                    "payment_status": response.payment.status.value,
                    "transaction_id": response.payment.transaction_id,
                    "database": str(args.database),
                },
                sort_keys=True,
            )
        )
        return 0

    if args.command == "config":
        settings = load_settings()
        print(
            json.dumps(
                {
                    "environment": settings.environment.value,
                    "network": settings.network.value,
                    "log_level": settings.log_level,
                    "horizon_url": settings.horizon_url,
                    "signer_backend": settings.signer_backend,
                    "default_asset": settings.default_asset_reference,
                    "debug": settings.debug,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.command == "doctor":
        settings = load_settings()
        if not args.init:
            _require_database(args.database)
        with Ledger(args.database), AuditLog(args.database):
            pass
        print(f"Configuration valid ({settings.environment.value}, {settings.network.value}).")
        state = "initialized" if args.init else "accessible"
        print(f"Ledger and audit database {state} at {args.database}.")
        try:
            stellar_version = version("stellar-sdk")
        except PackageNotFoundError:
            print("Optional Stellar SDK: not installed.")
        else:
            print(f"Optional Stellar SDK: {stellar_version}.")
        return 0

    if args.command == "policy":
        if args.policy_command == "intersect":
            left_policy = _load_policy(args.left_policy)
            right_policy = _load_policy(args.right_policy)
            print(
                json.dumps(
                    left_policy.intersect(right_policy).to_dict(),
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        spend_policy = _load_policy(args.policy_file)
        if args.policy_command == "validate":
            print(json.dumps(spend_policy.to_dict(), indent=2, sort_keys=True))
            return 0
        asset = Asset(
            code=args.asset_code,
            issuer=args.asset_issuer,
            contract_id=args.asset_contract_id,
        )
        request = PaymentRequest(
            recipient=args.recipient,
            amount=Decimal(args.amount),
            asset=asset,
            network=Network(args.network),
            request_id=args.request_id,
            resource=args.resource,
            origin=args.origin,
        )
        _require_database(args.database)
        with Ledger(args.database) as ledger:
            spent_total = ledger.spent(request.currency_key)
            spent_today = ledger.spent_today(request.currency_key)
        decision = PolicyEngine(spend_policy).evaluate(
            request,
            spent_total=spent_total,
            spent_today=spent_today,
        )
        print(_json_line(decision))
        return 0 if decision.allowed else 1

    if args.command == "ledger":
        _require_database(args.database)
        with Ledger(args.database) as ledger:
            if args.ledger_command == "list":
                for entry in ledger.list_entries(
                    currency=args.currency,
                    status=args.status,
                    limit=args.limit,
                    offset=args.offset,
                ):
                    print(_json_line(entry))
                return 0
            if args.ledger_command == "export":
                for entry in ledger.iter_entries(
                    currency=args.currency,
                    status=args.status,
                ):
                    print(_json_line(entry))
                return 0
            if args.ledger_command == "show":
                entry = ledger.get(args.request_id)
                if entry is None:
                    print(_json_line({"found": False, "request_id": args.request_id}))
                    return 1
                print(_json_line(entry))
                return 0
            if args.ledger_command == "totals":
                day = args.day or datetime.now(UTC).date()
                totals_by_status = ledger.status_totals(args.currency)
                output = {
                    "currency": args.currency,
                    "day": day.isoformat(),
                    "daily_total": format(ledger.spent_today(args.currency, today=day), "f"),
                    "total": format(ledger.spent(args.currency), "f"),
                    "by_status": {
                        status.value: format(amount, "f")
                        for status, amount in totals_by_status.items()
                    },
                }
                print(_json_line(output))
                return 0
            if args.ledger_command == "reconcile-settled":
                entry = ledger.commit(args.request_id, settlement_id=args.settlement_id)
                _audit_manual_reconciliation(args.database, entry.request_id, entry.status)
                print(_json_line(entry))
                return 0
            entry = ledger.release(args.request_id, reason=args.reason)
            _audit_manual_reconciliation(args.database, entry.request_id, entry.status)
            print(_json_line(entry))
            return 0

    if args.command == "audit":
        _require_database(args.database)
        with AuditLog(args.database) as audit_log:
            if args.audit_command == "verify":
                result = audit_log.inspect_chain()
                print(_json_line(result))
                return 0 if result.valid else 1
            if args.audit_command == "list":
                for event in audit_log.list_events(
                    request_id=args.request_id,
                    limit=args.limit,
                    offset=args.offset,
                ):
                    print(_json_line(event))
                return 0
            print(audit_log.export_jsonl(request_id=args.request_id), end="")
            return 0

    if args.command == "protocol":
        model_types = {
            "required": (PaymentRequired, "PAYMENT-REQUIRED"),
            "signature": (PaymentPayload, "PAYMENT-SIGNATURE"),
            "response": (SettlementResponse, "PAYMENT-RESPONSE"),
        }
        model_type, header_name = model_types[args.kind]
        if args.show_payload and args.kind != "signature":
            raise ValueError("--show-payload applies only to signature input.")
        limit = MAX_HEADER_CHARS if args.format == "header" else MAX_JSON_BYTES
        data = _read_protocol_input(args.file, limit)
        if args.format == "json":
            model = decode_json(data, model_type)
        else:
            try:
                value = data.decode("ascii").strip()
            except UnicodeDecodeError as exc:
                raise ValueError("Protocol header input must be ASCII.") from exc
            if "\n" in value or "\r" in value:
                raise ValueError("Protocol header input must contain one header line.")
            if ":" in value:
                supplied_name, separator, header_value = value.partition(":")
                if not separator or supplied_name.strip().upper() != header_name:
                    raise ValueError(f"Expected {header_name} header.")
                value = header_value.strip()
            model = decode_header(value, model_type)
        output = model.to_dict()
        if args.kind == "signature" and not args.show_payload:
            output["payload"] = "[REDACTED]"
        print(json.dumps(output, indent=2, sort_keys=True, allow_nan=False))
        return 0

    if args.command == "fetch":
        from .client import BuyerClient, ClientError
        from .signers import StellarAuthSigner

        _require_database(args.database)
        spend_policy = _load_policy(args.policy)
        secret_seed = os.environ.get(args.secret_env_var)
        if not secret_seed:
            raise ValueError(f"Signer seed environment variable {args.secret_env_var} is not set.")
        rpc_url = args.rpc_url or os.environ.get("SUBSTRATA_STELLAR_RPC_URL")
        if not rpc_url:
            raise ValueError("Pass --rpc-url or set SUBSTRATA_STELLAR_RPC_URL.")
        if args.request_id is not None and not args.request_id.strip():
            raise ValueError("--request-id must not be blank.")
        request_id = args.request_id or str(uuid4())
        signer = StellarAuthSigner(secret_seed, rpc_url=rpc_url, network="stellar:testnet")
        print(f"request_id={request_id}", file=sys.stderr)
        try:
            with Ledger(args.database) as ledger, AuditLog(args.database) as audit_log:
                with BuyerClient(
                    spend_policy,
                    signer,
                    ledger=ledger,
                    audit_log=audit_log,
                ) as buyer:
                    response = buyer.get(args.url, request_id=request_id)
        except ClientError as exc:
            raise SubstrataError(str(exc)) from None
        summary: dict[str, object] = {"http_status": response.status_code, "request_id": request_id}
        if response.payment is not None:
            summary["payment_status"] = response.payment.status.value
            summary["transaction_id"] = response.payment.transaction_id
        print(json.dumps(summary, sort_keys=True), file=sys.stderr)
        stdout_buffer = getattr(sys.stdout, "buffer", None)
        if stdout_buffer is not None:
            stdout_buffer.write(response.body)
            stdout_buffer.flush()
        else:
            sys.stdout.write(response.body.decode("utf-8", errors="replace"))
        return 0 if response.status_code < 400 else 1

    raise AssertionError(f"Unhandled command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run CLI when args supplied; keep historical no-arg runtime behavior."""
    if argv is None:
        from .services.runtime import run_example_application

        run_example_application()
        return 0
    try:
        return _run(_parser().parse_args(list(argv)))
    except sqlite3.Error:
        print(
            "substrata: database operation failed; check database integrity and access",
            file=sys.stderr,
        )
        return 2
    except (SubstrataError, OSError, ValueError) as exc:
        print(f"substrata: {exc}", file=sys.stderr)
        return 2


def entrypoint() -> int:
    """Console script wrapper that forwards command line arguments."""
    arguments = sys.argv[1:]
    return main(arguments) if arguments else main()
