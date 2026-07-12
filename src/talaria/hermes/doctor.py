"""Profile anomaly detector — `talaria hermes doctor`.

Twelve structured detectors run against the resolved profile's
``state.db``, ``logs/``, and skill registry
(``<skills_root>/.hub/lock.json`` + ``skills.disabled`` in
``config.yaml``). Findings are reported; no writes by default. The
free-flight curator pass (see
:mod:`talaria.hermes.doctor_free_flight`) is the only LLM use —
it analyses the selected log files plus a redacted ``config.yaml``
with the operator's configured ``_curator`` model and returns both
``anomaly`` findings and ``config_suggestion`` findings. The
``--apply-curator-suggestions`` flag (with ``--dry-run`` to preview)
writes the curator's config_suggestion findings to ``config.yaml``
via the same atomic backup writer used by :mod:`talaria.hermes.auxiliary``.

Findings come in two kinds and only one is actionable through this CLI:

* ``anomaly`` (deterministic detectors + free-flight) — diagnostic
  evidence about the profile. There is **no tactical action** for an
  anomaly finding; it is reported and the operator decides what to do.
  ``--apply-curator-suggestions`` does NOT act on anomalies.
* ``config_suggestion`` (free-flight only) — a curator-model
  recommendation carrying a ``yaml_path`` + ``suggested_value``. These
  are the only findings ``--apply-curator-suggestions`` writes; the
  apply path filters the findings list to ``id.startswith("free_flight:config:")``
  and ignores everything else.

A second opt-in remediation path covers three deterministic findings
that have an unambiguous local fix: ``--prune-stale-locks``,
``--close-zombies``, and ``--prune-ghost-sessions``. Each defaults
to dry-run preview; the shared ``--apply`` flag is the gate that
turns preview into write. See :func:`apply_tactical_actions` for
the per-class contracts.

The contract:

* Read-only access to ``state.db``, the profile's ``logs/``, and (for
  the free-flight pass) ``config.yaml``. No writes without
  ``--apply-curator-suggestions``.
* Each detector is a pure function returning a
  :class:`DetectorResult`.
* The orchestrator runs every detector, runs the free-flight pass
  (default on; ``--no-free-flight`` to opt out), and applies
  ``config_suggestion`` findings when ``--apply-curator-suggestions``
  is passed. A ``--dry-run`` previews the apply without writing.
* The exit code is 1 if any deterministic detector or free-flight
  ``anomaly`` finding fires; 0 if everything is clean. (A
  ``config_suggestion`` finding never fires; the operator decides
  whether to apply.)

Detector inventory (see ``DETECTOR_IDS`` and the operator-facing
catalog table in ``hermes/AGENTS.md``):

* ``truncation_output``     — sessions with ``output_tokens`` above
  ``OUTPUT_TOKEN_ALERT``.
* ``truncation_finish_reason`` — messages with ``finish_reason='length'``
  in the window. Catches the *server-side* length cap the SQL
  column cannot see.
* ``truncation_log_markers`` — ``WARNING|ERROR|CRITICAL`` lines in any
  ``*.log`` file that match a length-class pattern.
* ``stream_drops``          — ``WARNING|ERROR|CRITICAL`` lines matching
  the mid-tool-call stream-drop pattern, above a configurable rate.
* ``compression_stale_locks`` — ``compression_locks`` rows whose
  ``expires_at`` is in the past. Stale locks block the next
  compressor and indicate a crashed process.
* ``compression_failures``  — sessions with
  ``compression_failure_error IS NOT NULL`` in the window.
* ``rewinds``               — sessions with ``rewind_count`` above
  a threshold.
* ``handoff_errors``        — sessions with ``handoff_error IS NOT NULL``
  in the window.
* ``cost_anomalies``        — sessions with ``cost_status`` outside
  ``{ok, paid, free, estimated}``, or
  ``estimated_cost_usd`` diverging from ``actual_cost_usd`` by
  more than 25 %.
* ``zombie_sessions``       — sessions with ``ended_at IS NULL`` and
  ``started_at`` older than ``ZOMBIE_THRESHOLD_SECONDS``.
* ``ghost_sessions``        — sessions with no ``messages`` rows in
  the window. Indicates an aborted create.
* ``skill_index_drift``     — drift between the on-disk skill walk,
  ``<skills_root>/.hub/lock.json``, and ``skills.disabled`` in
  ``config.yaml``. Names that exist in only some of the three
  sources fire the alert; see :mod:`talaria.hermes.skill_index`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from talaria.paths import ResolvedPaths


# ---------------- Log-file discovery + Signal-B scan ----------------
#
# Part of ``doctor``: ``discover_log_files`` and
# ``scan_log_truncations`` are consumed by the ``truncation_log_markers``
# and ``stream_drops`` detectors.

#: Default log-file patterns discovered in a profile's ``logs/`` directory.
#: Matches every active ``*.log`` plus rotated copies (``*.log.N``,
#: ``*.log.N.gz``). Curated snapshot directories (``logs/curator/<ts>/``)
#: are excluded by default; opt in via ``include_curator=True``.
LOG_FILE_GLOBS: tuple[str, ...] = ("*.log", "*.log.*")

#: Subdirectory under ``logs/`` that holds curator snapshot trees.
CURATOR_SUBDIR = "curator"

#: Hermes log lines look like:
#:     2026-07-03 12:34:56,789 WARNING agent.chat_completion_helpers: ...
#: The severity lives at column 24. We only fire Signal B on WARNING/
#: ERROR/CRITICAL so user-message INFO echoes don't trigger false hits.
LOG_LEVEL_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)"
)
_LENGTH_LEVELS = frozenset({"WARNING", "ERROR", "CRITICAL"})

#: Patterns that indicate a length-class truncation event.
LENGTH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"finish_reason='length'"),
    re.compile(r'finish_reason="length"'),
    re.compile(r"Response truncated \(finish_reason='length'\)"),
    re.compile(r"hit max output tokens"),
)

#: Pattern for mid-tool-call stream drops (not a length event).
STREAM_DROP_PATTERN = re.compile(
    r"Stream ended with no finish_reason while a tool call's arguments "
    r"were still incomplete"
)

#: Cap on how many matching log lines to retain per report.
MATCHES_RETAINED = 20


def discover_log_files(
    log_dir: Path,
    *,
    include_curator: bool = False,
) -> list[Path]:
    """Return every log file under *log_dir* the log scan should read.

    Discovery is **all** ``*.log`` and ``*.log.*`` files at the top level
    of ``log_dir`` (the active file plus every rotated copy: ``agent.log``,
    ``agent.log.1``, ``agent.log.1.gz``, etc.). The look-back window
    is then applied at the *line* level so only the part of each file
    that is X days old or newer contributes to the verdict.

    ``logs/curator/<ts>/`` snapshot directories are excluded by default.
    Pass ``include_curator=True`` to walk them too.

    Files are returned sorted by name so the per-file breakdown in
    reports is deterministic.
    """
    if not log_dir.exists() or not log_dir.is_dir():
        return []
    seen: set[Path] = set()
    for pattern in LOG_FILE_GLOBS:
        for p in log_dir.glob(pattern):
            if not p.is_file():
                continue
            seen.add(p.resolve())
    if include_curator:
        curator = log_dir / CURATOR_SUBDIR
        if curator.is_dir():
            for p in curator.rglob("*.log*"):
                if p.is_file():
                    seen.add(p.resolve())
    return sorted(seen)


def scan_log_truncations(
    log_files: list[Path],
    since_ts: float,
) -> dict[str, Any]:
    """Scan every file in *log_files* for length-class markers + stream drops.

    Only WARNING/ERROR/CRITICAL lines trigger a length hit (INFO echoes
    are excluded). The ``STREAM_DROP_PATTERN`` count is reported
    separately because it is *not* a length event.

    The *since_ts* window is applied per line: lines older than the
    window are skipped, even within a long rotated file.
    """
    matches: list[dict[str, str]] = []
    stream_drops = 0
    cutoff_iso = datetime.fromtimestamp(since_ts, tz=timezone.utc).isoformat()
    per_file: list[dict[str, Any]] = []
    files_scanned = 0

    for log in log_files:
        if not log.exists():
            per_file.append({
                "path": str(log),
                "scanned": False,
                "reason": "missing",
                "length_class_hits": 0,
                "stream_drop_warnings": 0,
            })
            continue
        file_hits = 0
        file_drops = 0
        with log.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if len(line) < 20 or line[4] != "-" or line[7] != "-":
                    continue
                try:
                    line_ts = datetime.fromisoformat(line[:23]).replace(
                        tzinfo=timezone.utc,
                    ).timestamp()
                except ValueError:
                    continue
                if line_ts < since_ts:
                    continue
                level_match = LOG_LEVEL_RE.match(line)
                level = level_match.group(1) if level_match else "INFO"
                if level in _LENGTH_LEVELS and any(p.search(line) for p in LENGTH_PATTERNS):
                    matches.append({"file": log.name, "line": line.rstrip()})
                    file_hits += 1
                if STREAM_DROP_PATTERN.search(line):
                    stream_drops += 1
                    file_drops += 1
        files_scanned += 1
        per_file.append({
            "path": str(log),
            "scanned": True,
            "length_class_hits": file_hits,
            "stream_drop_warnings": file_drops,
        })

    return {
        "ok": True,
        "window_start_utc": cutoff_iso,
        "length_class_hits": len(matches),
        "stream_drop_warnings": stream_drops,
        "files_scanned": files_scanned,
        "files_missing": sum(1 for f in per_file if not f["scanned"]),
        "per_file": per_file,
        "matches": matches[:MATCHES_RETAINED],
    }


# ---------------- Tunables ----------------

#: Output-token alert threshold for ``truncation_output``. 64k is a
#: soft ceiling — high enough to avoid false positives, low enough to
#: catch regressions (pre-fix observed max ~165k, request ceiling 32k).
OUTPUT_TOKEN_ALERT = 64_000

#: Rewind threshold above which a session is treated as a rewind anomaly.
#: ``rewind_count == 1`` is normal (operator undid one turn);
#: ``rewind_count >= 3`` is a behavioural signal worth surfacing.
REWIND_ALERT = 3

#: Sessions with ``rewind_count`` between this value and ``REWIND_ALERT``
#: are flagged as ``borderline=True`` on the detector result so the
#: renderer can surface them for operator awareness.
REWIND_BORDERLINE = 2

#: Cost divergence threshold (fraction) for the cost_anomalies detector.
#: ``abs(estimated - actual) / max(estimated, actual) > 0.25`` is a
#: divergence signal; in [0.05, 0.25] is borderline and flagged as
#: ``borderline=True`` on the detector result.
COST_DIVERGENCE_ALERT = 0.25
COST_DIVERGENCE_BORDERLINE = 0.05

#: Allowed ``cost_status`` values. Anything else is an anomaly.
COST_STATUS_OK = frozenset({"ok", "paid", "free", "estimated"})

#: Sessions with ``ended_at IS NULL`` and ``started_at`` older than this
#: are zombies. Default 24 h.
ZOMBIE_THRESHOLD_SECONDS = 24 * 3600

#: Default look-back window for state.db–backed detectors.
DEFAULT_LOOKBACK_DAYS = 2

#: Stream-drops above this count per window are an anomaly; below
#: ``STREAM_DROPS_BORDERLINE`` is borderline.
STREAM_DROPS_ALERT = 10
STREAM_DROPS_BORDERLINE = 3

#: Detector identifier constants — used in ``--only=``, ``--skip=`` and
#: in the report's ``per_detector`` list.
TRUNCATION_OUTPUT = "truncation_output"
TRUNCATION_FINISH_REASON = "truncation_finish_reason"
TRUNCATION_LOG_MARKERS = "truncation_log_markers"
STREAM_DROPS = "stream_drops"
COMPRESSION_STALE_LOCKS = "compression_stale_locks"
COMPRESSION_FAILURES = "compression_failures"
REWIND = "rewinds"
HANDOFF_ERRORS = "handoff_errors"
COST_ANOMALIES = "cost_anomalies"
ZOMBIE_SESSIONS = "zombie_sessions"
GHOST_SESSIONS = "ghost_sessions"
SKILL_INDEX_DRIFT = "skill_index_drift"


#: Canonical detector inventory. Order = display order. Every detector
#: is a confident detector — its verdict is decided in pure Python with
#: no model call. The free-flight curator pass is the only LLM use.
DETECTOR_IDS: tuple[str, ...] = (
    TRUNCATION_OUTPUT,
    TRUNCATION_FINISH_REASON,
    TRUNCATION_LOG_MARKERS,
    STREAM_DROPS,
    COMPRESSION_STALE_LOCKS,
    COMPRESSION_FAILURES,
    REWIND,
    HANDOFF_ERRORS,
    COST_ANOMALIES,
    ZOMBIE_SESSIONS,
    GHOST_SESSIONS,
    SKILL_INDEX_DRIFT,
)

#: Canonical list of every detector id the orchestrator runs.
#: Every detector is a *confident* detector — its verdict is decided
#: in pure Python with no model call. The free-flight curator pass is
#: the only place the model is consulted (see
#: :mod:`talaria.hermes.doctor_free_flight`).
CONFIDENT_DETECTORS: frozenset[str] = frozenset(DETECTOR_IDS)

#: Severity values for ``DetectorResult.severity``.
SEVERITY_INFO = "info"
SEVERITY_WARN = "warn"
SEVERITY_ALERT = "alert"


# ---------------- Dataclasses ----------------

@dataclass(frozen=True)
class DetectorResult:
    """Outcome of running a single detector.

    Attributes:
        id: detector identifier (e.g. ``"zombie_sessions"``).
        severity: ``info`` / ``warn`` / ``alert``.
        summary: one-line human-readable verdict for the renderer.
        evidence: JSON-serialisable payload that a human operator can
            inspect for detail.
        fired: True when ``severity`` is ``warn`` or ``alert``. The
            orchestrator's ``fired`` flag is the OR of every detector's
            ``fired`` field.
        borderline: True when the deterministic verdict is uncertain
            (e.g. rewind count or cost divergence falls between the
            borderline threshold and the alert threshold). Surface-only
            — does not trigger a model call.
        adjudicated: True when the result originates from the
            free-flight curator pass (whose findings carry their own
            model verdict). Always False for deterministic detectors.
        model_verdict: the curator model's reply payload, if any
            (free-flight findings only).
    """

    id: str
    severity: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    fired: bool = False
    borderline: bool = False
    adjudicated: bool = False
    model_verdict: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "summary": self.summary,
            "evidence": self.evidence,
            "fired": self.fired,
            "borderline": self.borderline,
            "adjudicated": self.adjudicated,
            "model_verdict": self.model_verdict,
        }


@dataclass(frozen=True)
class SinceWindow:
    since_ts: float
    cutoff_iso: str


# ---------------- Window + path helpers ----------------

def resolve_window(*, days: int = DEFAULT_LOOKBACK_DAYS, since: str | None = None) -> SinceWindow:
    if since:
        dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        dt = datetime.now(timezone.utc) - timedelta(days=days)
    return SinceWindow(since_ts=dt.timestamp(), cutoff_iso=dt.isoformat())


def _open_state_db_readonly(path: Path) -> sqlite3.Connection | None:
    """Open *path* read-only via URI. Returns ``None`` if it does not exist."""
    if not path.exists():
        return None
    try:
        uri = f"file:{path}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.OperationalError:
        return None


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Convert a list of :class:`sqlite3.Row` to a list of dicts.

    ``dict(row)`` does not work on a ``sqlite3.Row`` because the row
    object iterates as a *sequence of column values*, not as a
    mapping. The mapping form requires the row to expose ``keys()``
    and ``__getitem__`` — which ``sqlite3.Row`` does — but the
    ``dict()`` constructor picks up the sequence protocol first.
    """
    return [{k: r[k] for k in r.keys()} for r in rows]


