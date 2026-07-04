"""Stop a running Hermes dashboard/serve backend by its listening port.

Why this exists
---------------
``hermes serve --stop`` and ``hermes dashboard --stop`` locate the backend
process by pattern-matching the process command line for contiguous
substrings like ``"hermes_cli.main dashboard"``.  When the process was
launched with a global flag between the module and the subcommand — which
is exactly what the Hermes Desktop app does
(``python3 -m hermes_cli.main -p default dashboard --port 9119 …``) — none
of those substrings are contiguous, the match fails, and ``--stop`` reports
"No hermes dashboard processes running" even though the backend is alive
and bound to the port.

This feature detects the backend the robust way: by the TCP port it is
listening on.  A listening socket identifies its owning process
independently of how that process was spelled on the command line, so
``psutil.net_connections`` → PID finds the process that
``hermes serve --stop`` cannot.

psutil abstracts the platform-specific discovery substrate (``/proc/net``
on Linux, libproc on macOS, the NT process/socket APIs on Windows) behind
one call, so this feature is cross-platform without per-OS branches.

Exit semantics (project-wide contract):

* ``0`` — stopped at least one backend, or none was running.
* ``2`` — tool error: a PID that could not be signalled, or a port with no
  listener that the caller asserted was running.
"""

from __future__ import annotations

import os
import signal
import time
from typing import Any

import psutil

from talaria.paths import ResolvedPaths

#: Default port the Hermes dashboard / ``hermes serve`` backend binds.
DEFAULT_SERVE_PORT = 9119

#: How long to wait for a SIGTERM'd backend to exit before SIGKILL.
GRACEFUL_TIMEOUT_SECONDS = 5.0

#: Poll interval while waiting for graceful exit.
POLL_INTERVAL_SECONDS = 0.1


def find_serve_pids(port: int = DEFAULT_SERVE_PORT) -> list[int]:
    """Return PIDs of processes listening on *port*.

    Uses ``psutil.net_connections("inet")`` to enumerate every listening
    TCP socket, filters by local port, and resolves each socket's owning
    PID.  The current process is excluded so a stop command never targets
    itself.  Returns an empty list when nothing is listening, psutil is
    unavailable, or the process table cannot be enumerated.
    """
    self_pid = os.getpid()
    pids: list[int] = []
    try:
        conns = psutil.net_connections("inet")
    except (psutil.Error, OSError, PermissionError):
        return pids
    for conn in conns:
        if conn.status != psutil.CONN_LISTEN:
            continue
        laddr = conn.laddr
        if laddr is None:
            continue
        if getattr(laddr, "port", None) != port:
            continue
        pid = conn.pid
        if pid is None or pid == self_pid:
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def _is_listening(port: int) -> bool:
    """True iff at least one process is listening on *port*."""
    return bool(find_serve_pids(port))


def _terminate_pids(pids: list[int], *, timeout: float = GRACEFUL_TIMEOUT_SECONDS,
                    poll_interval: float = POLL_INTERVAL_SECONDS) -> tuple[list[int], list[int]]:
    """SIGTERM then SIGKILL *pids*.  Returns ``(stopped, failed)``.

    Mirrors the graceful-stop sequence used by Hermes itself: SIGTERM,
    poll for exit up to *timeout* seconds, then SIGKILL any survivors.
    A PID that is already gone (``ProcessLookupError``) counts as stopped.
    """
    stopped: list[int] = []
    failed: list[int] = []
    pending = list(pids)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            stopped.append(pid)
            pending.remove(pid)
        except (PermissionError, OSError):
            failed.append(pid)
            pending.remove(pid)
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        time.sleep(poll_interval)
        still_pending: list[int] = []
        for pid in pending:
            if _pid_alive(pid):
                still_pending.append(pid)
            else:
                stopped.append(pid)
        pending = still_pending
    for pid in pending:
        try:
            os.kill(pid, signal.SIGKILL)
            stopped.append(pid)
        except ProcessLookupError:
            stopped.append(pid)
        except (PermissionError, OSError):
            failed.append(pid)
    return stopped, failed


