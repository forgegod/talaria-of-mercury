"""Test helper utilities (importable from any test module)."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def make_sessions_db(path: Path, rows: list[tuple]) -> None:
    """Create a minimal ``sessions`` table and insert *rows*.

    Each row is ``(id, model, output_tokens, message_count, api_call_count, started_at)``
    where ``started_at`` is a unix timestamp passed through unchanged.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY,
            model TEXT NOT NULL,
            output_tokens INTEGER,
            message_count INTEGER,
            api_call_count INTEGER,
            started_at INTEGER
        )
        """
    )
    con.executemany(
        "INSERT INTO sessions (id, model, output_tokens, message_count, api_call_count, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()