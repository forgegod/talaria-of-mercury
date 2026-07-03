"""MoA truncation verification.

Ports ``~/.hermes/scripts/check_moa_truncation.py`` into the Talaria
feature system. The two signals described in the original docstring
are kept intact:

* **Signal A** — sessions.output_tokens trend under MoA-preset sessions.
  Per-session output should drop substantially after the
  max_tokens reduction; the alert threshold (64k) flags regressions.
* **Signal B** — new occurrences of length-class truncation in
  ``agent.log`` / ``errors.log``. After the fix, zero new occurrences
  are expected.

The log severity gate (only WARNING/ERROR/CRITICAL lines trigger Signal
B) is preserved to avoid false positives from INFO-level user-message
echoes.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from talaria.paths import ResolvedPaths

# Output-token threshold that defines "excessive" for a single MoA session.
# Pre-fix observed max ~165k output_tokens. With max_tokens reduced to 32k,
# a legitimate MoA aggregator run rarely exceeds 32k output. 64k is a soft
# ceiling — high enough to avoid false positives, low enough to catch
# regressions.
MOA_OUTPUT_TOKEN_ALERT = 64_000

# Default look-back window when --days is not specified.
DEFAULT_LOOKBACK_DAYS = 2

#: Cap on how many matching log lines to retain per Signal B report.
MATCHES_RETAINED = 20

# ---------- Log severity + length-class detection ----------
#
# Hermes log lines look like:
#     2026-07-03 12:34:56,789 WARNING agent.chat_completion_helpers: ...
# The severity lives at column 24. We only fire Signal B on WARNING/
# ERROR/CRITICAL so user-message INFO echoes don't trigger false hits.
LOG_LEVEL_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)"
)
_LENGTH_LEVELS = frozenset({"WARNING", "ERROR", "CRITICAL"})

LENGTH_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Hermes runtime message when an API stream ends with finish_reason=length.
    re.compile(r"finish_reason='length'"),
    re.compile(r'finish_reason="length"'),
    # Generic provider-side messages that imply a length cap.
    re.compile(r"Response truncated \(finish_reason='length'\)"),
    re.compile(r"hit max output tokens"),
)
STREAM_DROP_PATTERN = re.compile(
    r"Stream ended with no finish_reason while a tool call's arguments "
    r"were still incomplete"
)


@dataclass(frozen=True)
class SinceWindow:
    """A time window derived from --days / --since / defaults."""

    since_ts: float
    cutoff_iso: str


def resolve_window(*, days: int = DEFAULT_LOOKBACK_DAYS, since: str | None = None) -> SinceWindow:
    """Compute the look-back window as a UTC unix timestamp + ISO string."""
    if since:
        dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        dt = datetime.now(timezone.utc) - timedelta(days=days)
    return SinceWindow(since_ts=dt.timestamp(), cutoff_iso=dt.isoformat())


# ---------- Signal A: per-session output_tokens ----------
def signal_a_output_tokens(state_db: Path, since_ts: float) -> dict[str, Any]:
    """Return the top MoA / coding sessions by output_tokens in the window.

    Shape::

        {
          "ok": bool,
          "error": str | None,
          "window_sessions": int,
          "alert_threshold": int,
          "flagged": [session_dict, ...],
          "sessions": [session_dict, ...],   # up to 15, DESC by output_tokens
        }
    """
    if not state_db.exists():
        return {"ok": False, "error": f"state.db not found: {state_db}",
                "window_sessions": 0, "alert_threshold": MOA_OUTPUT_TOKEN_ALERT,
                "flagged": [], "sessions": []}
    try:
        uri = f"file:{state_db}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
    except sqlite3.OperationalError as exc:
        return {"ok": False, "error": f"cannot open state.db: {exc}",
                "window_sessions": 0, "alert_threshold": MOA_OUTPUT_TOKEN_ALERT,
                "flagged": [], "sessions": []}

    try:
        rows = con.execute(
            """
            SELECT id, model, output_tokens, message_count, api_call_count,
                   datetime(started_at, 'unixepoch') AS started
            FROM sessions
            WHERE started_at >= ?
              AND output_tokens IS NOT NULL
            ORDER BY output_tokens DESC
            LIMIT 15
            """,
            (since_ts,),
        ).fetchall()
    finally:
        con.close()

    sessions = [dict(r) for r in rows]
    flagged = [s for s in sessions
               if s.get("output_tokens") and s["output_tokens"] >= MOA_OUTPUT_TOKEN_ALERT]
    return {
        "ok": True,
        "window_sessions": len(sessions),
        "alert_threshold": MOA_OUTPUT_TOKEN_ALERT,
        "flagged": flagged,
        "sessions": sessions,
    }


# ---------- Signal B: log truncation markers ----------
def signal_b_log_truncations(log_dir: Path, since_ts: float) -> dict[str, Any]:
    """Scan ``agent.log`` + ``errors.log`` for length-class markers.

    Only WARNING/ERROR/CRITICAL lines trigger a hit (INFO echoes are
    excluded). The ``STREAM_DROP_PATTERN`` count is reported separately
    because it is *not* a length event.
    """
    log_files = [log_dir / "agent.log", log_dir / "errors.log"]
    matches: list[dict[str, str]] = []
    stream_drops = 0
    cutoff_iso = datetime.fromtimestamp(since_ts, tz=timezone.utc).isoformat()

    for log in log_files:
        if not log.exists():
            continue
        with log.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if len(line) < 20 or line[4] != "-" or line[7] != "-":
                    continue
                try:
                    line_ts = datetime.fromisoformat(line[:23]).replace(tzinfo=timezone.utc).timestamp()
                except ValueError:
                    continue
                if line_ts < since_ts:
                    continue
                level_match = LOG_LEVEL_RE.match(line)
                level = level_match.group(1) if level_match else "INFO"
                if level in _LENGTH_LEVELS and any(p.search(line) for p in LENGTH_PATTERNS):
                    matches.append({"file": log.name, "line": line.rstrip()})
                if STREAM_DROP_PATTERN.search(line):
                    stream_drops += 1

    return {
        "ok": True,
        "window_start_utc": cutoff_iso,
        "length_class_hits": len(matches),
        "stream_drop_warnings": stream_drops,
        "matches": matches[:MATCHES_RETAINED],
    }


# ---------- Combined verdict ----------
def run(paths: ResolvedPaths, *, days: int = DEFAULT_LOOKBACK_DAYS,
        since: str | None = None) -> dict[str, Any]:
    """Run both signals and assemble a single report dict.

    Returns a structure ready to feed :func:`render_human` or to dump
    as JSON. ``fired`` is True when either signal indicates a problem.
    """
    window = resolve_window(days=days, since=since)
    a = signal_a_output_tokens(paths.state_db, window.since_ts)
    b = signal_b_log_truncations(paths.log_dir, window.since_ts)
    fired = (
        not a.get("ok")
        or bool(a.get("flagged"))
        or b.get("length_class_hits", 0) > 0
    )
    return {
        "profile": paths.profile,
        "state_db": str(paths.state_db),
        "log_dir": str(paths.log_dir),
        "window_start_utc": window.cutoff_iso,
        "signal_a_output_tokens": a,
        "signal_b_log_truncations": b,
        "fired": fired,
    }


# ---------- Human renderer ----------
def render_human(report: dict[str, Any]) -> tuple[int, str]:
    """Format *report* for the terminal. Returns ``(exit_code, text)``."""
    lines: list[str] = []
    fired = False
    a = report["signal_a_output_tokens"]
    b = report["signal_b_log_truncations"]

    lines.append("MoA truncation verification")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Profile: {report['profile']}")
    lines.append(f"state.db: {report['state_db']}")
    lines.append(f"log_dir:  {report['log_dir']}")
    lines.append(f"window:   {report['window_start_utc']} (UTC) -> now")
    lines.append("")

    # Signal A
    lines.append(f"Signal A: MoA / coding session output_tokens "
                 f"(alert threshold = {MOA_OUTPUT_TOKEN_ALERT:,})")
    if not a.get("ok"):
        lines.append(f"  ERROR: {a.get('error')}")
        fired = True
    elif a["window_sessions"] == 0:
        lines.append("  No sessions in window — nothing to check.")
    else:
        lines.append(f"  Top sessions in window: {a['window_sessions']}")
        for s in a["sessions"][:8]:
            flag = "  ⚠" if s in a["flagged"] else "   "
            lines.append(
                f"{flag} {s['id']}  model={s['model']:24s} "
                f"output={s['output_tokens']:>7,}  msgs={s['message_count']:>3} "
                f"api={s['api_call_count']:>3}  started={s['started']}"
            )
        if a["flagged"]:
            fired = True
            lines.append("")
            lines.append(f"  ⚠ {len(a['flagged'])} session(s) above alert threshold.")
            lines.append("    Possible causes:")
            lines.append("      - MoA preset max_tokens still too high (target: 32k)")
            lines.append("      - Coding alias still routed to MoA — switch to direct")
            lines.append("        model for long tool-heavy runs.")
            lines.append("    Continue: re-check MoA block in config.yaml, then re-run.")

    # Signal B
    lines.append("")
    lines.append(f"Signal B: length-class truncation in logs (since {b['window_start_utc']})")
    if not b.get("ok"):
        lines.append(f"  ERROR: {b.get('error')}")
        fired = True
    else:
        lines.append(f"  length_class_hits: {b['length_class_hits']}")
        lines.append(f"  stream_drop_warnings (NOT length events, informational): "
                     f"{b['stream_drop_warnings']}")
        for m in b["matches"]:
            lines.append(f"  • {m['file']}: {m['line'][:200]}")
        if b["length_class_hits"] > 0:
            fired = True
            lines.append("")
            lines.append("  ⚠ Length-class truncation markers found in logs.")
            lines.append("    Likely cause: a provider is silently capping MoA output")
            lines.append("    below the request ceiling. Continue:")
            lines.append("      1. Identify the session/model from the matching log line.")
            lines.append("      2. Lower moa.presets.coding.max_tokens further (try 16k).")
            lines.append("      3. Re-run this script after the next session.")

    lines.append("")
    lines.append("=" * 60)
    if fired:
        lines.append("VERDICT: at least one signal fired — review guidance above.")
        return 1, "\n".join(lines)
    lines.append("VERDICT: clean — both signals within tolerance.")
    return 0, "\n".join(lines)


# ---------- Resolution-descriptor helper ----------
def show_resolution(paths: ResolvedPaths) -> str:
    """Pretty-print the paths that would be inspected, for debugging."""
    import json
    return json.dumps({
        "profile": paths.profile,
        "hermes_root": str(paths.hermes_root),
        "resolved_state_db": str(paths.state_db),
        "resolved_log_dir": str(paths.log_dir),
    }, indent=2)