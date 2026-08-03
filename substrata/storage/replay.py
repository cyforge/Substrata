"""Atomic, persistent provider proof claims."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from ..errors import ValidationError
from .ledger import _now


class ReplayStore:
    """Store proof claims across threads, connections, and process restarts."""

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
            "CREATE TABLE IF NOT EXISTS replay_claims ("
            "scope TEXT NOT NULL, proof_id TEXT NOT NULL, digest TEXT, claimed_at TEXT NOT NULL, "
            "PRIMARY KEY(scope, proof_id))"
        )

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> "ReplayStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def claim(self, scope: str, proof_id: str, *, digest: str | None = None) -> bool:
        scope = self._required(scope, "scope")
        proof_id = self._required(proof_id, "proof_id")
        if digest is not None:
            digest = self._required(digest, "digest")
        with self._lock:
            connection = self._connection
            if connection is None:
                raise RuntimeError("ReplayStore is closed.")
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT digest FROM replay_claims WHERE scope = ? AND proof_id = ?",
                    (scope, proof_id),
                ).fetchone()
                if row is not None:
                    if digest is not None and row[0] not in (None, digest):
                        raise ValidationError("Proof ID already claimed with a different digest.")
                    connection.execute("COMMIT")
                    return False
                connection.execute(
                    "INSERT INTO replay_claims(scope, proof_id, digest, claimed_at) "
                    "VALUES (?, ?, ?, ?)",
                    (scope, proof_id, digest, _now()),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _required(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{field} must be a non-empty string.")
        return value.strip()
