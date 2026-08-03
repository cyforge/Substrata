import json

from substrata.audit import AuditLog


def test_audit_persists_redacted_events_and_exports(tmp_path):
    path = tmp_path / "audit.sqlite3"
    audit = AuditLog(path)
    event_id = audit.append(
        "payment.settled",
        request_id="pay-1",
        data={
            "amount": "2",
            "api_key": "secret-value",
            "nested": {"access_token": "bearer", "payment-signature": "signed-proof"},
            "privateKey": "never-store",
        },
    )
    assert event_id == 1
    event = audit.list_events(request_id="pay-1")[0]
    assert event["data"] == {
        "amount": "2",
        "api_key": "[REDACTED]",
        "nested": {
            "access_token": "[REDACTED]",
            "payment-signature": "[REDACTED]",
        },
        "privateKey": "[REDACTED]",
    }
    exported = audit.export_jsonl()
    assert "secret-value" not in exported
    assert "signed-proof" not in exported
    assert "never-store" not in exported
    audit.close()
    reopened = AuditLog(path)
    assert reopened.list_events(event_type="payment.settled")[0]["event_id"] == 1
    reopened.close()


def test_audit_export_is_json_lines(tmp_path):
    audit = AuditLog(tmp_path / "audit.sqlite3")
    audit.append("started", data={"attempt": 1})
    exported = audit.export_jsonl()
    assert json.loads(exported.splitlines()[0])["event_type"] == "started"
