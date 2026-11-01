import io
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from substrata.audit import AuditLog
from substrata.cli import entrypoint, main
from substrata.storage import Ledger
from substrata.x402.codec import encode_header, encode_json
from substrata.x402.models import PaymentPayload, PaymentRequired, SettlementResponse

_REQUIREMENTS = {
    "scheme": "substrata-demo",
    "network": "stellar:testnet",
    "amount": "100",
    "asset": "XLM",
    "payTo": "GRECIPIENT",
    "maxTimeoutSeconds": 60,
}


def test_cli_main_without_args_keeps_runtime_compatibility(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "substrata.services.runtime.run_example_application",
        lambda: calls.append(True),
    )

    assert main() == 0
    assert calls == [True]


def test_console_entrypoint_forwards_process_arguments(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["substrata", "--version"])

    with pytest.raises(SystemExit) as exit_info:
        entrypoint()

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.startswith("substrata ")


def test_package_and_compatibility_entrypoints_forward_arguments():
    repo_root = Path(__file__).resolve().parents[1]
    for command in ([sys.executable, "-m", "substrata"], [sys.executable, "main.py"]):
        result = subprocess.run(
            [*command, "--version"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.startswith("substrata ")


def test_protocol_inspect_decodes_json_and_http_headers(tmp_path, capsys, monkeypatch):
    required = PaymentRequired.from_dict(
        {
            "x402Version": 2,
            "resource": {"url": "http://127.0.0.1:8080/data"},
            "accepts": [_REQUIREMENTS],
        }
    )
    body_path = tmp_path / "required.json"
    body_path.write_bytes(encode_json(required))
    assert main(["protocol", "inspect", "required", "--file", str(body_path)]) == 0
    assert json.loads(capsys.readouterr().out)["accepts"][0]["scheme"] == "substrata-demo"

    payment = PaymentPayload.from_dict(
        {
            "x402Version": 2,
            "accepted": _REQUIREMENTS,
            "payload": {"signature": "signed-secret-value"},
        }
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(encode_header(payment)))
    assert main(["protocol", "inspect", "signature", "--format", "header"]) == 0
    redacted = capsys.readouterr().out
    assert json.loads(redacted)["payload"] == "[REDACTED]"
    assert "signed-secret-value" not in redacted

    monkeypatch.setattr("sys.stdin", io.StringIO(encode_json(payment).decode()))
    assert (
        main(
            [
                "protocol",
                "inspect",
                "signature",
                "--show-payload",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["payload"]["signature"] == "signed-secret-value"

    response = SettlementResponse(True, "demo:tx", "stellar:testnet")
    header_path = tmp_path / "response.header"
    header_path.write_text(f"PAYMENT-RESPONSE: {encode_header(response)}\n")
    assert (
        main(
            [
                "protocol",
                "inspect",
                "response",
                "--format",
                "header",
                "--file",
                str(header_path),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["transaction"] == "demo:tx"


def test_protocol_inspect_bounds_input_and_rejects_wrong_header(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(" " * (1_048_577)))
    assert main(["protocol", "inspect", "required"]) == 2
    assert "exceeds supported size" in capsys.readouterr().err

    monkeypatch.setattr("sys.stdin", io.StringIO("PAYMENT-SIGNATURE: e30="))
    assert main(["protocol", "inspect", "required", "--format", "header"]) == 2
    assert "Expected PAYMENT-REQUIRED" in capsys.readouterr().err


def test_fetch_uses_named_environment_secret_and_durable_client(tmp_path, capsys, monkeypatch):
    from substrata.client import ClientResponse

    database = tmp_path / "fetch.sqlite3"
    policy_path = tmp_path / "policy.json"
    _write_policy(policy_path)
    with Ledger(database):
        pass
    secret = "S" * 56
    monkeypatch.setenv("BUYER_SECRET_FOR_TEST", secret)
    monkeypatch.setenv("SUBSTRATA_STELLAR_RPC_URL", "https://rpc.example.test")
    observed: dict[str, object] = {}

    class FakeSigner:
        def __init__(self, secret_seed, *, rpc_url, network):
            observed.update(secret=secret_seed, rpc_url=rpc_url, network=network)

    class FakeBuyer:
        def __init__(self, policy, signer, *, ledger, audit_log):
            observed.update(policy=policy, signer=signer, ledger=ledger, audit_log=audit_log)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def get(self, url, *, request_id):
            observed.update(url=url, request_id=request_id)
            return ClientResponse(200, {}, b"response body")

    monkeypatch.setattr("substrata.signers.StellarAuthSigner", FakeSigner)
    monkeypatch.setattr("substrata.client.BuyerClient", FakeBuyer)

    result = main(
        [
            "fetch",
            "https://api.example.test/paid",
            "--policy",
            str(policy_path),
            "--database",
            str(database),
            "--secret-env-var",
            "BUYER_SECRET_FOR_TEST",
            "--request-id",
            "fetch-1",
        ]
    )

    output = capsys.readouterr()
    assert result == 0
    assert output.out == "response body"
    assert "fetch-1" in output.err
    assert secret not in output.out + output.err
    assert observed["secret"] == secret
    assert observed["network"] == "stellar:testnet"
    assert observed["url"] == "https://api.example.test/paid"


def test_fetch_requires_secret_environment_variable_without_echoing_secret(
    tmp_path, capsys, monkeypatch
):
    database = tmp_path / "fetch.sqlite3"
    policy_path = tmp_path / "policy.json"
    _write_policy(policy_path)
    monkeypatch.delenv("SUBSTRATA_STELLAR_SECRET_SEED", raising=False)
    with Ledger(database):
        pass

    assert (
        main(
            [
                "fetch",
                "https://api.example.test/paid",
                "--policy",
                str(policy_path),
                "--database",
                str(database),
            ]
        )
        == 2
    )
    assert "SUBSTRATA_STELLAR_SECRET_SEED" in capsys.readouterr().err


def test_config_show_prints_validated_nonsecret_settings(monkeypatch, capsys):
    monkeypatch.setenv("SUBSTRATA_NETWORK", "stellar_testnet")

    assert main(["config", "show"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["network"] == "stellar_testnet"
    assert "private_key" not in output


def test_doctor_checks_local_sqlite_path(tmp_path, capsys):
    database = tmp_path / "local.sqlite3"

    assert main(["doctor", "--database", str(database)]) == 2
    assert "doctor --init" in capsys.readouterr().err

    assert main(["doctor", "--init", "--database", str(database)]) == 0

    assert database.exists()
    output = capsys.readouterr().out
    assert "initialized" in output
    assert "Optional Stellar SDK:" in output


def test_doctor_reports_optional_stellar_version_without_network(tmp_path, capsys, monkeypatch):
    database = tmp_path / "local.sqlite3"
    with Ledger(database):
        pass
    monkeypatch.setattr("substrata.cli.version", lambda package: "17.0.1")

    assert main(["doctor", "--database", str(database)]) == 0

    assert "Optional Stellar SDK: 17.0.1" in capsys.readouterr().out


def _write_policy(path: Path, **overrides):
    policy = {
        "allowed_networks": ["stellar_testnet"],
        "allowed_recipients": ["GRECIPIENT"],
        "allowed_assets": [{"code": "XLM", "issuer": None, "contract_id": None}],
        "max_payment": "5",
        "daily_limit": "10",
        "total_limit": "20",
    }
    policy.update(overrides)
    path.write_text(json.dumps(policy))


def test_policy_cli_validates_and_evaluates_without_reserving(tmp_path, capsys):
    policy_path = tmp_path / "policy.json"
    database = tmp_path / "policy.sqlite3"
    _write_policy(policy_path)
    with Ledger(database):
        pass

    assert main(["policy", "validate", str(policy_path)]) == 0
    assert json.loads(capsys.readouterr().out)["max_payment"] == "5"

    assert main(
        [
            "policy",
            "evaluate",
            str(policy_path),
            "--recipient",
            "GRECIPIENT",
            "--amount",
            "2",
            "--network",
            "stellar_testnet",
            "--request-id",
            "request-1",
            "--database",
            str(database),
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["allowed"] is True

    with Ledger(database) as ledger:
        assert ledger.count_entries() == 0


def test_policy_cli_uses_ledger_spend_and_denies_over_daily_limit(tmp_path, capsys):
    policy_path = tmp_path / "policy.json"
    database = tmp_path / "policy.sqlite3"
    _write_policy(policy_path)
    with Ledger(database) as ledger:
        entry = ledger.reserve(
            "existing-payment",
            Decimal("9"),
            currency="stellar_testnet:XLM",
        )
        ledger.commit(entry.request_id)

    result_code = main(
        [
            "policy",
            "evaluate",
            str(policy_path),
            "--recipient",
            "GRECIPIENT",
            "--amount",
            "2",
            "--network",
            "stellar_testnet",
            "--request-id",
            "request-2",
            "--database",
            str(database),
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert result_code == 1
    assert result["reason_code"] == "daily_limit_exceeded"


def test_policy_cli_intersects_restrictions_without_mutating_files(tmp_path, capsys):
    global_policy_path = tmp_path / "global.json"
    task_policy_path = tmp_path / "task.json"
    _write_policy(
        global_policy_path,
        allowed_recipients=["GONE", "GSHARED"],
        max_payment="5",
        daily_limit="10",
        total_limit="20",
    )
    _write_policy(
        task_policy_path,
        allowed_recipients=["GSHARED", "GOTHER"],
        max_payment="3",
        daily_limit="4",
        total_limit="7",
    )
    original_global = global_policy_path.read_text()
    original_task = task_policy_path.read_text()

    assert (
        main(
            [
                "policy",
                "intersect",
                str(global_policy_path),
                str(task_policy_path),
            ]
        )
        == 0
    )
    combined = json.loads(capsys.readouterr().out)
    assert combined["allowed_recipients"] == ["GSHARED"]
    assert combined["max_payment"] == "3"
    assert combined["daily_limit"] == "4"
    assert combined["total_limit"] == "7"
    assert global_policy_path.read_text() == original_global
    assert task_policy_path.read_text() == original_task


def test_policy_cli_rejects_duplicate_and_unknown_json_fields(tmp_path, capsys):
    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text('{"allowed_networks": [], "allowed_networks": []}')
    assert main(["policy", "validate", str(duplicate_path)]) == 2
    assert "duplicate" in capsys.readouterr().err

    unknown_path = tmp_path / "unknown.json"
    _write_policy(unknown_path, allow_everything=True)
    assert main(["policy", "validate", str(unknown_path)]) == 2
    assert "Unknown policy fields" in capsys.readouterr().err


def test_policy_cli_bounds_files_and_rejects_nonfinite_json(tmp_path, capsys):
    oversized_path = tmp_path / "oversized.json"
    oversized_path.write_bytes(b" " * 1_048_577)
    assert main(["policy", "validate", str(oversized_path)]) == 2
    assert "exceeds 1 MiB" in capsys.readouterr().err

    nonfinite_path = tmp_path / "nonfinite.json"
    nonfinite_path.write_text('{"daily_limit": NaN}')
    assert main(["policy", "validate", str(nonfinite_path)]) == 2
    assert "unsupported constant" in capsys.readouterr().err

    nested_path = tmp_path / "nested.json"
    nested_path.write_text("[" * 1200 + "0" + "]" * 1200)
    assert main(["policy", "validate", str(nested_path)]) == 2
    assert "nesting depth" in capsys.readouterr().err


def test_ledger_cli_lists_exports_and_reconciles(tmp_path, capsys):
    database = tmp_path / "ledger.sqlite3"
    with Ledger(database) as ledger:
        ledger.reserve(
            "req-1",
            Decimal("1.25"),
            currency="stellar:testnet:XLM",
            metadata={"api_key": "dont-print", "note": "visible"},
        )

    assert main(["ledger", "list", "--database", str(database)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["request_id"] == "req-1"
    assert listed["status"] == "pending"
    assert listed["metadata"] == {"api_key": "[REDACTED]", "note": "visible"}

    assert main(
        [
            "ledger",
            "reconcile-settled",
            "req-1",
            "--settlement-id",
            "demo:tx-1",
            "--database",
            str(database),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "settled"

    assert main(
        ["ledger", "export", "--database", str(database), "--status", "settled"]
    ) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["settlement_id"] == "demo:tx-1"
    assert exported["metadata"]["api_key"] == "[REDACTED]"

    assert main(["audit", "list", "--database", str(database), "--request-id", "req-1"]) == 0
    reconciliation = json.loads(capsys.readouterr().out)
    assert reconciliation["event_type"] == "ledger.manual_reconciliation"
    assert reconciliation["data"] == {"source": "cli", "status": "settled"}


def test_ledger_show_displays_uncertain_entry_without_leaking_metadata(tmp_path, capsys):
    database = tmp_path / "ledger.sqlite3"
    with Ledger(database) as ledger:
        ledger.reserve(
            "req-uncertain",
            Decimal("0.75"),
            currency="stellar_testnet:XLM",
            metadata={"private_key": "never-show"},
        )
        ledger.mark_uncertain("req-uncertain", reason="No receipt")

    assert main(["ledger", "show", "req-uncertain", "--database", str(database)]) == 0
    shown = capsys.readouterr().out
    result = json.loads(shown)
    assert result["status"] == "uncertain"
    assert result["metadata"]["private_key"] == "[REDACTED]"
    assert "never-show" not in shown

    assert main(["ledger", "show", "missing", "--database", str(database)]) == 1
    assert json.loads(capsys.readouterr().out) == {"found": False, "request_id": "missing"}


def test_ledger_totals_reports_selected_utc_day_and_statuses(tmp_path, capsys):
    database = tmp_path / "ledger.sqlite3"
    currency = "stellar_testnet:XLM"
    today = datetime.now(UTC).date()
    with Ledger(database) as ledger:
        ledger.reserve("req-one", Decimal("1.25"), currency=currency)
        ledger.reserve("req-two", Decimal("2.75"), currency=currency)
        ledger.commit("req-two")

    assert main(
        [
            "ledger",
            "totals",
            "--currency",
            currency,
            "--day",
            today.isoformat(),
            "--database",
            str(database),
        ]
    ) == 0
    current = json.loads(capsys.readouterr().out)
    assert current["daily_total"] == "4.0000000"
    assert current["total"] == "4.0000000"
    assert current["by_status"] == {
        "failed": "0.0000000",
        "pending": "1.2500000",
        "settled": "2.7500000",
        "uncertain": "0.0000000",
    }

    yesterday = (today - timedelta(days=1)).isoformat()
    assert main(
        [
            "ledger",
            "totals",
            "--currency",
            currency,
            "--day",
            yesterday,
            "--database",
            str(database),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["daily_total"] == "0.0000000"


def test_audit_cli_lists_exports_and_verifies(tmp_path, capsys):
    database = tmp_path / "audit.sqlite3"
    with AuditLog(database) as audit:
        audit.append("payment.settled", request_id="req-1", data={"token": "hidden"})

    assert main(["audit", "list", "--database", str(database)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["data"]["token"] == "[REDACTED]"

    assert main(["audit", "verify", "--database", str(database)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True

    assert main(["audit", "export", "--database", str(database)]) == 0
    assert json.loads(capsys.readouterr().out)["event_type"] == "payment.settled"


def test_cli_errors_are_concise_and_use_nonzero_exit(capsys):
    assert main(["ledger", "list", "--database", "/no/such/parent/db.sqlite3"]) == 2

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.startswith("substrata:")
    assert "password" not in output.err


def test_read_only_commands_do_not_create_missing_database(tmp_path, capsys):
    database = tmp_path / "missing.sqlite3"
    policy_path = tmp_path / "policy.json"
    _write_policy(policy_path)

    assert main(["ledger", "list", "--database", str(database)]) == 2
    assert "doctor --init" in capsys.readouterr().err
    assert not database.exists()

    assert main(["audit", "verify", "--database", str(database)]) == 2
    assert "doctor --init" in capsys.readouterr().err
    assert not database.exists()

    assert main(
        [
            "policy",
            "evaluate",
            str(policy_path),
            "--recipient",
            "GRECIPIENT",
            "--amount",
            "1",
            "--network",
            "stellar_testnet",
            "--request-id",
            "request-1",
            "--database",
            str(database),
        ]
    ) == 2
    assert "doctor --init" in capsys.readouterr().err
    assert not database.exists()
