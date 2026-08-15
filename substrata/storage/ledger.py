"""SQLite-backed atomic budget reservations and settlement records."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from ..errors import ValidationError
from ._sqlite import configure_connection

_SCALE = 7
_FACTOR = 10**_SCALE
_ACTIVE_STATUSES = ("pending", "uncertain", "settled")


class LedgerStatus(str, Enum):
    PENDING = "pending"
    SETTLED = "settled"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    request_id: str
    amount: Decimal
    currency: str
    status: LedgerStatus
    created_at: str
    updated_at: str
    settlement_id: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] | None = None
    request_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class Reservation:
    entry: LedgerEntry
    created: bool


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _decimal_units(value: Decimal, field: str, *, positive: bool = True) -> int:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError(f"{field} must be a finite Decimal.")
    if positive and value <= 0:
        raise ValidationError(f"{field} must be greater than zero.")
    if not positive and value < 0:
        raise ValidationError(f"{field} must not be negative.")
    sign, digits, exponent = value.as_tuple()
    if not any(digits):
        return 0
    if sign:
        raise ValidationError(f"{field} must not be negative.")
    shift = exponent + _SCALE
    if shift < 0:
        places = -shift
        trailing_zeroes = 0
        for digit in reversed(digits):
            if digit != 0:
                break
            trailing_zeroes += 1
        if places > trailing_zeroes:
            raise ValidationError(f"{field} supports at most {_SCALE} decimal places.")
        unit_digits = digits[: len(digits) - places]
        if len(unit_digits) > 19:
            raise ValidationError(f"{field} exceeds SQLite's supported amount range.")
        units = int("".join(str(digit) for digit in unit_digits))
    else:
        if len(digits) + shift > 19:
            raise ValidationError(f"{field} exceeds SQLite's supported amount range.")
        coefficient = int("".join(str(digit) for digit in digits))
        units = coefficient * (10**shift)
    if units > 2**63 - 1:
        raise ValidationError(f"{field} exceeds SQLite's supported amount range.")
    return units


def _amount(units: int) -> Decimal:
    digits = tuple(int(character) for character in str(abs(units)))
    return Decimal((1 if units < 0 else 0, digits, -_SCALE))


def _metadata(value: dict[str, Any] | None) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, dict):
        raise ValidationError("metadata must be a dictionary.")
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError("metadata must contain JSON-compatible values.") from exc


def _entry(row: sqlite3.Row) -> LedgerEntry:
    return LedgerEntry(
        request_id=row["request_id"],
        amount=_amount(row["amount_units"]),
        currency=row["currency"],
        status=LedgerStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        settlement_id=row["settlement_id"],
        reason=row["reason"],
        metadata=json.loads(row["metadata"]),
        request_fingerprint=row["request_fingerprint"],
    )


class Ledger:
    """Durable reservation ledger with cross-connection transaction safety."""

    def __init__(self, path: str | Path = "substrata.sqlite3") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._stream_active = False
        try:
            self._connection = sqlite3.connect(
                self.path,
                timeout=30,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            configure_connection(self._connection, self.path)
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ledger_entries (
                    request_id TEXT PRIMARY KEY,
                    amount_units INTEGER NOT NULL CHECK (amount_units > 0),
                    currency TEXT NOT NULL,
                    request_fingerprint TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending','settled','failed','uncertain')
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    settlement_id TEXT,
                    reason TEXT,
                    metadata TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ledger_currency_status_idx
                    ON ledger_entries(currency, status);
                CREATE INDEX IF NOT EXISTS ledger_created_idx
                    ON ledger_entries(created_at);
                CREATE INDEX IF NOT EXISTS ledger_settlement_idx
                    ON ledger_entries(settlement_id) WHERE settlement_id IS NOT NULL;
                """
            )
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                columns = {
                    row[1] for row in self._connection.execute("PRAGMA table_info(ledger_entries)")
                }
                if "request_fingerprint" not in columns:
                    self._connection.execute(
                        "ALTER TABLE ledger_entries ADD COLUMN request_fingerprint TEXT"
                    )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        except sqlite3.Error as exc:
            raise ValidationError(f"Unable to open ledger: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            if self._stream_active:
                raise RuntimeError("Cannot close ledger while iterating a snapshot.")
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> "Ledger":
        self._conn()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _conn(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Ledger is closed.")
        return self._connection

    def _begin_write(self, connection: sqlite3.Connection) -> None:
        if self._stream_active:
            raise RuntimeError("Cannot mutate ledger while iterating a snapshot.")
        connection.execute("BEGIN IMMEDIATE")

    def reserve(
        self,
        request_id: str,
        amount: Decimal,
        *,
        currency: str,
        limit: Decimal | None = None,
        daily_limit: Decimal | None = None,
        request_fingerprint: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        """Reserve budget and return existing or newly created entry."""
        return self.reserve_once(
            request_id,
            amount,
            currency=currency,
            limit=limit,
            daily_limit=daily_limit,
            request_fingerprint=request_fingerprint,
            metadata=metadata,
        ).entry

    def reserve_once(
        self,
        request_id: str,
        amount: Decimal,
        *,
        currency: str,
        limit: Decimal | None = None,
        daily_limit: Decimal | None = None,
        request_fingerprint: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Reservation:
        request_id = self._required_text(request_id, "request_id")
        currency = self._required_text(currency, "currency")
        units = _decimal_units(amount, "amount")
        limit_units = None if limit is None else _decimal_units(limit, "limit", positive=False)
        daily_limit_units = (
            None
            if daily_limit is None
            else _decimal_units(daily_limit, "daily_limit", positive=False)
        )
        if request_fingerprint is not None:
            request_fingerprint = self._required_text(request_fingerprint, "request_fingerprint")
            request_fingerprint = hashlib.sha256(request_fingerprint.encode("utf-8")).hexdigest()
        metadata_json = _metadata(metadata)
        now = _now()
        with self._lock:
            connection = self._conn()
            self._begin_write(connection)
            try:
                existing = connection.execute(
                    "SELECT * FROM ledger_entries WHERE request_id = ?", (request_id,)
                ).fetchone()
                if existing is not None:
                    if (
                        existing["amount_units"] != units
                        or existing["currency"] != currency
                        or existing["request_fingerprint"] != request_fingerprint
                    ):
                        raise ValidationError("request_id already belongs to a different payment.")
                    connection.execute("COMMIT")
                    return Reservation(_entry(existing), created=False)
                spent_rows = connection.execute(
                    "SELECT amount_units, created_at FROM ledger_entries "
                    "WHERE currency = ? AND status IN ('pending','uncertain','settled')",
                    (currency,),
                ).fetchall()
                spent = sum(row["amount_units"] for row in spent_rows)
                if spent + units > 2**63 - 1:
                    raise ValidationError("Currency total exceeds supported accounting range.")
                if limit_units is not None and spent + units > limit_units:
                    raise ValidationError("Reservation exceeds currency budget.")
                if daily_limit_units is not None:
                    day_start = datetime.now(UTC).date().isoformat() + "T00:00:00+00:00"
                    daily_spent = sum(
                        row["amount_units"] for row in spent_rows if row["created_at"] >= day_start
                    )
                    if daily_spent + units > daily_limit_units:
                        raise ValidationError("Reservation exceeds daily currency budget.")
                connection.execute(
                    "INSERT INTO ledger_entries(request_id, amount_units, currency, "
                    "request_fingerprint, "
                    "status, created_at, updated_at, metadata) "
                    "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
                    (request_id, units, currency, request_fingerprint, now, now, metadata_json),
                )
                row = connection.execute(
                    "SELECT * FROM ledger_entries WHERE request_id = ?", (request_id,)
                ).fetchone()
                connection.execute("COMMIT")
                return Reservation(_entry(row), created=True)
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def commit(
        self,
        request_id: str,
        *,
        settlement_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        updates = _metadata(metadata)
        if settlement_id is not None:
            settlement_id = self._required_text(settlement_id, "settlement_id")
        return self._transition(
            request_id,
            LedgerStatus.SETTLED,
            settlement_id=settlement_id,
            metadata=updates,
        )

    def release(self, request_id: str, *, reason: str | None = None) -> LedgerEntry:
        if reason is not None:
            reason = self._required_text(reason, "reason")
        return self._transition(request_id, LedgerStatus.FAILED, reason=reason)

    def mark_uncertain(
        self,
        request_id: str,
        *,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        if reason is not None:
            reason = self._required_text(reason, "reason")
        return self._transition(
            request_id,
            LedgerStatus.UNCERTAIN,
            reason=reason,
            metadata=_metadata(metadata) if metadata is not None else None,
        )

    def _transition(
        self,
        request_id: str,
        status: str,
        *,
        settlement_id: str | None = None,
        reason: str | None = None,
        metadata: str | None = None,
    ) -> LedgerEntry:
        request_id = self._required_text(request_id, "request_id")
        with self._lock:
            connection = self._conn()
            self._begin_write(connection)
            try:
                row = connection.execute(
                    "SELECT * FROM ledger_entries WHERE request_id = ?", (request_id,)
                ).fetchone()
                if row is None:
                    raise ValidationError("Unknown request_id.")
                current = row["status"]
                if current == status:
                    if (
                        status == LedgerStatus.SETTLED
                        and settlement_id
                        and row["settlement_id"] not in (None, settlement_id)
                    ):
                        raise ValidationError("Settlement ID conflicts with recorded settlement.")
                    merged = json.loads(row["metadata"])
                    if metadata:
                        merged.update(json.loads(metadata))
                    merged_json = _metadata(merged)
                    if (
                        merged_json != row["metadata"]
                        or settlement_id is not None
                        and row["settlement_id"] is None
                    ):
                        connection.execute(
                            "UPDATE ledger_entries SET updated_at = ?, "
                            "settlement_id = COALESCE(?, settlement_id), metadata = ? "
                            "WHERE request_id = ?",
                            (_now(), settlement_id, merged_json, request_id),
                        )
                        row = connection.execute(
                            "SELECT * FROM ledger_entries WHERE request_id = ?", (request_id,)
                        ).fetchone()
                    connection.execute("COMMIT")
                    return _entry(row)
                if current in (LedgerStatus.FAILED, LedgerStatus.SETTLED):
                    raise ValidationError(f"Cannot change terminal ledger status '{current}'.")
                merged = json.loads(row["metadata"])
                if metadata:
                    merged.update(json.loads(metadata))
                connection.execute(
                    "UPDATE ledger_entries SET status = ?, updated_at = ?, "
                    "settlement_id = COALESCE(?, settlement_id), "
                    "reason = ?, metadata = ? WHERE request_id = ?",
                    (status, _now(), settlement_id, reason, _metadata(merged), request_id),
                )
                updated = connection.execute(
                    "SELECT * FROM ledger_entries WHERE request_id = ?", (request_id,)
                ).fetchone()
                connection.execute("COMMIT")
                return _entry(updated)
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def get(self, request_id: str) -> LedgerEntry | None:
        request_id = self._required_text(request_id, "request_id")
        with self._lock:
            row = (
                self._conn()
                .execute("SELECT * FROM ledger_entries WHERE request_id = ?", (request_id,))
                .fetchone()
            )
            return None if row is None else _entry(row)

    def spent(
        self,
        currency: str,
        *,
        include_pending: bool = True,
        since: str | datetime | None = None,
    ) -> Decimal:
        currency = self._required_text(currency, "currency")
        statuses = _ACTIVE_STATUSES if include_pending else ("uncertain", "settled")
        parameters: list[Any] = [currency, *statuses]
        status_placeholders = ",".join("?" for _ in statuses)
        query = (
            "SELECT amount_units FROM ledger_entries WHERE currency = ? "
            f"AND status IN ({status_placeholders})"
        )
        if since is not None:
            if isinstance(since, datetime):
                parsed_since = since
            elif isinstance(since, str):
                try:
                    parsed_since = datetime.fromisoformat(since.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValidationError("since must be an ISO-8601 datetime.") from exc
            else:
                raise ValidationError("since must be a timezone-aware datetime or ISO-8601 string.")
            if parsed_since.tzinfo is None or parsed_since.utcoffset() is None:
                raise ValidationError("since must include a timezone.")
            since_text = parsed_since.astimezone(UTC).isoformat(timespec="microseconds")
            query += " AND created_at >= ?"
            parameters.append(since_text)
        with self._lock:
            rows = self._conn().execute(query, parameters).fetchall()
            units = sum(row[0] for row in rows)
        if units > 2**63 - 1:
            raise ValidationError("Currency total exceeds supported accounting range.")
        return _amount(units)

    def spent_today(
        self,
        currency: str,
        *,
        today: date | None = None,
        include_pending: bool = True,
    ) -> Decimal:
        """Return current UTC-day spend, including reservations by default."""
        if today is not None and (not isinstance(today, date) or isinstance(today, datetime)):
            raise ValidationError("today must be a calendar date.")
        utc_day = today or datetime.now(UTC).date()
        start = datetime.combine(utc_day, time.min, tzinfo=UTC)
        return self.spent(currency, include_pending=include_pending, since=start)

    def iter_entries(
        self,
        *,
        currency: str | None = None,
        status: str | None = None,
        page_size: int = 1000,
    ) -> Iterator[LedgerEntry]:
        """Yield every matching row from one stable SQLite read snapshot."""
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 10000
        ):
            raise ValidationError("page_size must be an integer from 1 to 10000.")
        clauses: list[str] = []
        parameters: list[Any] = []
        if currency is not None:
            clauses.append("currency = ?")
            parameters.append(self._required_text(currency, "currency"))
        if status is not None:
            status_value = status.value if isinstance(status, LedgerStatus) else status
            if status_value not in ("pending", "settled", "failed", "uncertain"):
                raise ValidationError("Invalid ledger status.")
            clauses.append("status = ?")
            parameters.append(status_value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            connection = self._conn()
            connection.execute("BEGIN")
            self._stream_active = True
            try:
                offset = 0
                while True:
                    rows = connection.execute(
                        f"SELECT * FROM ledger_entries{where} "
                        "ORDER BY created_at, request_id LIMIT ? OFFSET ?",
                        [*parameters, page_size, offset],
                    ).fetchall()
                    for row in rows:
                        yield _entry(row)
                    if len(rows) < page_size:
                        break
                    offset += len(rows)
                connection.execute("COMMIT")
            finally:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                self._stream_active = False

    def list_entries(
        self,
        *,
        currency: str | None = None,
        status: str | LedgerStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LedgerEntry]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValidationError("limit must be an integer from 1 to 10000.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValidationError("offset must be a non-negative integer.")
        clauses: list[str] = []
        parameters: list[Any] = []
        if currency is not None:
            clauses.append("currency = ?")
            parameters.append(self._required_text(currency, "currency"))
        if status is not None:
            if status not in ("pending", "settled", "failed", "uncertain"):
                raise ValidationError("Invalid ledger status.")
            clauses.append("status = ?")
            parameters.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.extend((limit, offset))
        with self._lock:
            rows = (
                self._conn()
                .execute(
                    f"SELECT * FROM ledger_entries{where} ORDER BY created_at, request_id "
                    "LIMIT ? OFFSET ?",
                    parameters,
                )
                .fetchall()
            )
        return [_entry(row) for row in rows]

    def find_by_settlement_id(self, settlement_id: str, *, limit: int = 100) -> list[LedgerEntry]:
        """Find ledger entries reconciled by one provider or chain transaction."""
        settlement_id = self._required_text(settlement_id, "settlement_id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValidationError("limit must be an integer from 1 to 10000.")
        with self._lock:
            rows = (
                self._conn()
                .execute(
                    "SELECT * FROM ledger_entries WHERE settlement_id = ? "
                    "ORDER BY created_at, request_id LIMIT ?",
                    (settlement_id, limit),
                )
                .fetchall()
            )
        return [_entry(row) for row in rows]

    def count_entries(
        self,
        *,
        currency: str | None = None,
        status: str | LedgerStatus | None = None,
    ) -> int:
        """Count rows matching optional currency and status filters."""
        clauses: list[str] = []
        parameters: list[Any] = []
        if currency is not None:
            clauses.append("currency = ?")
            parameters.append(self._required_text(currency, "currency"))
        if status is not None:
            status_value = status.value if isinstance(status, LedgerStatus) else status
            if status_value not in ("pending", "settled", "failed", "uncertain"):
                raise ValidationError("Invalid ledger status.")
            clauses.append("status = ?")
            parameters.append(status_value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            result = (
                self._conn()
                .execute(f"SELECT COUNT(*) FROM ledger_entries{where}", parameters)
                .fetchone()
            )
        return int(result[0])

    def export_jsonl(
        self,
        *,
        currency: str | None = None,
        status: str | LedgerStatus | None = None,
        page_size: int = 1000,
    ) -> str:
        entries = self.iter_entries(currency=currency, status=status, page_size=page_size)
        return "".join(
            json.dumps(
                {
                    "request_id": e.request_id,
                    "amount": str(e.amount),
                    "currency": e.currency,
                    "status": e.status,
                    "created_at": e.created_at,
                    "updated_at": e.updated_at,
                    "settlement_id": e.settlement_id,
                    "reason": e.reason,
                    "metadata": e.metadata,
                    "request_fingerprint": e.request_fingerprint,
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
            for e in entries
        )

    @staticmethod
    def _required_text(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{field} must be a non-empty string.")
        return value.strip()