# ---------------- Per-detector SQL helpers ----------------

def _top_output_sessions(con: sqlite3.Connection, since_ts: float, limit: int = 25) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT id, model, output_tokens, message_count, api_call_count,
               datetime(started_at, 'unixepoch') AS started
        FROM sessions
        WHERE started_at >= ?
          AND output_tokens IS NOT NULL
        ORDER BY output_tokens DESC
        LIMIT ?
        """,
        (since_ts, limit),
    ).fetchall()
    return _rows_to_dicts(rows)


def _finish_reason_length_sessions(con: sqlite3.Connection, since_ts: float) -> list[dict[str, Any]]:
    """Sessions with at least one ``finish_reason='length'`` message in window."""
    rows = con.execute(
        """
        SELECT s.id, s.model, s.output_tokens,
               datetime(s.started_at, 'unixepoch') AS started,
               COUNT(m.id) AS length_messages
        FROM sessions s
        JOIN messages m ON m.session_id = s.id
        WHERE m.finish_reason = 'length'
          AND m.timestamp >= ?
        GROUP BY s.id
        ORDER BY length_messages DESC, s.started_at DESC
        """,
        (since_ts,),
    ).fetchall()
    return _rows_to_dicts(rows)


def _compression_locks(con: sqlite3.Connection, now_ts: float) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT session_id, holder, acquired_at, expires_at,
               (expires_at - ?) AS seconds_remaining
        FROM compression_locks
        WHERE expires_at < ?
        ORDER BY expires_at ASC
        """,
        (now_ts, now_ts),
    ).fetchall()
    return _rows_to_dicts(rows)


def _compression_failure_sessions(con: sqlite3.Connection, since_ts: float) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT id, model, compression_failure_error,
               compression_failure_cooldown_until,
               datetime(started_at, 'unixepoch') AS started
        FROM sessions
        WHERE compression_failure_error IS NOT NULL
          AND started_at >= ?
        ORDER BY started_at DESC
        """,
        (since_ts,),
    ).fetchall()
    return _rows_to_dicts(rows)


def _rewind_sessions(con: sqlite3.Connection, since_ts: float) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT id, model, rewind_count,
               datetime(started_at, 'unixepoch') AS started
        FROM sessions
        WHERE rewind_count >= ?
          AND started_at >= ?
        ORDER BY rewind_count DESC, started_at DESC
        """,
        (REWIND_BORDERLINE, since_ts),
    ).fetchall()
    return _rows_to_dicts(rows)


