"""Command line tools for inspecting local Substrata configuration and records."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from . import __version__
from .audit import AuditLog
from .config import load_settings
from .errors import SubstrataError
from .storage import Ledger, LedgerStatus


def _version() -> str:
    try:
        return version("substrata")
    except PackageNotFoundError:
        return __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="substrata", description="Inspect local Substrata state.")
    parser.add_argument("--version", action="version", version=f"substrata {_version()}")
    commands = parser.add_subparsers(dest="command", required=True)

    config = commands.add_parser("config", help="validate and inspect runtime settings")
    config.add_subparsers(dest="config_command", required=True).add_parser(
        "show", help="print validated settings"
    )

    doctor = commands.add_parser("doctor", help="check local configuration and database access")
    _add_database_argument(doctor)

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
    return parser


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", type=Path, default=Path("substrata.sqlite3"))


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


def _json_line(value: object) -> str:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    if hasattr(value, "value"):
        value = value.value
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def _run(args: argparse.Namespace) -> int:
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
        with Ledger(args.database), AuditLog(args.database):
            pass
        print(f"Configuration valid ({settings.environment.value}, {settings.network.value}).")
        print(f"Ledger and audit database accessible at {args.database}.")
        return 0

    if args.command == "ledger":
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
                print(
                    ledger.export_jsonl(
                        currency=args.currency,
                        status=args.status,
                    ),
                    end="",
                )
                return 0
            if args.ledger_command == "reconcile-settled":
                entry = ledger.commit(args.request_id, settlement_id=args.settlement_id)
                print(_json_line(entry))
                return 0
            entry = ledger.release(args.request_id, reason=args.reason)
            print(_json_line(entry))
            return 0

    if args.command == "audit":
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

    raise AssertionError(f"Unhandled command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run CLI when args supplied; keep historical no-arg runtime behavior."""
    if argv is None:
        from .services.runtime import run_example_application

        run_example_application()
        return 0
    try:
        return _run(_parser().parse_args(list(argv)))
    except (SubstrataError, OSError, ValueError) as exc:
        print(f"substrata: {exc}", file=sys.stderr)
        return 2


def entrypoint() -> int:
    """Console script wrapper that forwards command line arguments."""
    arguments = sys.argv[1:]
    return main(arguments) if arguments else main()
