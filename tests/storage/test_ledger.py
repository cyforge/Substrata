from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from substrata.errors import ValidationError
from substrata.storage import Ledger, LedgerStatus

CURRENCY = "stellar:testnet:XLM"


def test_reservation_and_settlement_persist_across_restart(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    ledger = Ledger(path)
    reserved = ledger.reserve(
        "payment-1", Decimal("1.2345678"), currency=CURRENCY, limit=Decimal("2")
    )
    assert reserved.amount == Decimal("1.2345678")
    assert reserved.status == "pending"
    ledger.mark_uncertain("payment-1", reason="settlement timed out")
    assert ledger.spent(CURRENCY) == Decimal("1.2345678")
    ledger.close()

    reopened = Ledger(path)
    entry = reopened.commit("payment-1", settlement_id="tx-1", metadata={"ledger": "seq-4"})
    assert entry.status == "settled"
    assert entry.settlement_id == "tx-1"
    assert entry.metadata == {"ledger": "seq-4"}
    assert reopened.get("payment-1") == entry
    reopened.close()


def test_atomic_limit_rejects_overspend_and_separates_currency(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.reserve("one", Decimal("0.75"), currency=CURRENCY, limit=Decimal("1"))
    with pytest.raises(ValidationError, match="exceeds"):
        ledger.reserve("two", Decimal("0.30"), currency=CURRENCY, limit=Decimal("1"))
    ledger.reserve("other", Decimal("0.30"), currency="stellar:pubnet:XLM", limit=Decimal("1"))
    assert ledger.spent(CURRENCY) == Decimal("0.75")


def test_same_request_id_is_idempotent_but_payload_conflicts_fail(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    original = ledger.reserve("same", Decimal("2"), currency=CURRENCY)
    again = ledger.reserve("same", Decimal("2.0000000"), currency=CURRENCY)
    assert again == original
    with pytest.raises(ValidationError, match="different payment"):
        ledger.reserve("same", Decimal("3"), currency=CURRENCY)
    with pytest.raises(ValidationError, match="different payment"):
        ledger.reserve("same", Decimal("2"), currency="stellar:pubnet:XLM")
    with pytest.raises(ValidationError, match="different payment"):
        ledger.reserve(
            "same", Decimal("2"), currency=CURRENCY, request_fingerprint="other-destination"
        )


def test_failed_release_stops_counting_and_terminal_state_is_immutable(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.reserve("failed", Decimal("1"), currency=CURRENCY)
    entry = ledger.release("failed", reason="verification rejected")
    assert entry.status == "failed"
    assert ledger.spent(CURRENCY) == Decimal("0")
    with pytest.raises(ValidationError, match="terminal"):
        ledger.commit("failed")


def test_seven_decimal_exactness_and_input_type_validation(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    for value in (Decimal("0.00000001"), Decimal("NaN"), 1.0, True):
        with pytest.raises(ValidationError):
            ledger.reserve("bad-" + str(value), value, currency=CURRENCY)


def test_concurrent_connections_cannot_overspend(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    ledgers = [Ledger(path) for _ in range(8)]

    def reserve(index):
        try:
            ledgers[index].reserve(
                f"payment-{index}", Decimal("1"), currency=CURRENCY, limit=Decimal("3")
            )
            return True
        except ValidationError:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(reserve, range(8)))
    assert sum(results) == 3
    assert ledgers[0].spent(CURRENCY) == Decimal("3")
    for ledger in ledgers:
        ledger.close()


def test_query_and_jsonl_export(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.reserve("one", Decimal("1"), currency=CURRENCY)
    ledger.reserve("two", Decimal("2"), currency="stellar:pubnet:XLM")
    rows = ledger.list_entries(currency=CURRENCY)
    assert [row.request_id for row in rows] == ["one"]
    assert '"amount":"1.0000000"' in ledger.export_jsonl(currency=CURRENCY)


def test_reservation_binds_destination_and_checks_daily_budget_atomically(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.reserve(
        "first",
        Decimal("0.70"),
        currency=CURRENCY,
        limit=Decimal("5"),
        daily_limit=Decimal("1"),
        request_fingerprint="recipient:GABC",
    )
    with pytest.raises(ValidationError, match="different payment"):
        ledger.reserve(
            "first",
            Decimal("0.70"),
            currency=CURRENCY,
            request_fingerprint="recipient:GDEF",
        )
    with pytest.raises(ValidationError, match="daily"):
        ledger.reserve("second", Decimal("0.40"), currency=CURRENCY, daily_limit=Decimal("1"))


def test_total_overflow_is_rejected_without_sqlite_sum_error(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    maximum = Decimal(2**63 - 1) / Decimal(10**7)
    ledger.reserve("large", maximum, currency=CURRENCY)
    with pytest.raises(ValidationError, match="range"):
        ledger.reserve("too-large-total", Decimal("0.0000001"), currency=CURRENCY)
    assert ledger.spent(CURRENCY) == maximum


def test_since_requires_timezone_and_normalizes_offsets(tmp_path):
    from datetime import datetime, timedelta, timezone

    ledger = Ledger(tmp_path / "ledger.sqlite3")
    entry = ledger.reserve("timed", Decimal("1"), currency=CURRENCY)
    instant = datetime.fromisoformat(entry.created_at)
    equivalent_offset = instant.astimezone(timezone(timedelta(hours=3)))
    assert ledger.spent(CURRENCY, since=instant) == Decimal("1.0000000")
    assert ledger.spent(CURRENCY, since=equivalent_offset.isoformat()) == Decimal("1.0000000")
    for invalid in (datetime.now(), "2026-01-01T00:00:00", "bad-date", 12):
        with pytest.raises(ValidationError, match="since"):
            ledger.spent(CURRENCY, since=invalid)


def test_amount_conversion_rejects_huge_exponents_without_large_intermediate(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    with pytest.raises(ValidationError, match="range"):
        ledger.reserve("huge", Decimal("1E+999999999"), currency=CURRENCY)
    with pytest.raises(ValidationError, match="decimal places"):
        ledger.reserve("tiny", Decimal("1E-999999999"), currency=CURRENCY)


def test_repeated_commit_fills_missing_settlement_id_and_merges_receipt_metadata(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.reserve("receipt", Decimal("1"), currency=CURRENCY)
    first = ledger.commit("receipt", metadata={"sequence": "42"})
    assert first.status == "settled"
    assert first.settlement_id is None
    repeated = ledger.commit("receipt", settlement_id="tx-hash", metadata={"fee": "0.00001"})
    assert repeated.settlement_id == "tx-hash"
    assert repeated.metadata == {"sequence": "42", "fee": "0.00001"}
    assert ledger.commit("receipt", settlement_id="tx-hash") == repeated
    with pytest.raises(ValidationError, match="conflicts"):
        ledger.commit("receipt", settlement_id="different-hash")


def test_invalid_settlement_metadata_and_identifiers_are_rejected(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.reserve("receipt", Decimal("1"), currency=CURRENCY)
    for settlement_id in ("", "  "):
        with pytest.raises(ValidationError, match="settlement_id"):
            ledger.commit("receipt", settlement_id=settlement_id)
    with pytest.raises(ValidationError, match="metadata"):
        ledger.commit("receipt", metadata={"value": object()})


def test_reserve_once_returns_creation_signal_atomically(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    ledgers = [Ledger(path) for _ in range(8)]

    def reserve(index):
        return ledgers[index].reserve_once(
            "same-payment",
            Decimal("1"),
            currency=CURRENCY,
            request_fingerprint="recipient:GABC",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        reservations = list(executor.map(reserve, range(8)))
    assert sum(item.created for item in reservations) == 1
    assert {item.entry.request_id for item in reservations} == {"same-payment"}
    assert all(item.entry.status == "pending" for item in reservations)
    for ledger in ledgers:
        ledger.close()


def test_legacy_ledger_schema_migrates_without_losing_rows(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE ledger_entries ("
        "request_id TEXT PRIMARY KEY, amount_units INTEGER NOT NULL, currency TEXT NOT NULL, "
        "status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "settlement_id TEXT, reason TEXT, metadata TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO ledger_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "old-request",
            25_000_000,
            CURRENCY,
            "pending",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            None,
            None,
            "{}",
        ),
    )
    connection.commit()
    connection.close()

    ledger = Ledger(path)
    migrated = ledger.get("old-request")
    assert migrated is not None
    assert migrated.amount == Decimal("2.5000000")
    assert migrated.request_fingerprint is None
    assert ledger.reserve("old-request", Decimal("2.5"), currency=CURRENCY) == migrated
    ledger.close()


def test_concurrent_legacy_schema_migration_runs_once(tmp_path):
    import sqlite3

    path = tmp_path / "legacy-concurrent.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE ledger_entries ("
        "request_id TEXT PRIMARY KEY, amount_units INTEGER NOT NULL, currency TEXT NOT NULL, "
        "status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "settlement_id TEXT, reason TEXT, metadata TEXT NOT NULL)"
    )
    connection.commit()
    connection.close()

    with ThreadPoolExecutor(max_workers=6) as executor:
        ledgers = list(executor.map(lambda _: Ledger(path), range(6)))
    for ledger in ledgers:
        ledger.close()


def test_entries_expose_typed_status_enum(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    entry = ledger.reserve("typed", Decimal("1"), currency=CURRENCY)
    assert entry.status is LedgerStatus.PENDING
    assert ledger.commit("typed").status is LedgerStatus.SETTLED
    assert LedgerStatus.FAILED.value == "failed"