def _handoff_error_sessions(con: sqlite3.Connection, since_ts: float) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT id, model, handoff_state, handoff_platform, handoff_error,
               datetime(started_at, 'unixepoch') AS started
        FROM sessions
        WHERE handoff_error IS NOT NULL
          AND started_at >= ?
        ORDER BY started_at DESC
        """,
        (since_ts,),
    ).fetchall()
    return _rows_to_dicts(rows)


def _cost_anomaly_sessions(con: sqlite3.Connection, since_ts: float) -> list[dict[str, Any]]:
    """Sessions with bad ``cost_status`` or high estimated-vs-actual divergence."""
    rows = con.execute(
        """
        SELECT id, model, cost_status, cost_source,
               ROUND(estimated_cost_usd, 6) AS estimated_cost_usd,
               ROUND(actual_cost_usd, 6) AS actual_cost_usd,
               CASE
                   WHEN estimated_cost_usd IS NULL OR actual_cost_usd IS NULL THEN NULL
                   WHEN MAX(estimated_cost_usd, actual_cost_usd) = 0 THEN 0
                   ELSE ROUND(
                       ABS(estimated_cost_usd - actual_cost_usd) /
                       MAX(estimated_cost_usd, actual_cost_usd), 4)
               END AS divergence,
               datetime(started_at, 'unixepoch') AS started
        FROM sessions
        WHERE started_at >= ?
          AND (
               (cost_status IS NOT NULL AND cost_status NOT IN ('ok', 'paid', 'free', 'estimated'))
               OR (estimated_cost_usd > 0 AND actual_cost_usd IS NULL)
               OR (estimated_cost_usd IS NOT NULL AND actual_cost_usd IS NOT NULL
                   AND MAX(estimated_cost_usd, actual_cost_usd) > 0
                   AND ABS(estimated_cost_usd - actual_cost_usd) /
                       MAX(estimated_cost_usd, actual_cost_usd) > ?)
          )
        ORDER BY COALESCE(divergence, 0) DESC, started_at DESC
        """,
        (since_ts, COST_DIVERGENCE_BORDERLINE),
    ).fetchall()
    return _rows_to_dicts(rows)


def _zombie_sessions(con: sqlite3.Connection, now_ts: float) -> list[dict[str, Any]]:
    cutoff = now_ts - ZOMBIE_THRESHOLD_SECONDS
    rows = con.execute(
        """
        SELECT id, model,
               datetime(started_at, 'unixepoch') AS started,
               ROUND(? - started_at, 0) AS age_seconds,
               message_count
        FROM sessions
        WHERE ended_at IS NULL
          AND started_at < ?
        ORDER BY started_at ASC
        """,
        (now_ts, cutoff),
    ).fetchall()
    return _rows_to_dicts(rows)


def _ghost_sessions(con: sqlite3.Connection, since_ts: float) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT s.id, s.model, s.message_count,
               datetime(s.started_at, 'unixepoch') AS started
        FROM sessions s
        WHERE s.started_at >= ?
          AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id)
        ORDER BY s.started_at DESC
        """,
        (since_ts,),
    ).fetchall()
    return _rows_to_dicts(rows)


# ---------------- Detectors ----------------

def detector_truncation_output(
    con: sqlite3.Connection, since_ts: float, threshold: int,
) -> DetectorResult:
    """Sessions whose ``output_tokens`` exceed *threshold*."""
    sessions = _top_output_sessions(con, since_ts, limit=25)
    flagged = [s for s in sessions if (s.get("output_tokens") or 0) >= threshold]
    fired = bool(flagged)
    # Borderline = top sessions are within 1.5x of the threshold but below it.
    borderline_band = [s for s in sessions
                       if (s.get("output_tokens") or 0) >= threshold * 0.75
                       and (s.get("output_tokens") or 0) < threshold]
    borderline = fired and bool(borderline_band)
    summary = (
        f"{len(flagged)} session(s) above output-token alert "
        f"({threshold:,})"
        if fired else "all sessions within output-token tolerance"
    )
    return DetectorResult(
        id=TRUNCATION_OUTPUT,
        severity=SEVERITY_ALERT if fired else SEVERITY_INFO,
        summary=summary,
        evidence={
            "alert_threshold": threshold,
            "flagged": flagged,
            "borderline_band": borderline_band,
            "top_sessions": sessions,
        },
        fired=fired,
        borderline=borderline,
    )


def detector_truncation_finish_reason(
    con: sqlite3.Connection, since_ts: float,
) -> DetectorResult:
    """Sessions with any ``messages.finish_reason='length'`` in window."""
    sessions = _finish_reason_length_sessions(con, since_ts)
    fired = bool(sessions)
    return DetectorResult(
        id=TRUNCATION_FINISH_REASON,
        severity=SEVERITY_ALERT if fired else SEVERITY_INFO,
        summary=(
            f"{len(sessions)} session(s) hit server-side length cap"
            if fired else "no messages with finish_reason='length'"
        ),
        evidence={"sessions": sessions},
        fired=fired,
    )


def detector_truncation_log_markers(
    log_dir: Path, since_ts: float, include_curator: bool,
) -> DetectorResult:
    """Length-class markers in any ``*.log`` file."""
    files = discover_log_files(log_dir, include_curator=include_curator)
    b = scan_log_truncations(files, since_ts)
    length_hits = b.get("length_class_hits", 0)
    fired = length_hits > 0
    return DetectorResult(
        id=TRUNCATION_LOG_MARKERS,
        severity=SEVERITY_ALERT if fired else SEVERITY_INFO,
        summary=(
            f"{length_hits} length-class marker(s) across {b.get('files_scanned', 0)} log file(s)"
            if fired else f"no length-class markers across {b.get('files_scanned', 0)} log file(s)"
        ),
        evidence={
            "files_scanned": b.get("files_scanned", 0),
            "files_missing": b.get("files_missing", 0),
            "length_class_hits": length_hits,
            "matches": b.get("matches", []),
            "per_file": b.get("per_file", []),
        },
        fired=fired,
    )


def detector_stream_drops(
    log_dir: Path, since_ts: float, include_curator: bool,
) -> DetectorResult:
    """Mid-tool-call stream drops above the alert/borderline thresholds."""
    files = discover_log_files(log_dir, include_curator=include_curator)
    b = scan_log_truncations(files, since_ts)
    drops = b.get("stream_drop_warnings", 0)
    if drops >= STREAM_DROPS_ALERT:
        sev, fired, borderline = SEVERITY_ALERT, True, False
    elif drops >= STREAM_DROPS_BORDERLINE:
        sev, fired, borderline = SEVERITY_WARN, True, True
    else:
        sev, fired, borderline = SEVERITY_INFO, False, drops > 0
    return DetectorResult(
        id=STREAM_DROPS,
        severity=sev,
        summary=(
            f"{drops} mid-tool-call stream-drop warning(s)"
            if drops else "no stream-drop warnings"
        ),
        evidence={
            "stream_drop_warnings": drops,
            "alert_threshold": STREAM_DROPS_ALERT,
            "borderline_threshold": STREAM_DROPS_BORDERLINE,
            "files_scanned": b.get("files_scanned", 0),
        },
        fired=fired,
        borderline=borderline,
    )


def detector_compression_stale_locks(con: sqlite3.Connection, now_ts: float) -> DetectorResult:
    locks = _compression_locks(con, now_ts)
    fired = bool(locks)
    return DetectorResult(
        id=COMPRESSION_STALE_LOCKS,
        severity=SEVERITY_ALERT if fired else SEVERITY_INFO,
        summary=(
            f"{len(locks)} stale compression lock(s) — likely crashed compressor process(es)"
            if fired else "no stale compression locks"
        ),
        evidence={"locks": locks},
        fired=fired,
    )


def detector_compression_failures(con: sqlite3.Connection, since_ts: float) -> DetectorResult:
    sessions = _compression_failure_sessions(con, since_ts)
    fired = bool(sessions)
    return DetectorResult(
        id=COMPRESSION_FAILURES,
        severity=SEVERITY_ALERT if fired else SEVERITY_INFO,
        summary=(
            f"{len(sessions)} session(s) with compression failure"
            if fired else "no compression failures"
        ),
        evidence={"sessions": sessions},
        fired=fired,
    )


def detector_rewinds(con: sqlite3.Connection, since_ts: float) -> DetectorResult:
    sessions = _rewind_sessions(con, since_ts)
    fired = bool(sessions)
    borderline = any(
        s.get("rewind_count", 0) < REWIND_ALERT for s in sessions
    )
    return DetectorResult(
        id=REWIND,
        severity=SEVERITY_WARN if fired else SEVERITY_INFO,
        summary=(
            f"{len(sessions)} session(s) with rewind_count >= {REWIND_BORDERLINE}"
            if fired else "no sessions with elevated rewind_count"
        ),
        evidence={
            "alert_threshold": REWIND_ALERT,
            "borderline_threshold": REWIND_BORDERLINE,
            "sessions": sessions,
        },
        fired=fired,
        borderline=borderline and fired,
    )


def detector_handoff_errors(con: sqlite3.Connection, since_ts: float) -> DetectorResult:
    sessions = _handoff_error_sessions(con, since_ts)
    fired = bool(sessions)
    return DetectorResult(
        id=HANDOFF_ERRORS,
        severity=SEVERITY_ALERT if fired else SEVERITY_INFO,
        summary=(
            f"{len(sessions)} session(s) with handoff_error"
            if fired else "no handoff errors"
        ),
        evidence={"sessions": sessions},
        fired=fired,
    )


