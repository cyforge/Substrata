from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from substrata.errors import ValidationError
from substrata.storage import Ledger

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
