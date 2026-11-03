import json
import threading
from decimal import Decimal

from substrata.audit import AuditLog
from substrata.demo import run_local_demo
from substrata.models import PaymentStatus
from substrata.storage import Ledger, ReplayStore


def test_real_http_demo_settles_and_retains_ledger_audit_and_replay_records(tmp_path):
    database = tmp_path / "demo.sqlite3"
    before = set(threading.enumerate())
    response = run_local_demo(database)
    assert response.status_code == 200
    assert json.loads(response.body) == {"message": "paid content", "mode": "local-demo"}
    assert response.payment.status == PaymentStatus.SETTLED
    assert response.payment.transaction_id.startswith("demo:")
    assert set(threading.enumerate()) == before
    with Ledger(database) as ledger, AuditLog(database) as audit, ReplayStore(database) as replay:
        entry = ledger.get(response.payment.request_id)
        assert entry.amount == Decimal("0.1")
        assert entry.settlement_id == response.payment.transaction_id
        assert ledger.spent(entry.currency) == Decimal("0.1")
        assert audit.verify_chain()
        events = {event["event_type"] for event in audit.list_events(limit=100)}
        assert {"payment_reserved", "payment_settled", "provider.settled"} <= events
        assert replay.count_claims() == 1
        assert "signature" not in audit.export_jsonl().lower()


def test_repeat_demo_preserves_previous_spend_and_unique_payment_identity(tmp_path):
    database = tmp_path / "demo.sqlite3"
    first = run_local_demo(database)
    second = run_local_demo(database)
    assert first.payment.request_id != second.payment.request_id
    with Ledger(database) as ledger:
        assert ledger.count_entries(status="settled") == 2
        assert ledger.spent("stellar_testnet:XLM") == Decimal("0.2")