def detector_cost_anomalies(con: sqlite3.Connection, since_ts: float) -> DetectorResult:
    sessions = _cost_anomaly_sessions(con, since_ts)
    if not sessions:
        return DetectorResult(
            id=COST_ANOMALIES,
            severity=SEVERITY_INFO,
            summary="no cost anomalies",
            evidence={"sessions": []},
        )
    # Split between alert (divergence >= 25 % or bad cost_status) and borderline.
    alert = [s for s in sessions
             if (s.get("divergence") or 0) >= COST_DIVERGENCE_ALERT
             or (s.get("cost_status") and s["cost_status"] not in COST_STATUS_OK)]
    borderline = [s for s in sessions if s not in alert]
    fired = bool(alert)
    sev = SEVERITY_ALERT if fired else SEVERITY_WARN
    return DetectorResult(
        id=COST_ANOMALIES,
        severity=sev,
        summary=(
            f"{len(alert)} alert cost anomaly/ies, {len(borderline)} borderline"
            if alert else f"{len(borderline)} borderline cost divergence(s)"
        ),
        evidence={
            "alert_threshold": COST_DIVERGENCE_ALERT,
            "borderline_threshold": COST_DIVERGENCE_BORDERLINE,
            "alert_sessions": alert,
            "borderline_sessions": borderline,
            "all_sessions": sessions,
        },
        fired=fired,
        borderline=bool(borderline),
    )


def detector_zombie_sessions(con: sqlite3.Connection, now_ts: float) -> DetectorResult:
    sessions = _zombie_sessions(con, now_ts)
    fired = bool(sessions)
    return DetectorResult(
        id=ZOMBIE_SESSIONS,
        severity=SEVERITY_ALERT if fired else SEVERITY_INFO,
        summary=(
            f"{len(sessions)} zombie session(s) older than {ZOMBIE_THRESHOLD_SECONDS // 3600}h"
            if fired else "no zombie sessions"
        ),
        evidence={
            "threshold_seconds": ZOMBIE_THRESHOLD_SECONDS,
            "sessions": sessions,
        },
        fired=fired,
    )


def detector_ghost_sessions(con: sqlite3.Connection, since_ts: float) -> DetectorResult:
    sessions = _ghost_sessions(con, since_ts)
    fired = bool(sessions)
    return DetectorResult(
        id=GHOST_SESSIONS,
        severity=SEVERITY_WARN if fired else SEVERITY_INFO,
        summary=(
            f"{len(sessions)} ghost session(s) (started but no messages)"
            if fired else "no ghost sessions"
        ),
        evidence={"sessions": sessions},
        fired=fired,
    )


def detector_skill_index_drift(paths: ResolvedPaths) -> DetectorResult:
    """Drift between the three sources that record installed skills.

    Reads the filesystem walk (what ``hermes skills list`` shows), the
    lock.json (what ``hermes skills search`` shows), and the
    ``skills.disabled`` policy in the profile's ``config.yaml``. Any
    name present in only some of the three is reported. The verdict
    is read-only; the ``talaria skills prune`` tool consumes the same
    :class:`SkillIndex` to do the write.

    Detection classes:

    * **filesystem_only** — on disk but not in lock.json. Show in
      ``skills list``, missing from ``skills search``. Root cause is
      almost always a filesystem install that bypassed
      ``hermes skills install`` (``cp -r``, untar, etc.).
    * **lock_only** — in lock.json but not on disk. The reverse: a
      manual ``rm -rf`` left the lock entry dangling. ``skills list``
      silently omits them; ``skills search`` still returns them.
    * **disabled_orphans** — names in ``skills.disabled`` that exist
      in neither registry. Harmless but stale; a clean policy file
      should not reference nothing.

    None of these break runtime behaviour on their own, but each
    makes the operator's view of "what skills do I have?" disagree
    with what Hermes actually loads — exactly the silent drift that
    bites during install/uninstall workflows.

    The detector does **not** check the ``SKILL.md`` ``name:``
    frontmatter vs the directory basename. That's a different concern
    (skill content drift) and would warrant a sibling detector if it
    becomes a recurring false positive.
    """
    from talaria.hermes.skill_index import index_to_report, read_index

    idx = read_index(paths)
    fired = idx.has_drift
    parts: list[str] = []
    if idx.filesystem_only:
        parts.append(f"{len(idx.filesystem_only)} filesystem-only")
    if idx.lock_only:
        parts.append(f"{len(idx.lock_only)} lock-only")
    if idx.disabled_orphans:
        parts.append(f"{len(idx.disabled_orphans)} disabled-orphans")
    summary = (
        "skill index drift: " + ", ".join(parts)
        if fired else "skill index consistent across filesystem, lock, and disabled policy"
    )
    return DetectorResult(
        id=SKILL_INDEX_DRIFT,
        severity=SEVERITY_ALERT if fired else SEVERITY_INFO,
        summary=summary,
        evidence=index_to_report(idx),
        fired=fired,
    )


# ---------------- Orchestrator ----------------

#: Callable signature of every detector.
DetectorFn = Callable[..., DetectorResult]


def _select_detectors(
    only: tuple[str, ...] = (),
    skip: tuple[str, ...] = (),
) -> list[str]:
    if only:
        unknown = set(only) - set(DETECTOR_IDS)
        if unknown:
            raise ValueError(
                f"unknown detector(s) in --only: {sorted(unknown)}; "
                f"valid: {list(DETECTOR_IDS)}"
            )
        return list(only)
    selected = list(DETECTOR_IDS)
    if skip:
        unknown = set(skip) - set(DETECTOR_IDS)
        if unknown:
            raise ValueError(
                f"unknown detector(s) in --skip: {sorted(unknown)}; "
                f"valid: {list(DETECTOR_IDS)}"
            )
        return [d for d in selected if d not in set(skip)]
    return selected


