"""Tests for talaria.hermes.serve_stop.

Layout:

* TestFindPids — psutil net_connections parsing, port filter, self-exclusion, dedup
* TestRun — full orchestration across all branches (mocked psutil + PIDs)
* TestPidAlive — os.kill(0) + psutil.pid_exists fallback
* TestRenderer — verdicts and exit codes
* TestCli — argparse + subprocess --help + --show-resolution + --json
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from collections import namedtuple

import psutil
import pytest

from talaria.hermes import serve_stop
from talaria.paths import ResolvedPaths


# ---------- Helpers ----------

Addr = namedtuple("Addr", ["ip", "port"])


def _conn(pid: int | None, port: int, status=psutil.CONN_LISTEN, family=0, laddr=None, raddr=None):
    """Build a psutil-like connection namedtuple.

    ``pid`` is the owning PID; ``port`` is the LISTEN port when status is
    CONN_LISTEN.  ``pid=None`` matches a socket psutil could not resolve an
    owner for.
    """
    if laddr is None:
        laddr = Addr("127.0.0.1", port)
    if raddr is None:
        raddr = ()
    return namedtuple(
        "conn", ["fd", "family", "type", "laddr", "raddr", "status", "pid"]
    )(fd=-1, family=family, type=1, laddr=laddr, raddr=raddr, status=status, pid=pid)


def _paths(tmp_path) -> ResolvedPaths:
    return ResolvedPaths(
        profile="test", hermes_root=tmp_path,
        state_db=tmp_path / "state.db",
        log_dir=tmp_path / "logs",
    )


def _mock_net_connections(monkeypatch, conns):
    """Stub psutil.net_connections to return *conns*."""
    monkeypatch.setattr(serve_stop.psutil, "net_connections", lambda kind="inet": conns)


# ---------- TestFindPids ----------

class TestFindPids:
    def test_finds_pid_listening_on_port(self, monkeypatch) -> None:
        conns = [
            _conn(4242, 9119),                # LISTEN on 9119 — match
            _conn(5353, 80),                  # LISTEN on 80 — no match
            _conn(None, 9119),                # no owner — ignored
        ]
        _mock_net_connections(monkeypatch, conns)
        pids = serve_stop.find_serve_pids(9119)
        assert pids == [4242]

    def test_excludes_self_pid(self, monkeypatch) -> None:
        self_pid = os.getpid()
        conns = [
            _conn(self_pid, 9119),            # our own socket — excluded
            _conn(11111, 9119),               # other process — included
        ]
        _mock_net_connections(monkeypatch, conns)
        pids = serve_stop.find_serve_pids(9119)
        assert self_pid not in pids
        assert 11111 in pids

    def test_ignores_non_listen_states(self, monkeypatch) -> None:
        conns = [
            _conn(1234, 9119, status=psutil.CONN_ESTABLISHED),
            _conn(5678, 9119, status=psutil.CONN_CLOSE_WAIT),
            _conn(4242, 9119, status=psutil.CONN_LISTEN),
        ]
        _mock_net_connections(monkeypatch, conns)
        pids = serve_stop.find_serve_pids(9119)
        assert pids == [4242]

    def test_deduplicates_pid_with_multiple_sockets(self, monkeypatch) -> None:
        conns = [
            _conn(2222, 9119),
            _conn(2222, 9119),                # same PID, same port — dedup
        ]
        _mock_net_connections(monkeypatch, conns)
        pids = serve_stop.find_serve_pids(9119)
        assert pids == [2222]

    def test_ignores_connection_with_no_laddr(self, monkeypatch) -> None:
        conn = namedtuple("conn", ["fd", "family", "type", "laddr", "raddr", "status", "pid"])(
            fd=-1, family=0, type=1, laddr=None, raddr=None,
            status=psutil.CONN_LISTEN, pid=1234,
        )
        _mock_net_connections(monkeypatch, [conn])
        assert serve_stop.find_serve_pids(9119) == []

    def test_returns_empty_on_psutil_error(self, monkeypatch) -> None:
        def boom(kind="inet"):
            raise psutil.AccessDenied("denied")
        monkeypatch.setattr(serve_stop.psutil, "net_connections", boom)
        assert serve_stop.find_serve_pids(9119) == []

    def test_returns_empty_on_oserror(self, monkeypatch) -> None:
        def boom(kind="inet"):
            raise OSError("nope")
        monkeypatch.setattr(serve_stop.psutil, "net_connections", boom)
        assert serve_stop.find_serve_pids(9119) == []

    def test_empty_connection_list(self, monkeypatch) -> None:
        _mock_net_connections(monkeypatch, [])
        assert serve_stop.find_serve_pids(9119) == []


# ---------- TestRun ----------

class TestRun:
    def test_none_when_port_silent(self, tmp_path, monkeypatch) -> None:
        _mock_net_connections(monkeypatch, [_conn(12345, 80)])  # port 80, not 9119
        report = serve_stop.run(_paths(tmp_path), port=9119)
        assert report["ok"] is True
        assert report["reason"] == "none"
        assert report["found_pids"] == []

    def test_dry_run_reports_detected_without_signalling(self, tmp_path, monkeypatch) -> None:
        _mock_net_connections(monkeypatch, [_conn(12345, 9119)])

        kills: list[tuple[int, int]] = []
        monkeypatch.setattr(serve_stop.os, "kill", lambda pid, sig: kills.append((pid, sig)))

        report = serve_stop.run(_paths(tmp_path), port=9119, apply=False)
        assert report["ok"] is True
        assert report["reason"] == "detected"
        assert report["found_pids"] == [12345]
        assert kills == []  # no signal sent

    def test_stop_sends_sigterm_then_sigkill_on_survivor(self, tmp_path, monkeypatch) -> None:
        _mock_net_connections(monkeypatch, [_conn(12345, 9119)])

        # Force the PID to always look alive so SIGKILL is reached.
        monkeypatch.setattr(serve_stop, "_pid_alive", lambda pid: True)
        sent: list[tuple[int, int]] = []
        monkeypatch.setattr(serve_stop.os, "kill", lambda pid, sig: sent.append((pid, sig)))

        report = serve_stop.run(_paths(tmp_path), port=9119)
        assert report["reason"] == "stopped"
        assert report["stopped_pids"] == [12345]
        # SIGTERM sent first, then SIGKILL.
        assert (12345, signal.SIGTERM) in sent
        assert (12345, signal.SIGKILL) in sent

    def test_stop_clean_when_process_exits_after_sigterm(self, tmp_path, monkeypatch) -> None:
        _mock_net_connections(monkeypatch, [_conn(12345, 9119)])

        alive = {"state": True}
        monkeypatch.setattr(serve_stop, "_pid_alive", lambda pid: alive["state"])

        def fake_kill(pid: int, sig: int) -> None:
            if sig == signal.SIGTERM:
                alive["state"] = False

        monkeypatch.setattr(serve_stop.os, "kill", fake_kill)

        report = serve_stop.run(_paths(tmp_path), port=9119)
        assert report["ok"] is True
        assert report["reason"] == "stopped"
        assert report["stopped_pids"] == [12345]
        assert report["failed_pids"] == []

    def test_partial_failure_on_permission_denied(self, tmp_path, monkeypatch) -> None:
        _mock_net_connections(monkeypatch, [_conn(12345, 9119)])

        def fake_kill(pid: int, sig: int) -> None:
            raise PermissionError("not allowed")

        monkeypatch.setattr(serve_stop.os, "kill", fake_kill)

        report = serve_stop.run(_paths(tmp_path), port=9119)
        assert report["ok"] is False
        assert report["reason"] == "partial"
        assert report["failed_pids"] == [12345]

    def test_processlookup_counts_as_stopped(self, tmp_path, monkeypatch) -> None:
        _mock_net_connections(monkeypatch, [_conn(12345, 9119)])

        def fake_kill(pid: int, sig: int) -> None:
            raise ProcessLookupError("gone")

        monkeypatch.setattr(serve_stop.os, "kill", fake_kill)

        report = serve_stop.run(_paths(tmp_path), port=9119)
        assert report["ok"] is True
        assert report["reason"] == "stopped"
        assert report["stopped_pids"] == [12345]

    def test_find_serve_pids_end_to_end(self, monkeypatch) -> None:
        conns = [
            _conn(7777, 9119),
            _conn(8888, 9118),                # different port — no match
        ]
        _mock_net_connections(monkeypatch, conns)
        pids = serve_stop.find_serve_pids(9119)
        assert pids == [7777]


# ---------- TestPidAlive ----------

class TestPidAlive:
    def test_self_is_alive(self) -> None:
        assert serve_stop._pid_alive(os.getpid()) is True

    def test_nonexistent_pid_is_gone(self) -> None:
        # PID 0 is never a valid target; use a very high PID unlikely to exist.
        assert serve_stop._pid_alive(999999) is False

    def test_permission_denied_falls_back_to_psutil(self, monkeypatch) -> None:
        # Simulate a process owned by another user: os.kill(pid,0) raises
        # PermissionError, and psutil.pid_exists should be consulted.
        def kill_raises(pid, sig):
            raise PermissionError("denied")

        monkeypatch.setattr(serve_stop.os, "kill", kill_raises)
        monkeypatch.setattr(serve_stop.psutil, "pid_exists", lambda pid: True)
        assert serve_stop._pid_alive(5555) is True

    def test_permission_denied_psutil_confirms_dead(self, monkeypatch) -> None:
        def kill_raises(pid, sig):
            raise PermissionError("denied")

        monkeypatch.setattr(serve_stop.os, "kill", kill_raises)
        monkeypatch.setattr(serve_stop.psutil, "pid_exists", lambda pid: False)
        assert serve_stop._pid_alive(5555) is False


# ---------- TestRenderer ----------

class TestRenderer:
    def _report(self, **overrides) -> dict:
        base = {
            "ok": True, "reason": "stopped", "port": 9119,
            "found_pids": [12345], "stopped_pids": [12345], "failed_pids": [],
        }
        base.update(overrides)
        return base

    def test_clean_stop(self) -> None:
        code, text = serve_stop.render_human(self._report())
        assert code == 0
        assert "VERDICT: clean" in text
        assert "12345" in text

    def test_none_running(self) -> None:
        code, text = serve_stop.render_human(self._report(
            reason="none", found_pids=[], stopped_pids=[],
        ))
        assert code == 0
        assert "nothing to stop" in text

    def test_dry_run_detected(self) -> None:
        code, text = serve_stop.render_human(self._report(
            reason="detected", stopped_pids=[],
        ))
        assert code == 0
        assert "dry-run" in text

    def test_partial_failure_exits_2(self) -> None:
        code, text = serve_stop.render_human(self._report(
            ok=False, reason="partial", failed_pids=[12345],
        ))
        assert code == 2
        assert "partial failure" in text
        assert "12345" in text


# ---------- TestCli ----------

class TestCli:
    """End-to-end CLI tests via subprocess — proves the entry point works."""

    def test_help_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "serve-stop", "--help"],
            capture_output=True, text=True, cwd="/home/raphael/src/rb/talaria-of-mercury",
        )
        assert result.returncode == 0
        assert "9119" in result.stdout
        assert "port" in result.stdout.lower()

    def test_show_resolution_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "serve-stop",
             "--port", "9119", "--show-resolution"],
            capture_output=True, text=True, cwd="/home/raphael/src/rb/talaria-of-mercury",
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["port"] == 9119
        assert "listening" in payload
        assert "graceful_timeout_seconds" in payload

    def test_json_none_when_port_silent(self) -> None:
        # Use an unlikely-to-be-bound port.
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "serve-stop",
             "--port", "39998", "--json"],
            capture_output=True, text=True, cwd="/home/raphael/src/rb/talaria-of-mercury",
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["reason"] == "none"
        assert payload["port"] == 39998

    def test_dry_run_does_not_kill(self) -> None:
        # --dry-run must never send a signal even if something is listening.
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "serve-stop",
             "--port", "39999", "--dry-run", "--json"],
            capture_output=True, text=True, cwd="/home/raphael/src/rb/talaria-of-mercury",
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["reason"] in ("none", "detected")