def _pid_alive(pid: int) -> bool:
    """True if *pid* still exists.

    ``os.kill(pid, 0)`` is the cheap probe.  ``PermissionError`` means the
    process exists but is owned by another user, so fall back to psutil
    to confirm it is still alive.
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        try:
            return psutil.pid_exists(pid)
        except (psutil.Error, OSError):
            return False
    except OSError:
        return False


def show_resolution(paths: ResolvedPaths, *, port: int = DEFAULT_SERVE_PORT) -> str:
    """Pretty-print what ``serve-stop`` would target, for debugging."""
    import json as _json
    pids = find_serve_pids(port)
    return _json.dumps(
        {
            "profile": paths.profile,
            "port": port,
            "running_pids": pids,
            "listening": bool(pids),
            "graceful_timeout_seconds": GRACEFUL_TIMEOUT_SECONDS,
        },
        indent=2,
    )


def run(
    paths: ResolvedPaths,
    *,
    port: int = DEFAULT_SERVE_PORT,
    apply: bool = True,
) -> dict[str, Any]:
    """Detect and (optionally) stop the Hermes backend on *port*.

    ``paths`` is accepted for symmetry with the other ``hermos`` features
    but is unused — detection is purely port-based and profile-agnostic.

    Returned report::

        {
          "ok": bool,
          "reason": str | None,   # "stopped" | "none" | "detected" | "partial"
          "port": int,
          "found_pids": [int, ...],
          "stopped_pids": [int, ...],
          "failed_pids": [int, ...],
        }

    * ``stopped`` — at least one backend was found and fully terminated.
    * ``none`` — no backend was listening on the port (success: nothing
      to stop).  ``ok`` is ``True``.
    * ``detected`` — dry-run: at least one backend found, no signal sent.
      ``ok`` is ``True``.
    * ``partial`` — some PIDs could not be signalled; ``ok`` is ``False``.
    """
    report: dict[str, Any] = {
        "ok": False,
        "reason": None,
        "port": port,
        "found_pids": [],
        "stopped_pids": [],
        "failed_pids": [],
    }

    pids = find_serve_pids(port)
    report["found_pids"] = list(pids)

    if not pids:
        report["ok"] = True
        report["reason"] = "none"
        return report

    if not apply:
        # Dry-run: report what we found without signalling.
        report["ok"] = True
        report["reason"] = "detected"
        return report

    stopped, failed = _terminate_pids(pids)
    report["stopped_pids"] = stopped
    report["failed_pids"] = failed

    if failed:
        report["ok"] = False
        report["reason"] = "partial"
    else:
        report["ok"] = True
        report["reason"] = "stopped"
    return report


# ---------- Human renderer ----------
def render_human(report: dict[str, Any]) -> tuple[int, str]:
    """Format *report* for the terminal.  Returns ``(exit_code, text)``.

    Exit codes: ``0`` for clean (stopped or nothing to stop), ``2`` for a
    tool error (partial failure).
    """
    lines: list[str] = []
    lines.append(f"Hermes serve-stop (port {report['port']})")
    lines.append("=" * 60)
    lines.append("")

    reason = report.get("reason")

    if reason == "none":
        lines.append(f"No Hermes backend listening on port {report['port']}.")
        lines.append("")
        lines.append("=" * 60)
        lines.append("VERDICT: clean — nothing to stop.")
        return 0, "\n".join(lines)

    found = report.get("found_pids") or []
    if found:
        lines.append(f"Detected backend PID(s): {', '.join(str(p) for p in found)}")

    if reason == "detected":
        lines.append("(dry-run: no signal sent.)")
        lines.append("")
        lines.append("=" * 60)
        lines.append("VERDICT: detected — re-run without --dry-run to stop.")
        return 0, "\n".join(lines)

    stopped = report.get("stopped_pids") or []
    failed = report.get("failed_pids") or []
    for pid in stopped:
        lines.append(f"  ✓ stopped PID {pid}")
    for pid in failed:
        lines.append(f"  ✗ failed to stop PID {pid}")

    if failed:
        lines.append("")
        lines.append("Some processes could not be stopped (permission denied or already gone).")
        lines.append("=" * 60)
        lines.append("VERDICT: tool error — partial failure.")
        return 2, "\n".join(lines)

    lines.append("")
    lines.append("=" * 60)
    lines.append("VERDICT: clean — backend stopped.")
    return 0, "\n".join(lines)