def run(
    paths: ResolvedPaths,
    *,
    days: int = DEFAULT_LOOKBACK_DAYS,
    since: str | None = None,
    include_curator: bool = False,
    only: tuple[str, ...] = (),
    skip: tuple[str, ...] = (),
    free_flight: bool = True,
    free_flight_log_lines: int = 200,
    free_flight_timeout: int = 180,
    free_flight_runner: Callable[..., Any] | None = None,
    apply_curator_suggestions: bool = False,
    apply_dry_run: bool = False,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Run every selected detector against *paths* and assemble a report.

    The free-flight curator pass is **default-on**. The whole point
    of the doctor command is to find inconsistencies the operator
    didn't anticipate, and the deterministic 12-detector pass only
    covers patterns the rules know to look for. A first-time
    ``talaria hermes doctor`` therefore calls the configured
    ``_curator`` model on the assembled evidence. Operators who want
    pure deterministic results pass ``--no-free-flight`` at the CLI
    (which sets ``free_flight=False`` here). The token budget and
    recent-sessions cap are the safety guardrails that keep the
    pass bounded.

    ``config_suggestion`` findings returned by the free-flight pass
    are reported in the verdict but never applied unless
    ``apply_curator_suggestions=True`` is passed. With
    ``apply_curator_suggestions``, the active profile's ``config.yaml``
    is written via :func:`talaria.sync.writer.write_with_backup`
    (the same atomic backup writer used by
    :mod:`talaria.hermes.auxiliary`). With ``apply_dry_run=True`` the
    proposed change is computed and reported but no bytes are
    written. The two flags are independent:
    ``apply_curator_suggestions=False`` skips the apply path entirely
    (the default report-only mode); ``apply_dry_run=True`` previews
    the apply without writing. Both can be set; the dry-run wins.

    Note on what the apply path actually does: it filters the
    findings list to ``id.startswith("free_flight:config:")`` and
    writes only those to ``config.yaml``. Anomaly findings
    (deterministic detectors + free-flight ``kind=anomaly``) are
    diagnostic; they do not have a tactical action and are
    intentionally NOT applied. The flag name
    ``--apply-curator-suggestions`` reflects this — only curator
    suggestions are applied.

    Parameters:
        paths: the resolved profile (state.db, log_dir).
        days: look-back window in days (default 2).
        since: ISO date override of *days*.
        include_curator: pass through to log-file discovery.
        only: whitelist of detector ids. Empty = run all.
        skip: blacklist of detector ids. Empty = run all.
        free_flight: when True (default), run the open-ended
            curator pass over raw evidence (see
            :mod:`talaria.hermes.doctor_free_flight`). Set False
            for pure deterministic results.
        free_flight_log_lines: per-file line cap when the hermes
            framework inlines the logs folder in the model
            context (default 200). The framework uses
            ``@folder:<path>:N`` to inline the first N lines of
            every file. Operators with smaller contexts can lower
            this; operators who want more in-context can raise it.
        free_flight_timeout: per-call subprocess timeout in
            seconds (default 180). One ``hermes chat -q`` call is
            made per doctor run; raise this if the curator
            model is consistently slow on the operator's network.
        free_flight_runner: override the curator-model subprocess
            runner for tests. ``None`` (default) uses
            :func:`talaria.hermes.doctor_llm.hermes_chat`.
        apply_curator_suggestions: when True, write curator
            ``config_suggestion`` findings to ``config.yaml`` via the
            atomic backup writer. Anomaly findings are NOT applied —
            they are diagnostic and have no tactical action.
        apply_dry_run: when True, preview the apply without writing.
            Forces ``apply_curator_suggestions=True`` to compute the
            preview; the write is still suppressed.
        config_path: explicit path to ``config.yaml`` (overrides
            the resolved-profile path). Useful for tests.
    """
    window = resolve_window(days=days, since=since)
    now_ts = datetime.now(timezone.utc).timestamp()
    selected = _select_detectors(only=only, skip=skip)
    skipped = [d for d in DETECTOR_IDS if d not in selected]
    con = _open_state_db_readonly(paths.state_db)

    per_detector: list[DetectorResult] = []
    detector_errors: dict[str, str] = {}

    for det_id in selected:
        try:
            result = _dispatch(det_id, con, paths, window.since_ts, now_ts, include_curator)
        except Exception as exc:  # one detector must not break the others
            detector_errors[det_id] = f"{type(exc).__name__}: {exc}"
            result = DetectorResult(
                id=det_id, severity=SEVERITY_INFO,
                summary=f"detector error: {exc}",
                evidence={"error": str(exc)},
            )
        per_detector.append(result)

    if con is not None:
        con.close()

    fired = any(r.fired for r in per_detector)
    free_flight_report: dict[str, Any] | None = None
    apply_report: dict[str, Any] | None = None
    if free_flight:
        from talaria.hermes import doctor_free_flight, doctor_llm
        runner = free_flight_runner
        if runner is None:
            runner = doctor_llm.hermes_chat
        try:
            findings = doctor_free_flight.run(
                paths,
                days=days,
                since=since,
                log_lines=free_flight_log_lines,
                include_curator=include_curator,
                subprocess_runner=runner,
                timeout=free_flight_timeout,
            )
        except Exception as exc:
            findings = [DetectorResult(
                id="free_flight:error",
                severity=SEVERITY_INFO,
                summary=f"free-flight orchestrator failed: {type(exc).__name__}: {exc}",
            )]
        per_detector.extend(findings)
        if any(r.fired for r in findings):
            fired = True
        free_flight_report = {
            "findings_count": len(findings),
            "fired_count": sum(1 for r in findings if r.fired),
            "log_lines": free_flight_log_lines,
            "timeout_seconds": free_flight_timeout,
        }
        # Auto-apply config_suggestion findings when the operator
        # opted in. The two flags are independent: apply_curator_suggestions
        # is the gate, apply_dry_run forces the preview path.
        # The apply path is curator-only: it filters findings to
        # id.startswith("free_flight:config:") and ignores anomaly
        # findings (deterministic detectors + free-flight anomalies),
        # because those have no tactical action.
        if apply_curator_suggestions or apply_dry_run:
            config_suggestions = [
                r for r in findings
                if r.id.startswith("free_flight:config:")
            ]
            if config_suggestions:
                apply_report = apply_config_suggestions(
                    paths,
                    config_suggestions,
                    dry_run=apply_dry_run,
                    config_path=config_path,
                )

    return {
        "profile": paths.profile,
        "state_db": str(paths.state_db),
        "log_dir": str(paths.log_dir),
        "window_start_utc": window.cutoff_iso,
        "selected_detectors": list(selected),
        "skipped_detectors": skipped,
        "per_detector": [
            _with_remediation_hint(r.to_dict()) for r in per_detector
        ],
        "detector_errors": detector_errors,
        "free_flight": free_flight_report,
        "apply": apply_report,
        "fired": fired,
    }


def _set_dotted_key(d: dict[str, Any], dotted: str, value: Any) -> tuple[bool, str]:
    """Set ``d[dotted] = value`` where ``dotted`` is a yaml dot-path.

    Returns ``(applied, message)``. ``applied=False`` means the path
    could not be resolved (a parent block is missing); the message
    is the human-readable reason. The function mutates ``d`` in
    place; the caller is responsible for the surrounding
    write/diff/backup.
    """
    if not dotted:
        return False, "empty yaml_path"
    parts = dotted.split(".")
    cur: Any = d
    for p in parts[:-1]:
        if not isinstance(cur, dict):
            return False, f"path traverses non-dict at {p!r}"
        if p not in cur:
            # Auto-create the parent block so the suggestion can land
            # even when the operator never wrote the key. This is
            # safer than rejecting the suggestion outright — the
            # operator asked for a config change, and the change
            # semantically wants the parent to exist.
            cur[p] = {}
        cur = cur[p]
    leaf = parts[-1]
    if not isinstance(cur, dict):
        return False, f"leaf parent is not a dict at {leaf!r}"
    cur[leaf] = value
    return True, "applied"


def _coerce_suggested_value(raw: str) -> Any:
    """Coerce the model's string-suggested value to a sensible Python type.

    Booleans first (since ``bool`` is a subclass of ``int`` in Python
    and ``int("true")`` would raise), then integers, then floats,
    then a plain string. The model's string format is opaque — the
    YAML serialiser will quote it back if it can't be coerced, so
    string fallback is always safe.
    """
    s = raw.strip()
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("yes", "no", "on", "off"):
        return low in ("yes", "on")
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return raw


def _format_diff(before: str, after: str) -> str:
    """Return a unified diff string between *before* and *after*.

    Used by ``apply_config_suggestions(dry_run=True)`` so the
    operator can see exactly which lines would change before
    committing. Falls back to a coarse line-diff when ``difflib`` is
    not available (it always is on Python 3, but the guard keeps
    the helper import-safe for tests that stub the yaml_io layer).
    """
    import difflib
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines, after_lines,
        fromfile="config.yaml (current)",
        tofile="config.yaml (proposed)",
        n=2,
    )
    return "".join(diff)


def apply_config_suggestions(
    paths: ResolvedPaths,
    suggestions: list[DetectorResult],
    *,
    dry_run: bool = False,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Apply *suggestions* to the active profile's ``config.yaml``.

    The function uses:

    * :func:`talaria.sync.yaml_io.load_yaml` to read the current
      config.
    * A dotted-key walker (this module) to set each
      ``yaml_path = suggested_value``.
    * :func:`talaria.sync.yaml_io.dump_yaml` to serialise the
      result.
    * :func:`talaria.sync.writer.write_with_backup` to atomically
      replace the file with a ``.bak`` sidecar.

    ``dry_run=True`` computes the proposed change and a unified diff
    but never writes bytes. The diff is reported in the
    ``dry_run_diff`` field so the operator can ``cat`` it from the
    JSON.

    Failures are reported, not raised:

    * A bad ``yaml_path`` (empty, non-existent parent that cannot be
      auto-created) is captured as ``{applied: False, reason: ...}``
      in the per-suggestion result. The successful suggestions are
      still applied.
    * A read/write failure on the config file aborts the whole
      apply and is reported as ``ok=False, error=...``.

    Returns a report dict with:

    * ``ok`` (bool) — the apply (or dry-run) ran to completion.
    * ``dry_run`` (bool) — was this a preview?
    * ``applied`` (list of {yaml_path, current_value, new_value,
      message}) — suggestions that landed.
    * ``skipped`` (list of {yaml_path, reason}) — suggestions that
      could not be applied.
    * ``config_path`` (str) — the config file targeted.
    * ``backup`` (str|None) — path to the ``.bak`` sidecar written
      (only when ``dry_run=False``).
    * ``dry_run_diff`` (str) — unified diff of the proposed change
      (only when ``dry_run=True``).
    """
    from talaria.sync.writer import write_with_backup
    from talaria.sync.yaml_io import dump_yaml, load_yaml, validate_yaml

    # Resolve the target config file. Explicit `config_path` wins
    # (test path); otherwise the active profile's config.yaml.
    if config_path is not None:
        target = Path(config_path)
    else:
        root = paths.hermes_root
        if paths.profile == "default":
            target = root / "config.yaml"
        else:
            target = root / "profiles" / paths.profile / "config.yaml"

    report: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "config_path": str(target),
        "applied": [],
        "skipped": [],
        "backup": None,
        "dry_run_diff": None,
    }

    if not suggestions:
        return report

    # Read the current config. A missing file is fine — start with an
    # empty dict and create the file on first apply.
    current_text = ""
    current: dict[str, Any] = {}
    if target.exists():
        try:
            current_text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            report["ok"] = False
            report["error"] = f"read failed: {exc}"
            return report
        try:
            loaded = load_yaml(target)
        except Exception as exc:
            report["ok"] = False
            report["error"] = f"yaml parse failed: {exc}"
            return report
        if isinstance(loaded, dict):
            current = loaded

    # Apply each suggestion in turn. Failures on individual
    # suggestions are captured; successful suggestions still land.
    for sug in suggestions:
        ev = sug.evidence or {}
        yaml_path = (ev.get("yaml_path") or "").strip()
        suggested_raw = ev.get("suggested_value", "")
        current_value = ev.get("current_value", "unknown")
        if not yaml_path:
            report["skipped"].append({
                "yaml_path": yaml_path,
                "reason": "empty yaml_path",
            })
            continue
        # Coerce the value. Type errors fall back to string; the
        # YAML serialiser will quote it correctly either way.
        try:
            new_value = _coerce_suggested_value(str(suggested_raw))
        except Exception as exc:
            report["skipped"].append({
                "yaml_path": yaml_path,
                "reason": f"coerce failed: {type(exc).__name__}: {exc}",
            })
            continue
        applied, message = _set_dotted_key(current, yaml_path, new_value)
        if applied:
            report["applied"].append({
                "yaml_path": yaml_path,
                "current_value": current_value,
                "new_value": str(new_value),
                "message": message,
            })
        else:
            report["skipped"].append({
                "yaml_path": yaml_path,
                "reason": message,
            })

    if not report["applied"]:
        # Nothing to do; don't serialise an unchanged config.
        return report

    # Serialise the proposed config and validate it round-trips.
    try:
        proposed_text = dump_yaml(current)
    except Exception as exc:
        report["ok"] = False
        report["error"] = f"yaml dump failed: {exc}"
        return report
    valid, parse_error = validate_yaml(proposed_text)
    if not valid:
        report["ok"] = False
        report["error"] = f"proposed config does not round-trip: {parse_error}"
        return report

    if dry_run:
        report["dry_run_diff"] = _format_diff(current_text, proposed_text)
        return report

    # Apply: atomic write with .bak sidecar.
    try:
        outcome = write_with_backup(target, proposed_text)
    except OSError as exc:
        report["ok"] = False
        report["error"] = f"write failed: {exc}"
        return report
    report["backup"] = str(outcome.backup) if outcome.backup else None
    report["written"] = str(outcome.written)
    report["bytes_written"] = outcome.bytes_written
    report["proposed_diff"] = _format_diff(current_text, proposed_text)
    return report


