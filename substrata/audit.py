"""Append-only, redacted audit event storage."""

from __future__ import annotations

import json
import sqlite3
import threading
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
        clean_data = _redact(data or {})
        if not isinstance(clean_data, dict):
            raise ValidationError("data must be a dictionary.")
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
            cursor = connection.execute(
                "INSERT INTO audit_events(event_type, request_id, created_at, data) "
                "VALUES (?, ?, ?, ?)",
                (event_type, request_id, _now(), serialized),
            )
            return int(cursor.lastrowid)

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
        parameters.extend((limit, offset))
        with self._lock:
            connection = self._connection
            if connection is None:
                raise RuntimeError("AuditLog is closed.")
            rows = connection.execute(
                "SELECT event_id, event_type, request_id, created_at, data "
                f"FROM audit_events{where} "
                "ORDER BY event_id LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
        return [
            {
                "event_id": row[0],
                "event_type": row[1],
                "request_id": row[2],
                "created_at": row[3],
                "data": json.loads(row[4]),
            }
            for row in rows
        ]

    def export_jsonl(self, *, request_id: str | None = None, event_type: str | None = None) -> str:
        return "".join(
            json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
            for event in self.list_events(request_id=request_id, event_type=event_type, limit=10000)
        )

    @staticmethod
    def _required(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{field} must be a non-empty string.")
        return value.strip()
