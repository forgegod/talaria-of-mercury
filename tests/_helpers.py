"""Test helper utilities (importable from any test module)."""

from __future__ import annotations

import sqlite3
from pathlib import Path


# Minimal session columns required by the doctor signal-a SQL
# (kept exactly as-is for backward compatibility with the existing
# test fixtures).
_SESSIONS_MIN = """
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    output_tokens INTEGER,
    message_count INTEGER,
    api_call_count INTEGER,
    started_at INTEGER
)
"""

# Full Hermes session columns used by the doctor detectors.
# Mirrors the production schema in `state.db` (see vc-client
# introspection at session start).
_SESSIONS_FULL = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    user_id TEXT,
    session_key TEXT,
    chat_id TEXT,
    chat_type TEXT,
    thread_id TEXT,
    model TEXT,
    model_config TEXT,
    system_prompt TEXT,
    parent_session_id TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    cwd TEXT,
    git_branch TEXT,
    git_repo_root TEXT,
    billing_provider TEXT,
    billing_base_url TEXT,
    billing_mode TEXT,
    estimated_cost_usd REAL,
    actual_cost_usd REAL,
    cost_status TEXT,
    cost_source TEXT,
    pricing_version TEXT,
    title TEXT,
    api_call_count INTEGER DEFAULT 0,
    handoff_state TEXT,
    handoff_platform TEXT,
    handoff_error TEXT,
    compression_failure_cooldown_until REAL,
    compression_failure_error TEXT,
    rewind_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0
)
"""

_MESSAGES_MIN = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    finish_reason TEXT,
    timestamp REAL NOT NULL,
    tool_name TEXT,
    tool_call_id TEXT
)
"""

_COMPRESSION_LOCKS = """
CREATE TABLE compression_locks (
    session_id TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    acquired_at REAL NOT NULL,
    expires_at REAL NOT NULL
)
"""


def make_sessions_db(path: Path, rows: list[tuple]) -> None:
    """Create the minimal ``sessions`` table and insert *rows*.

    Each row is ``(id, model, output_tokens, message_count, api_call_count, started_at)``
    where ``started_at`` is a unix timestamp passed through unchanged.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(_SESSIONS_MIN)
    con.executemany(
        "INSERT INTO sessions (id, model, output_tokens, message_count, api_call_count, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()


def make_full_state_db(
    path: Path,
    *,
    sessions: list[dict] | None = None,
    messages: list[dict] | None = None,
    compression_locks: list[dict] | None = None,
) -> None:
    """Create a state.db with the full Hermes schema (sessions, messages, locks).

    Parameters mirror the production tables the `talaria hermes doctor`
    actually reads. Each row is a dict whose keys are subset of the
    table columns; missing columns default to NULL/0 per the schema.

    Use this for the doctor tests; the minimal ``make_sessions_db``
    stays for any test that only needs the signal-a fixture format.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    # Python's ``sqlite3.executescript`` issues each CREATE as a
    # separate ``sqlite3_exec`` call internally; that helper REQUIRES
    # a semicolon between statements. Newline-only separation is
    # not enough. We therefore join the schemas with explicit ``;``
    # separators before passing the combined script to executescript.
    con.executescript(
        _SESSIONS_FULL.rstrip().rstrip(";")
        + ";\n"
        + _MESSAGES_MIN.strip()
        + ";\n"
        + _COMPRESSION_LOCKS.strip()
    )
    if sessions:
        cols = list(sessions[0].keys())
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        con.executemany(
            f"INSERT INTO sessions ({col_list}) VALUES ({placeholders})",
            [tuple(r[c] for c in cols) for r in sessions],
        )
    if messages:
        cols = list(messages[0].keys())
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        con.executemany(
            f"INSERT INTO messages ({col_list}) VALUES ({placeholders})",
            [tuple(r[c] for c in cols) for r in messages],
        )
    if compression_locks:
        cols = list(compression_locks[0].keys())
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        con.executemany(
            f"INSERT INTO compression_locks ({col_list}) VALUES ({placeholders})",
            [tuple(r[c] for c in cols) for r in compression_locks],
        )
    con.commit()
    con.close()