# ---------------- Tactical actions (deterministic remediation) ----------------
#
# Most doctor findings are diagnostic — they have no local fix and the
# operator decides what to do. Three detectors have actionable remediation
# where a destructive write is genuinely the right thing:
#
#   * ``compression_stale_locks``  — drop expired rows in ``compression_locks``
#     that are blocking the next compressor run.
#   * ``zombie_sessions``           — close sessions whose writer crashed
#     without setting ``ended_at``. They inflate session counts and any
#     "currently running" reporting.
#   * ``ghost_sessions``            — delete sessions that were created but
#     never received a single message row. They are dead weight that
#     pollutes cost and message-count aggregates.
#
# The other nine findings stay diagnostic. Truncation, stream drops,
# compression failures, rewinds, handoff errors, cost anomalies, and
# skill_index_drift each need human context to fix correctly — auto-applying
# any of them would create the exact "baroque defensive machinery" we
# avoid.
#
# Flag model: each tactical flag defaults to dry-run preview. ``--apply``
# is the gate that turns preview into a write. The defaults match
# ``talaria skills prune`` — preview by default, explicit apply required.

#: Identifier for the stale-lock tactical action (used in CLI + report).
TACTICAL_PRUNE_STALE_LOCKS = "prune_stale_locks"

#: Identifier for the zombie-closure tactical action.
TACTICAL_CLOSE_ZOMBIES = "close_zombies"

#: Identifier for the ghost-session tactical action.
TACTICAL_PRUNE_GHOST_SESSIONS = "prune_ghost_sessions"

#: Canonical ordered tuple of tactical-action identifiers. Each maps to
#: one detector id and one per-action helper. The orchestrator walks
#: this tuple in order so the report keys have a stable shape.
TACTICAL_ACTION_IDS: tuple[str, ...] = (
    TACTICAL_PRUNE_STALE_LOCKS,
    TACTICAL_CLOSE_ZOMBIES,
    TACTICAL_PRUNE_GHOST_SESSIONS,
)


#: Mapping from detector id → the CLI invocation the operator can
#: paste to fix that detector's findings. The renderer prints the
#: hint after the detector's "first flagged …" line so the operator
#: sees both the evidence and the remediation in one glance. The
#: same hint lands in the JSON report's
#: ``per_detector[i].remediation`` field for machines.
#:
#: Hint text is the exact argv shape — no markdown, no surrounding
#: prose, no leading "fix:" prefix (the renderer adds that). When the
#: detector has no remediation, omit the id from this map; the
#: renderer will not emit a hint line.
#:
#: Two flavours of hint exist:
#:
#: * Doctor tactical flags — ``--prune-stale-locks [--apply]`` and
#:   friends. Same command, opt-in write.
#: * Sibling-command remediations — e.g. ``talaria skills prune …``.
#:   The remediation lives in a different command (different
#:   process, different config backup contract, different safety
#:   model); the renderer cannot preview them inline, so the
#:   operator runs the sibling in dry-run first. The hint text
#:   always starts with the command name so the operator cannot
#:   mistake it for a doctor flag.
#:
#: Keep in sync with the tactical-action layer. Adding a new
#: detector+action pair is one line here plus one entry in
#: ``TACTICAL_ACTION_IDS``.
_DETECTOR_REMEDIATION_HINTS: dict[str, str] = {
    COMPRESSION_STALE_LOCKS:
        "--prune-stale-locks [--apply]",
    ZOMBIE_SESSIONS:
        "--close-zombies [--apply]",
    GHOST_SESSIONS:
        "--prune-ghost-sessions [--apply]",
    SKILL_INDEX_DRIFT:
        "talaria skills prune --prune-filesystem-only "
        "--prune-lock-only --prune-disabled-orphans --apply",
}


def _with_remediation_hint(detector_dict: dict[str, Any]) -> dict[str, Any]:
    """Attach a CLI remediation hint to a detector's serialised form.

    The orchestrator emits one ``per_detector`` entry per detector;
    this helper looks the detector's id up in
    :data:`_DETECTOR_REMEDIATION_HINTS` and attaches the matching
    flag shape as a ``remediation`` key. The renderer reads it to
    print ``fix: <hint>`` under fired findings; JSON consumers see
    it as a top-level field on the per-detector dict.

    Free-flight findings (``free_flight:anomaly:<slug>``,
    ``free_flight:config:<slug>``) are deliberately NOT enriched —
    config_suggestions are not actionable through tactical flags,
    and free-flight anomalies are open-ended by design (the model
    decides the remediation). Adding ``remediation`` to those would
    create the misleading impression that the deterministic
    tactical layer applies to them.

    The input dict is shallow-copied to keep this function pure; the
    orchestrator never re-reads the original ``DetectorResult``.
    """
    out = dict(detector_dict)
    out["remediation"] = _DETECTOR_REMEDIATION_HINTS.get(detector_dict["id"])
    return out


def _open_state_db_readwrite(path: Path) -> sqlite3.Connection | None:
    """Open *path* read-write. Returns ``None`` if it does not exist.

    Distinct from :func:`_open_state_db_readonly` which uses ``mode=ro``
    URI; that connection cannot be used for writes. Tactical actions
    need RW access. A read-only mount or insufficient permission will
    raise ``sqlite3.OperationalError``; we let the caller report it
    rather than silently degrading to a no-op.
    """
    if not path.exists():
        return None
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _tactical_action_report(
    *,
    dry_run: bool,
    would_modify: list[dict[str, Any]],
    applied: list[dict[str, Any]],
    skipped: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build the standard per-action report dict.

    Matches the shape of :func:`apply_config_suggestions` so JSON
    consumers can branch on ``ok`` / ``dry_run`` / ``applied`` /
    ``skipped`` uniformly across all doctor apply paths.
    """
    return {
        "ok": error is None,
        "dry_run": dry_run,
        "would_modify": would_modify,
        "applied": applied,
        "skipped": skipped or [],
        "error": error,
    }


def _prune_stale_locks_action(
    paths: ResolvedPaths,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Tactical action for ``compression_stale_locks``.

    Deletes every row in ``compression_locks`` whose ``expires_at`` is
    in the past. The preview identifies each row by ``(session_id,
    holder, expires_at)`` so the operator can audit what would be
    removed. Identifies which session ids will be affected.
    """
    if not paths.state_db.exists():
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=[],
            applied=[],
            error=f"state.db not found: {paths.state_db}",
        )
    try:
        con = _open_state_db_readwrite(paths.state_db)
    except sqlite3.OperationalError as exc:
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=[],
            applied=[],
            error=f"state.db not writable: {exc}",
        )
    if con is None:
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=[],
            applied=[],
            error=f"state.db not found: {paths.state_db}",
        )
    try:
        now_ts = datetime.now(timezone.utc).timestamp()
        rows = con.execute(
            """
            SELECT session_id, holder, acquired_at, expires_at,
                   (expires_at - ?) AS seconds_remaining
            FROM compression_locks
            WHERE expires_at < ?
            ORDER BY expires_at ASC
            """,
            (now_ts, now_ts),
        ).fetchall()
        would_modify = _rows_to_dicts(rows)
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        if not dry_run and would_modify:
            for r in would_modify:
                # ``session_id`` is the PRIMARY KEY of
                # ``compression_locks`` (one lock per session), so the
                # delete is keyed on session_id alone. holder /
                # expires_at are kept in the report for audit; we
                # still re-assert expires_at < now_ts at delete time
                # so a row whose expires_at got extended between scan
                # and write is preserved.
                cur = con.execute(
                    """
                    DELETE FROM compression_locks
                    WHERE session_id = ?
                      AND expires_at < ?
                    """,
                    (r["session_id"], now_ts),
                )
                if cur.rowcount > 0:
                    applied.append({
                        "session_id": r["session_id"],
                        "holder": r["holder"],
                        "expires_at": r["expires_at"],
                    })
                else:
                    skipped.append({
                        "session_id": r["session_id"],
                        "reason": "row gone or expires_at was extended (concurrent writer?)",
                    })
            con.commit()
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=would_modify,
            applied=applied,
            skipped=skipped,
        )
    except sqlite3.Error as exc:
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=[],
            applied=[],
            error=f"sqlite error: {exc}",
        )
    finally:
        con.close()


