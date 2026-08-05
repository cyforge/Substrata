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


def test_audit_hash_chain_detects_event_edits_and_survives_restart(tmp_path):
    import sqlite3

    path = tmp_path / "audit.sqlite3"
    audit = AuditLog(path)
    audit.append("started", request_id="pay-2", data={"attempt": 1})
    audit.append("settled", request_id="pay-2", data={"tx": "abc"})
    assert audit.verify_chain()
    audit.close()

    connection = sqlite3.connect(path)
    connection.execute("UPDATE audit_events SET data = ? WHERE event_id = 1", ('{"attempt":2}',))
    connection.commit()
    connection.close()
    reopened = AuditLog(path)
    assert not reopened.verify_chain()
    reopened.close()


def test_legacy_audit_rows_receive_hash_baseline(tmp_path):
    import sqlite3

    path = tmp_path / "legacy-audit.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE audit_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "event_type TEXT NOT NULL, request_id TEXT, created_at TEXT NOT NULL, data TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO audit_events(event_type, request_id, created_at, data) VALUES (?, ?, ?, ?)",
        ("legacy", None, "2026-01-01T00:00:00+00:00", "{}"),
    )
    connection.commit()
    connection.close()
    audit = AuditLog(path)
    assert audit.verify_chain()
    audit.close()


def test_export_includes_more_than_ten_thousand_events(tmp_path):
    import sqlite3

    path = tmp_path / "many-events.sqlite3"
    audit = AuditLog(path)
    audit.close()
    connection = sqlite3.connect(path)
    connection.executemany(
        "INSERT INTO audit_events(event_type, request_id, created_at, data) VALUES (?, ?, ?, ?)",
        (
            ("bulk", None, f"2026-01-01T00:00:{index % 60:02d}+00:00", "{}")
            for index in range(10001)
        ),
    )
    connection.commit()
    connection.close()
    audit = AuditLog(path)
    assert len(audit.export_jsonl().splitlines()) == 10001
    audit.close()


def test_audit_rejects_non_dictionary_data_including_empty_lists(tmp_path):
    import pytest

    from substrata.errors import ValidationError

    audit = AuditLog(tmp_path / "audit.sqlite3")
    with pytest.raises(ValidationError, match="dictionary"):
        audit.append("bad", data=[])
    assert audit.append("empty") == 1
    audit.close()


def test_audit_chain_serializes_concurrent_writers_across_connections(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "shared-audit.sqlite3"
    logs = [AuditLog(path) for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as executor:
        list(
            executor.map(
                lambda pair: pair[1].append(
                    "writer", request_id=f"req-{pair[0]}", data={"writer": pair[0]}
                ),
                enumerate(logs),
            )
        )
    assert len(logs[0].list_events()) == 6
    assert logs[0].verify_chain()
    for audit in logs:
        audit.close()
