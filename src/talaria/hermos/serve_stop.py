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
listening on.  A listening socket has a stable kernel identity (its inode)
regardless of how the owning process was spelled on the command line, so
``/proc/net/tcp`` → inode → PID via ``/proc/<pid>/fd`` finds the process
that ``hermes serve --stop`` cannot.

Exit semantics (project-wide contract):

* ``0`` — stopped at least one backend, or none was running.
* ``2`` — tool error: not on Linux, a PID that could not be signalled,
  or a port with no listener that the caller asserted was running.

This feature is Linux-only (``/proc`` is the discovery substrate).  On
other platforms it reports ``ok: False`` with ``reason: "unsupported"``
rather than attempting a fallback.
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path
from typing import Any

from talaria.paths import ResolvedPaths

#: Default port the Hermes dashboard / ``hermes serve`` backend binds.
DEFAULT_SERVE_PORT = 9119

#: How long to wait for a SIGTERM'd backend to exit before SIGKILL.
GRACEFUL_TIMEOUT_SECONDS = 5.0

#: Poll interval while waiting for graceful exit.
POLL_INTERVAL_SECONDS = 0.1

#: TCP status code for ``LISTEN`` in ``/proc/net/tcp``.
_TCP_LISTEN = "0A"


def _parse_proc_net_tcp(path: Path) -> dict[int, int]:
    """Return ``{socket_inode: 0}`` for every LISTEN socket in *path*.

    Only the inode is needed; the value is a placeholder kept at ``0`` so
    callers can treat the dict as a set with cheap membership tests while
    still being able to extend it later if a use case needs the local
    address.  The value is intentionally not the local port — that would
    be misleading because one inode maps to exactly one listener.
    """
    inodes: dict[int, int] = {}
    try:
        text = path.read_text()
    except OSError:
        return inodes
    for line in text.splitlines()[1:]:  # skip the header row
        parts = line.split()
        # min columns: sl local_address rem_address st tx_queue rx_queue ...
        if len(parts) < 10:
            continue
        st = parts[3]
        if st != _TCP_LISTEN:
            continue
        inode_field = parts[9]
        try:
            inode = int(inode_field)
        except ValueError:
            continue
        if inode > 0:
            inodes[inode] = 0
    return inodes


def _hex_port(local_address: str) -> int | None:
    """Decode the port half of a ``/proc/net/tcp`` local address field.

    The field is ``HEX_IP:HEX_PORT`` (e.g. ``"0100007F:23AF"``).  Only the
    port is returned; the IP is discarded because we match by port, and
    callers that need the address can read it from the raw line.
    """
    if ":" not in local_address:
        return None
    port_hex = local_address.rsplit(":", 1)[1]
    try:
        return int(port_hex, 16)
    except ValueError:
        return None


def _listening_inodes_for_port(port: int, *, net_root: Path | None = None) -> set[int]:
    """Return socket inodes LISTENing on *port* across tcp + tcp6.

    ``net_root`` defaults to ``/proc/net`` and is overridable for tests.
    """
    root = net_root or Path("/proc/net")
    hits: set[int] = set()
    for name in ("tcp", "tcp6"):
        path = root / name
        try:
            text = path.read_text()
        except OSError:
            continue
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 10:
                continue
            if parts[3] != _TCP_LISTEN:
                continue
            local = parts[1]
            if _hex_port(local) != port:
                continue
            try:
                inode = int(parts[9])
            except ValueError:
                continue
            if inode > 0:
                hits.add(inode)
    return hits


def _proc_fd_root() -> Path:
    """Return the ``/proc`` filesystem root (overridable via env in tests)."""
    return Path(os.environ.get("TALARIA_PROC_ROOT", "/proc"))


def _pids_for_inodes(inodes: set[int], *, proc_root: Path | None = None) -> list[int]:
    """Return PIDs that own any socket inode in *inodes*.

    Scans ``/proc/<pid>/fd/*`` for socket symlinks whose target is
    ``socket:[<inode>]``.  Each PID is reported at most once even if it
    owns several matching sockets.  The current process is excluded so a
    stop command never targets itself.
    """
    if not inodes:
        return []
    root = proc_root or _proc_fd_root()
    self_pid = os.getpid()
    targets = {f"socket:[{i}]" for i in inodes}
    found: list[int] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return found
    for entry in entries:
        name = entry.name
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == self_pid:
            continue
        fd_dir = entry / "fd"
        try:
            for fd in fd_dir.iterdir():
                try:
                    link = os.readlink(fd)
                except OSError:
                    continue
                if link in targets:
                    if pid not in found:
                        found.append(pid)
                    break
        except OSError:
            continue
    return found


def find_serve_pids(port: int = DEFAULT_SERVE_PORT, *, proc_root: Path | None = None) -> list[int]:
    """Return PIDs of processes listening on *port*.

    Combines the port→inode lookup with the inode→PID lookup.  Returns an
    empty list on Linux if nothing is listening, or on any platform where
    ``/proc`` is unavailable.
    """
    root = proc_root or _proc_fd_root()
    net_root = root / "net"
    inodes = _listening_inodes_for_port(port, net_root=net_root)
    return _pids_for_inodes(inodes, proc_root=root)


def _is_listening(port: int, *, proc_root: Path | None = None) -> bool:
    """True iff at least one process is listening on *port*."""
    return bool(find_serve_pids(port, proc_root=proc_root))


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
    process exists but is owned by another user, so fall back to a
    ``/proc/<pid>`` stat to confirm it is still alive.
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        try:
            Path(f"/proc/{pid}").stat()
            return True
        except OSError:
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
          "reason": str | None,   # "stopped" | "none" | "unsupported" | "partial"
          "port": int,
          "found_pids": [int, ...],
          "stopped_pids": [int, ...],
          "failed_pids": [int, ...],
        }

    * ``stopped`` — at least one backend was found and fully terminated.
    * ``none`` — no backend was listening on the port (success: nothing
      to stop).  ``ok`` is ``True``.
    * ``unsupported`` — not on Linux (no ``/proc``); ``ok`` is ``False``.
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

    proc_root = _proc_fd_root()
    if not (proc_root / "net" / "tcp").exists():
        report["reason"] = "unsupported"
        return report

    pids = find_serve_pids(port, proc_root=proc_root)
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
    tool error (unsupported platform, partial failure).
    """
    lines: list[str] = []
    lines.append(f"Hermes serve-stop (port {report['port']})")
    lines.append("=" * 60)
    lines.append("")

    reason = report.get("reason")
    if reason == "unsupported":
        lines.append("ERROR: /proc/net not found — serve-stop is Linux-only.")
        lines.append("  On macOS/Windows, stop the backend directly or use a process manager.")
        lines.append("")
        lines.append("=" * 60)
        lines.append("VERDICT: tool error — unsupported platform.")
        return 2, "\n".join(lines)

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