def _close_zombies_action(
    paths: ResolvedPaths,
    *,
    dry_run: bool,
    threshold_seconds: int = ZOMBIE_THRESHOLD_SECONDS,
) -> dict[str, Any]:
    """Tactical action for ``zombie_sessions``.

    Sets ``ended_at = now`` on every session whose ``started_at`` is
    older than ``threshold_seconds`` and whose ``ended_at IS NULL``.
    The session row itself stays — closing it preserves the audit
    trail of what crashed. Only ``ended_at`` is written.

    The preview lists each session id + age so the operator can
    audit which ones would be closed.
    """
    if not paths.state_db.exists():
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=[],
            applied=[],
            error=f"state.db not found: {paths.state_db}",
        )
    try:
        con = _open_state_db_readwrite(paths.state_db)
    except sqlite3.OperationalError as exc:
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=[],
            applied=[],
            error=f"state.db not writable: {exc}",
        )
    if con is None:
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=[],
            applied=[],
            error=f"state.db not found: {paths.state_db}",
        )
    try:
        now_ts = datetime.now(timezone.utc).timestamp()
        cutoff = now_ts - threshold_seconds
        rows = con.execute(
            """
            SELECT id, model,
                   datetime(started_at, 'unixepoch') AS started,
                   ROUND(? - started_at, 0) AS age_seconds,
                   message_count
            FROM sessions
            WHERE ended_at IS NULL
              AND started_at < ?
            ORDER BY started_at ASC
            """,
            (now_ts, cutoff),
        ).fetchall()
        would_modify = _rows_to_dicts(rows)
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        if not dry_run and would_modify:
            for r in would_modify:
                cur = con.execute(
                    "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                    (now_ts, r["id"]),
                )
                if cur.rowcount > 0:
                    applied.append({"id": r["id"], "ended_at": now_ts})
                else:
                    skipped.append({
                        "id": r["id"],
                        "reason": "row already closed (concurrent writer?)",
                    })
            con.commit()
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=would_modify,
            applied=applied,
            skipped=skipped,
        )
    except sqlite3.Error as exc:
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=[],
            applied=[],
            error=f"sqlite error: {exc}",
        )
    finally:
        con.close()


def _prune_ghost_sessions_action(
    paths: ResolvedPaths,
    *,
    dry_run: bool,
    since_ts: float,
) -> dict[str, Any]:
    """Tactical action for ``ghost_sessions``.

    Deletes every session whose ``started_at`` is at or after the
    window cutoff and which has zero rows in ``messages``. These are
    aborted creates — a session row was inserted, the writer crashed
    before any message arrived. There are no cost rows or message
    rows to preserve; the session itself is the dead weight.

    The window cutoff matches the detector's window so we never
    delete ghosts older than the scan scope. (Out-of-window ghosts
    are still diagnostic-only; deleting them silently would be
    surprising.)
    """
    if not paths.state_db.exists():
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=[],
            applied=[],
            error=f"state.db not found: {paths.state_db}",
        )
    try:
        con = _open_state_db_readwrite(paths.state_db)
    except sqlite3.OperationalError as exc:
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=[],
            applied=[],
            error=f"state.db not writable: {exc}",
        )
    if con is None:
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=[],
            applied=[],
            error=f"state.db not found: {paths.state_db}",
        )
    try:
        rows = con.execute(
            """
            SELECT s.id, s.model, s.message_count,
                   datetime(s.started_at, 'unixepoch') AS started
            FROM sessions s
            WHERE s.started_at >= ?
              AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id)
            ORDER BY s.started_at DESC
            """,
            (since_ts,),
        ).fetchall()
        would_modify = _rows_to_dicts(rows)
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        if not dry_run and would_modify:
            for r in would_modify:
                cur = con.execute(
                    "DELETE FROM sessions WHERE id = ? "
                    "AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.session_id = ?)",
                    (r["id"], r["id"]),
                )
                if cur.rowcount > 0:
                    applied.append({"id": r["id"]})
                else:
                    skipped.append({
                        "id": r["id"],
                        "reason": "row already gone or has messages now (concurrent writer?)",
                    })
            con.commit()
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=would_modify,
            applied=applied,
            skipped=skipped,
        )
    except sqlite3.Error as exc:
        return _tactical_action_report(
            dry_run=dry_run,
            would_modify=[],
            applied=[],
            error=f"sqlite error: {exc}",
        )
    finally:
        con.close()


def apply_tactical_actions(
    paths: ResolvedPaths,
    *,
    prune_stale_locks: bool = False,
    close_zombies: bool = False,
    prune_ghost_sessions: bool = False,
    apply: bool = False,
    days: int = DEFAULT_LOOKBACK_DAYS,
    since: str | None = None,
    state_db_override: Path | None = None,
) -> dict[str, Any]:
    """Apply tactical remediations to the profile's ``state.db``.

    Each tactical flag defaults to **dry-run preview** — the report's
    ``would_modify`` lists every row that would be changed, and no
    bytes are written. Setting ``apply=True`` flips every selected
    action to its write path; the per-action ``dry_run`` field
    records which side actually ran.

    Flags are independent. ``prune_stale_locks=True,
    close_zombies=True`` runs both previews in one call; the
    resulting ``report`` keys are stable regardless of which flags
    are set (selected actions get a full report; unselected actions
    get ``{"selected": False}``).

    Backups: tactical actions write to ``state.db`` directly via
    SQLite's WAL. They do **not** create a ``state.db.bak`` —
    partial-file backup of a live SQLite DB is unsafe (the backup
    may capture a mid-transaction state). The operator's existing
    ``state.db`` backup regime is the contract for recovery.

    Returned dict shape::

        {
          "prune_stale_locks": {...},
          "close_zombies": {...},
          "prune_ghost_sessions": {...},
        }

    Each per-action value has: ``ok`` (bool), ``dry_run`` (bool),
    ``would_modify`` (list of dicts that would be touched),
    ``applied`` (list of dicts actually written), ``skipped`` (list
    of ``{... reason}`` rows that vanished between scan and write),
    ``error`` (str or ``None``). Unselected actions have a single
    ``{"selected": False}`` placeholder so consumers can iterate
    ``TACTICAL_ACTION_IDS`` without conditional key access.

    Parameters mirror the orchestrator's window knobs so the same
    scan window applies to detection and action. ``state_db_override``
    is the test seam — same purpose as
    ``apply_config_suggestions(config_path=...)``.
    """
    window = resolve_window(days=days, since=since)
    report: dict[str, Any] = {}
    # Resolve the target state.db. The caller-provided override
    # wins (test path); otherwise the resolved-profile path.
    if state_db_override is not None:
        paths = ResolvedPaths(
            profile=paths.profile,
            hermes_root=paths.hermes_root,
            state_db=state_db_override,
            log_dir=paths.log_dir,
        )

    if prune_stale_locks:
        report[TACTICAL_PRUNE_STALE_LOCKS] = _prune_stale_locks_action(
            paths, dry_run=not apply,
        )
    else:
        report[TACTICAL_PRUNE_STALE_LOCKS] = {"selected": False}

    if close_zombies:
        report[TACTICAL_CLOSE_ZOMBIES] = _close_zombies_action(
            paths, dry_run=not apply,
        )
    else:
        report[TACTICAL_CLOSE_ZOMBIES] = {"selected": False}

    if prune_ghost_sessions:
        report[TACTICAL_PRUNE_GHOST_SESSIONS] = _prune_ghost_sessions_action(
            paths, dry_run=not apply, since_ts=window.since_ts,
        )
    else:
        report[TACTICAL_PRUNE_GHOST_SESSIONS] = {"selected": False}

    return report


def _dispatch(
    det_id: str,
    con: sqlite3.Connection | None,
    paths: ResolvedPaths,
    since_ts: float,
    now_ts: float,
    include_curator: bool,
) -> DetectorResult:
    """Route a detector id to the matching function with the right args."""
    if det_id in (TRUNCATION_LOG_MARKERS, STREAM_DROPS):
        if det_id == TRUNCATION_LOG_MARKERS:
            return detector_truncation_log_markers(paths.log_dir, since_ts, include_curator)
        return detector_stream_drops(paths.log_dir, since_ts, include_curator)
    if det_id == SKILL_INDEX_DRIFT:
        return detector_skill_index_drift(paths)
    if con is None:
        return DetectorResult(
            id=det_id, severity=SEVERITY_INFO,
            summary="state.db not found — detector skipped",
            evidence={"error": f"state.db not found: {paths.state_db}"},
        )
    if det_id == TRUNCATION_OUTPUT:
        return detector_truncation_output(con, since_ts, OUTPUT_TOKEN_ALERT)
    if det_id == TRUNCATION_FINISH_REASON:
        return detector_truncation_finish_reason(con, since_ts)
    if det_id == COMPRESSION_STALE_LOCKS:
        return detector_compression_stale_locks(con, now_ts)
    if det_id == COMPRESSION_FAILURES:
        return detector_compression_failures(con, since_ts)
    if det_id == REWIND:
        return detector_rewinds(con, since_ts)
    if det_id == HANDOFF_ERRORS:
        return detector_handoff_errors(con, since_ts)
    if det_id == COST_ANOMALIES:
        return detector_cost_anomalies(con, since_ts)
    if det_id == ZOMBIE_SESSIONS:
        return detector_zombie_sessions(con, now_ts)
    if det_id == GHOST_SESSIONS:
        return detector_ghost_sessions(con, since_ts)
    raise ValueError(f"unhandled detector id: {det_id}")


# ---------------- Renderer ----------------

