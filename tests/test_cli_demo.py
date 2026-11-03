import json
import os
import subprocess
import sys
from pathlib import Path

from substrata.cli import main
from substrata.storage import Ledger


def test_demo_command_initializes_database_and_reports_local_receipt(tmp_path, capsys):
    database = tmp_path / "demo.sqlite3"
    assert main(["demo", "--database", str(database)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "local-demo"
    assert output["http_status"] == 200
    assert output["payment_status"] == "settled"
    assert output["transaction_id"].startswith("demo:")
    with Ledger(database) as ledger:
        assert ledger.get(output["request_id"]).status == "settled"


def test_module_demo_runs_outside_repository_with_explicit_package_path(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "substrata", "demo", "--database", str(tmp_path / "demo.db")],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert json.loads(result.stdout)["payment_status"] == "settled"
