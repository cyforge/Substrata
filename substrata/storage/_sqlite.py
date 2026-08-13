"""Shared SQLite connection setup for concurrent ledger components."""

from __future__ import annotations

import sqlite3
import time

_BUSY_TIMEOUT_MS = 30_000


def configure_connection(connection: sqlite3.Connection, path: str) -> None:
    """Set durable database defaults, retrying concurrent WAL initialization."""
    connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    if path == ":memory:":
        return
    deadline = time.monotonic() + _BUSY_TIMEOUT_MS / 1000
    delay = 0.01
    while True:
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.25)
