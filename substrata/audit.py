"""Append-only, redacted audit event storage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .storage.ledger import _now

_SENSITIVE_PARTS = (
    "secret",
    "private",
    "token",
    "credential",
    "password",
    "apikey",
    "authorization",
    "signature",
    "signingkey",
)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if any(
                part in "".join(char for char in str(key).lower() if char.isalnum())
                for part in _SENSITIVE_PARTS
            )
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


class AuditLog:
    """Append events with sensitive fields removed before persistence."""

    def __init__(self, path: str | Path = "substrata.sqlite3") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = sqlite3.connect(
            self.path, timeout=30, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA busy_timeout = 30000")
        if self.path != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS audit_events ("
            "event_id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, "
            "request_id TEXT, "
            "created_at TEXT NOT NULL, data TEXT NOT NULL)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS audit_request_idx ON audit_events(request_id, event_id)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS audit_chain ("
            "event_id INTEGER PRIMARY KEY REFERENCES audit_events(event_id), "
            "previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL)"
        )
        self._backfill_chain()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def append(
        self,
        event_type: str,
        *,
        request_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> int:
        event_type = self._required(event_type, "event_type")
        if request_id is not None:
            request_id = self._required(request_id, "request_id")
        if data is not None and not isinstance(data, dict):
            raise ValidationError("data must be a dictionary.")
        clean_data = _redact(data if data is not None else {})
        try:
            serialized = json.dumps(
                clean_data, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError("data must contain JSON-compatible values.") from exc
        with self._lock:
            connection = self._connection
            if connection is None:
                raise RuntimeError("AuditLog is closed.")
            connection.execute("BEGIN IMMEDIATE")
            try:
                previous = connection.execute(
                    "SELECT event_hash FROM audit_chain ORDER BY event_id DESC LIMIT 1"
                ).fetchone()
                previous_hash = previous[0] if previous is not None else "0" * 64
                created_at = _now()
                cursor = connection.execute(
                    "INSERT INTO audit_events(event_type, request_id, created_at, data) "
                    "VALUES (?, ?, ?, ?)",
                    (event_type, request_id, created_at, serialized),
                )
                event_id = int(cursor.lastrowid)
                event_hash = self._hash_event(
                    previous_hash,
                    event_id,
                    event_type,
                    request_id,
                    created_at,
                    serialized,
                )
                connection.execute(
                    "INSERT INTO audit_chain(event_id, previous_hash, event_hash) VALUES (?, ?, ?)",
                    (event_id, previous_hash, event_hash),
                )
                connection.execute("COMMIT")
                return event_id
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def iter_events(
        self,
        *,
        request_id: str | None = None,
        event_type: str | None = None,
        page_size: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        """Yield every matching event from one stable read snapshot."""
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 10000
        ):
            raise ValidationError("page_size must be an integer from 1 to 10000.")
        clauses: list[str] = []
        parameters: list[Any] = []
        if request_id is not None:
            clauses.append("request_id = ?")
            parameters.append(self._required(request_id, "request_id"))
        if event_type is not None:
            clauses.append("event_type = ?")
            parameters.append(self._required(event_type, "event_type"))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        where_events = where.replace("request_id", "e.request_id").replace(
            "event_type", "e.event_type"
        )
        with self._lock:
            connection = self._connection
            if connection is None:
                raise RuntimeError("AuditLog is closed.")
            connection.execute("BEGIN")
            try:
                offset = 0
                while True:
                    rows = connection.execute(
                        "SELECT e.event_id, e.event_type, e.request_id, e.created_at, e.data, "
                        "c.previous_hash, c.event_hash "
                        "FROM audit_events AS e LEFT JOIN audit_chain AS c USING(event_id) "
                        f"{where_events} "
                        "ORDER BY e.event_id LIMIT ? OFFSET ?",
                        [*parameters, page_size, offset],
                    ).fetchall()
                    for row in rows:
                        yield {
                            "event_id": row[0],
                            "event_type": row[1],
                            "request_id": row[2],
                            "created_at": row[3],
                            "data": json.loads(row[4]),
                            "previous_hash": row[5],
                            "event_hash": row[6],
                        }
                    if len(rows) < page_size:
                        break
                    offset += len(rows)
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def list_events(
        self,
        *,
        request_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValidationError("limit must be an integer from 1 to 10000.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValidationError("offset must be a non-negative integer.")
        clauses: list[str] = []
        parameters: list[Any] = []
        if request_id is not None:
            clauses.append("request_id = ?")
            parameters.append(self._required(request_id, "request_id"))
        if event_type is not None:
            clauses.append("event_type = ?")
            parameters.append(self._required(event_type, "event_type"))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        where_events = where.replace("request_id", "e.request_id").replace(
            "event_type", "e.event_type"
        )
        parameters.extend((limit, offset))
        with self._lock:
            connection = self._connection
            if connection is None:
                raise RuntimeError("AuditLog is closed.")
            rows = connection.execute(
                "SELECT e.event_id, e.event_type, e.request_id, e.created_at, e.data, "
                "c.previous_hash, c.event_hash FROM audit_events AS e "
                "LEFT JOIN audit_chain AS c USING(event_id) "
                f"{where_events} "
                "ORDER BY e.event_id LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
        return [
            {
                "event_id": row[0],
                "event_type": row[1],
                "request_id": row[2],
                "created_at": row[3],
                "data": json.loads(row[4]),
                "previous_hash": row[5],
                "event_hash": row[6],
            }
            for row in rows
        ]

    def verify_chain(self) -> bool:
        """Return whether stored events match their append-only hash chain."""
        with self._lock:
            connection = self._connection
            if connection is None:
                raise RuntimeError("AuditLog is closed.")
            rows = connection.execute(
                "SELECT e.event_id, e.event_type, e.request_id, e.created_at, e.data, "
                "c.previous_hash, c.event_hash FROM audit_events AS e "
                "LEFT JOIN audit_chain AS c USING(event_id) ORDER BY e.event_id"
            ).fetchall()
        expected_previous = "0" * 64
        for row in rows:
            if row[5] != expected_previous:
                return False
            expected_hash = self._hash_event(
                expected_previous, row[0], row[1], row[2], row[3], row[4]
            )
            if row[6] != expected_hash:
                return False
            expected_previous = expected_hash
        return True

    def _backfill_chain(self) -> None:
        """Create a baseline chain for audit rows written before chain support."""
        connection = self._connection
        assert connection is not None
        connection.execute("BEGIN IMMEDIATE")
        try:
            chain_count = connection.execute("SELECT COUNT(*) FROM audit_chain").fetchone()[0]
            if chain_count:
                connection.execute("COMMIT")
                return
            rows = connection.execute(
                "SELECT event_id, event_type, request_id, created_at, data "
                "FROM audit_events ORDER BY event_id"
            ).fetchall()
            previous_hash = "0" * 64
            for row in rows:
                event_hash = self._hash_event(previous_hash, row[0], row[1], row[2], row[3], row[4])
                connection.execute(
                    "INSERT INTO audit_chain(event_id, previous_hash, event_hash) VALUES (?, ?, ?)",
                    (row[0], previous_hash, event_hash),
                )
                previous_hash = event_hash
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _hash_event(
        previous_hash: str,
        event_id: int,
        event_type: str,
        request_id: str | None,
        created_at: str,
        serialized_data: str,
    ) -> str:
        canonical = json.dumps(
            [event_id, event_type, request_id, created_at, json.loads(serialized_data)],
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(f"{previous_hash}\n{canonical}".encode("utf-8")).hexdigest()

    def count_events(self, *, request_id: str | None = None, event_type: str | None = None) -> int:
        """Count retained events matching optional request and type filters."""
        clauses: list[str] = []
        parameters: list[Any] = []
        if request_id is not None:
            clauses.append("request_id = ?")
            parameters.append(self._required(request_id, "request_id"))
        if event_type is not None:
            clauses.append("event_type = ?")
            parameters.append(self._required(event_type, "event_type"))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            connection = self._connection
            if connection is None:
                raise RuntimeError("AuditLog is closed.")
            return int(
                connection.execute(
                    f"SELECT COUNT(*) FROM audit_events{where}", parameters
                ).fetchone()[0]
            )

    def export_jsonl(self, *, request_id: str | None = None, event_type: str | None = None) -> str:
        return "".join(
            json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
            for event in self.iter_events(request_id=request_id, event_type=event_type)
        )

    @staticmethod
    def _required(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{field} must be a non-empty string.")
        return value.strip()
