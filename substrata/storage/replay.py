"""Atomic, persistent provider proof claims."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from ..errors import ValidationError
from ._sqlite import configure_connection
from .ledger import _now


@dataclass(frozen=True, slots=True)
class ReplayClaim:
    scope: str
    proof_id: str
    digest: str | None
    claimed_at: str


class ReplayStore:
    """Store proof claims across threads, connections, and process restarts."""

    def __init__(self, path: str | Path = "substrata.sqlite3") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = sqlite3.connect(
            self.path, timeout=30, isolation_level=None, check_same_thread=False
        )
        configure_connection(self._connection, self.path)
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
        if self._connection is None:
            raise RuntimeError("ReplayStore is closed.")
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

    def get_claim(self, scope: str, proof_id: str) -> ReplayClaim | None:
        scope = self._required(scope, "scope")
        proof_id = self._required(proof_id, "proof_id")
        with self._lock:
            connection = self._connection
            if connection is None:
                raise RuntimeError("ReplayStore is closed.")
            row = connection.execute(
                "SELECT scope, proof_id, digest, claimed_at FROM replay_claims "
                "WHERE scope = ? AND proof_id = ?",
                (scope, proof_id),
            ).fetchone()
        if row is None:
            return None
        return ReplayClaim(scope=row[0], proof_id=row[1], digest=row[2], claimed_at=row[3])

    def count_claims(self, *, scope: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM replay_claims"
        parameters: tuple[str, ...] = ()
        if scope is not None:
            query += " WHERE scope = ?"
            parameters = (self._required(scope, "scope"),)
        with self._lock:
            connection = self._connection
            if connection is None:
                raise RuntimeError("ReplayStore is closed.")
            return int(connection.execute(query, parameters).fetchone()[0])

    @staticmethod
    def _required(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{field} must be a non-empty string.")
        return value.strip()