def render_human(report: dict[str, Any]) -> tuple[int, str]:
    """Format *report* for the terminal. Returns ``(exit_code, text)``."""
    lines: list[str] = []
    fired = bool(report.get("fired"))

    lines.append("Profile doctor")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Profile:  {report['profile']}")
    lines.append(f"state.db: {report['state_db']}")
    lines.append(f"log_dir:  {report['log_dir']}")
    lines.append(f"window:   {report['window_start_utc']} (UTC) -> now")
    lines.append(f"selected: {', '.join(report['selected_detectors'])}")
    skipped = report.get("skipped_detectors") or []
    if skipped:
        lines.append(f"skipped:  {', '.join(skipped)}")
    lines.append("")

    by_id: dict[str, dict[str, Any]] = {
        d["id"]: d for d in report.get("per_detector", [])
    }
    free_flight_anomalies = [d for d in report.get("per_detector", [])
                             if d["id"].startswith("free_flight:anomaly:")
                             or d["id"] in (
                                 "free_flight:skipped", "free_flight:no_data",
                                 "free_flight:unavailable", "free_flight:error",
                                 "free_flight:unparseable",
                             )]
    free_flight_suggestions = [d for d in report.get("per_detector", [])
                               if d["id"].startswith("free_flight:config:")]

    for det_id in report.get("selected_detectors", DETECTOR_IDS):
        d = by_id.get(det_id)
        if d is None:
            continue
        sev = d["severity"]
        marker = {"alert": "⚠", "warn": "!", "info": " "}.get(sev, " ")
        line = f"{marker} [{sev.upper():5s}] {det_id:32s}  {d['summary']}"
        lines.append(line)
        if d.get("adjudicated"):
            mv = d.get("model_verdict") or {}
            lines.append(f"           adjudicated by curator model: {mv.get('verdict', '?')}")
        if d.get("fired") and d.get("evidence"):
            ev = d["evidence"]
            if "sessions" in ev and ev["sessions"]:
                lines.append(f"           first flagged session: {ev['sessions'][0].get('id', '?')}")
            if "locks" in ev and ev["locks"]:
                lines.append(f"           first stale lock: {ev['locks'][0].get('session_id', '?')}")
        # Remediation hint: when the detector has a tactical action,
        # print the exact argv shape so the operator knows what to
        # paste. Only fired findings get a hint — telling the
        # operator how to "fix" something that didn't fire is noise.
        # Free-flight findings have ``remediation == None`` and are
        # skipped silently.
        if d.get("fired") and d.get("remediation"):
            lines.append(f"           fix: {d['remediation']}")

    if free_flight_anomalies:
        lines.append("")
        lines.append("Free-flight anomalies (curator model, open-ended pass):")
        for f in free_flight_anomalies:
            sev = f["severity"]
            marker = {"alert": "⚠", "warn": "!", "info": " "}.get(sev, " ")
            quote = (f.get("evidence") or {}).get("evidence_quote", "")
            line = f"  {marker} [{sev.upper():5s}] {f['id']}  {f['summary']}"
            lines.append(line)
            if quote:
                lines.append(f"           evidence: {quote[:200]}")

    if free_flight_suggestions:
        lines.append("")
        lines.append("Free-flight config suggestions (curator model, opt-in):")
        for f in free_flight_suggestions:
            sev = f["severity"]
            marker = {"alert": "⚠", "warn": "!", "info": " "}.get(sev, " ")
            ev = f.get("evidence") or {}
            line = f"  {marker} [{sev.upper():5s}] {f['id']}  {f['summary']}"
            lines.append(line)
            if ev.get("yaml_path"):
                lines.append(
                    f"           yaml_path: {ev['yaml_path']}   "
                    f"current={ev.get('current_value', '?')}   "
                    f"suggested={ev.get('suggested_value', '?')}"
                )

    if report.get("detector_errors"):
        lines.append("")
        lines.append("Detector errors:")
        for det_id, err in report["detector_errors"].items():
            lines.append(f"  {det_id}: {err}")

    tactical = report.get("tactical_actions")
    if tactical:
        lines.append("")
        lines.append("Tactical actions:")
        for action_id in TACTICAL_ACTION_IDS:
            entry = tactical.get(action_id)
            if entry is None or entry.get("selected") is False:
                continue
            ok = entry.get("ok", False)
            dry = entry.get("dry_run", True)
            err = entry.get("error")
            verb = "would" if dry else "did"
            if err:
                lines.append(
                    f"  ✗ [{action_id:28s}]  error: {err}"
                )
                continue
            would = entry.get("would_modify", [])
            applied = entry.get("applied", [])
            skipped = entry.get("skipped", [])
            count = len(would) if dry else len(applied)
            tail = (
                f"  ok — {verb} act on {count} row(s)"
                if ok else f"  ! failed — {err}"
            )
            lines.append(f"  ✓ [{action_id:28s}]{tail}")
            for row in (would if dry else applied)[:10]:
                lines.append(
                    f"           {json.dumps(row, default=str, sort_keys=True)}"
                )
            if skipped:
                lines.append(
                    f"           skipped: {len(skipped)} row(s) — concurrent writer?"
                )

    lines.append("")
    lines.append("=" * 60)
    if fired:
        lines.append("VERDICT: at least one detector fired — review above.")
        return 1, "\n".join(lines)
    lines.append("VERDICT: clean — all detectors within tolerance.")
    return 0, "\n".join(lines)


#: Operator-facing catalog of every detector. One source of truth
#: for the ``Detector catalog`` table in ``hermes/AGENTS.md`` and
#: the ``detector_catalog`` field of ``show_resolution`` JSON.
#: Keep the table and the AGENTS.md in sync when changing this list.
DETECTOR_CATALOG: tuple[dict[str, str], ...] = (
    {
        "id": TRUNCATION_OUTPUT,
        "what": "sessions with output_tokens above the alert threshold",
        "threshold": "64 000 (OUTPUT_TOKEN_ALERT)",
        "severity": "alert",
        "source": "sessions table (SQL)",
    },
    {
        "id": TRUNCATION_FINISH_REASON,
        "what": "messages with finish_reason='length' in the window",
        "threshold": ">= 1 hit",
        "severity": "alert",
        "source": "messages table (SQL)",
    },
    {
        "id": TRUNCATION_LOG_MARKERS,
        "what": "WARNING/ERROR/CRITICAL lines matching a length-class pattern in any *.log file",
        "threshold": ">= 1 hit",
        "severity": "alert",
        "source": "log files (uses discover_log_files)",
    },
    {
        "id": STREAM_DROPS,
        "what": "mid-tool-call stream-drop warnings above the alert / borderline rate",
        "threshold": f"alert: {STREAM_DROPS_ALERT} / borderline: {STREAM_DROPS_BORDERLINE} per window",
        "severity": "warn / alert",
        "source": "log files",
    },
    {
        "id": COMPRESSION_STALE_LOCKS,
        "what": "compression_locks rows whose expires_at is in the past",
        "threshold": ">= 1 expired lock",
        "severity": "alert",
        "source": "compression_locks table (SQL)",
    },
    {
        "id": COMPRESSION_FAILURES,
        "what": "sessions with compression_failure_error IS NOT NULL in the window",
        "threshold": ">= 1 session",
        "severity": "alert",
        "source": "sessions table (SQL)",
    },
    {
        "id": REWIND,
        "what": "sessions with rewind_count above the alert threshold",
        "threshold": f"alert: {REWIND_ALERT} (counts >= {REWIND_BORDERLINE} are reported)",
        "severity": "warn",
        "source": "sessions table (SQL)",
    },
    {
        "id": HANDOFF_ERRORS,
        "what": "sessions with handoff_error IS NOT NULL in the window",
        "threshold": ">= 1 session",
        "severity": "alert",
        "source": "sessions table (SQL)",
    },
    {
        "id": COST_ANOMALIES,
        "what": "sessions with cost_status outside the allowed set, or est/actual divergence",
        "threshold": f"alert: divergence >= {COST_DIVERGENCE_ALERT:.0%} or bad status; borderline: >= {COST_DIVERGENCE_BORDERLINE:.0%}",
        "severity": "warn / alert",
        "source": "sessions table (SQL)",
    },
    {
        "id": ZOMBIE_SESSIONS,
        "what": "sessions with ended_at IS NULL and started_at older than the threshold",
        "threshold": f"{ZOMBIE_THRESHOLD_SECONDS // 3600} h (ZOMBIE_THRESHOLD_SECONDS)",
        "severity": "alert",
        "source": "sessions table (SQL)",
    },
    {
        "id": GHOST_SESSIONS,
        "what": "sessions with no messages rows in the window",
        "threshold": ">= 1 session",
        "severity": "warn",
        "source": "sessions + messages join (SQL)",
    },
)


# ---------------- show_resolution ----------------

def show_resolution(paths: ResolvedPaths) -> str:
    """Pretty-print the paths + detector inventory for debugging.

    The output includes a ``detector_catalog`` block — the same
    catalog as the operator-facing table in ``hermes/AGENTS.md`` —
    so the machine-readable form and the human-readable form stay
    in sync from one source of truth (``DETECTOR_CATALOG``).
    """
    return json.dumps({
        "profile": paths.profile,
        "state_db": str(paths.state_db),
        "log_dir": str(paths.log_dir),
        "detector_ids": list(DETECTOR_IDS),
        "confident_detectors": sorted(CONFIDENT_DETECTORS),
        "detector_catalog": [dict(row) for row in DETECTOR_CATALOG],
        "thresholds": {
            "output_token_alert": OUTPUT_TOKEN_ALERT,
            "rewind_alert": REWIND_ALERT,
            "rewind_borderline": REWIND_BORDERLINE,
            "cost_divergence_alert": COST_DIVERGENCE_ALERT,
            "cost_divergence_borderline": COST_DIVERGENCE_BORDERLINE,
            "zombie_threshold_seconds": ZOMBIE_THRESHOLD_SECONDS,
            "stream_drops_alert": STREAM_DROPS_ALERT,
            "stream_drops_borderline": STREAM_DROPS_BORDERLINE,
        },
        "free_flight_defaults": {
            "enabled_by_default": True,
            "log_lines": 200,
            "timeout_seconds": 180,
        },
    }, indent=2)
