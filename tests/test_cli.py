import json
from decimal import Decimal

from substrata.audit import AuditLog
from substrata.cli import main
from substrata.storage import Ledger


def test_cli_main_without_args_keeps_runtime_compatibility(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "substrata.services.runtime.run_example_application",
        lambda: calls.append(True),
    )

    assert main() == 0
    assert calls == [True]


def test_config_show_prints_validated_nonsecret_settings(monkeypatch, capsys):
    monkeypatch.setenv("SUBSTRATA_NETWORK", "stellar_testnet")

    assert main(["config", "show"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["network"] == "stellar_testnet"
    assert "private_key" not in output


def test_doctor_checks_local_sqlite_path(tmp_path, capsys):
    database = tmp_path / "local.sqlite3"

    assert main(["doctor", "--database", str(database)]) == 0

    assert database.exists()
    assert "accessible" in capsys.readouterr().out


def test_ledger_cli_lists_exports_and_reconciles(tmp_path, capsys):
    database = tmp_path / "ledger.sqlite3"
    with Ledger(database) as ledger:
        ledger.reserve("req-1", Decimal("1.25"), currency="stellar:testnet:XLM")

    assert main(["ledger", "list", "--database", str(database)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["request_id"] == "req-1"
    assert listed["status"] == "pending"

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
