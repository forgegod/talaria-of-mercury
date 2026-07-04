"""Tests for talaria.hermos.serve_stop.

Layout (mirrors test_refresh_catalog.py):

* TestProcParsing — /proc/net/tcp + port/inode decoding
* TestPidLookup — inode→PID via /proc/<pid>/fd
* TestRun — full orchestration across all branches (mocked /proc + PIDs)
* TestRenderer — verdicts and exit codes
* TestCli — argparse + subprocess --help + --show-resolution + --json
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from talaria.hermos import serve_stop
from talaria.paths import ResolvedPaths


# ---------- Helpers ----------

def _write_proc_net(path: Path, rows: list[tuple[int, str, str]]) -> None:
    """Write a synthetic /proc/net/tcp file.

    rows = [(port, state, inode), ...] e.g. (9119, "0A", "12345").
    The port is encoded to hex so callers never have to compute it.
    """
    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    lines = [header]
    for i, (port, st, inode) in enumerate(rows):
        port_hex = f"{port:04X}"
        local = f"0100007F:{port_hex}"
        # Pad the row like the real kernel output.
        lines.append(
            f"   {i}: {local:<14} 00000000:0000 {st} 00000000:00000000 00:00000000 00000000     0        0 {inode} 1 0000000000000000 100 0 0 10 0\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def _make_fd_socket_link(proc_root: Path, pid: int, inode: int) -> None:
    """Create a /proc/<pid>/fd/N symlink pointing at socket:[<inode>]."""
    fd_dir = proc_root / str(pid) / "fd"
    fd_dir.mkdir(parents=True, exist_ok=True)
    link = fd_dir / "3"
    link.symlink_to(f"socket:[{inode}]")


def _paths(tmp_path: Path) -> ResolvedPaths:
    return ResolvedPaths(
        profile="test", hermes_root=tmp_path,
        state_db=tmp_path / "state.db",
        log_dir=tmp_path / "logs",
    )


# ---------- TestProcParsing ----------

class TestProcParsing:
    def test_parse_listening_inodes(self, tmp_path: Path) -> None:
        net = tmp_path / "net"
        _write_proc_net(net / "tcp", [
            (9119, "0A", "1001"),   # LISTEN on 9119
            (80, "0A", "1002"),     # LISTEN on port 80
            (9119, "06", "1003"),   # TIME_WAIT on 9119 — ignored
        ])
        inodes = serve_stop._listening_inodes_for_port(9119, net_root=net)
        assert inodes == {1001}

    def test_parse_handles_missing_tcp6(self, tmp_path: Path) -> None:
        net = tmp_path / "net"
        net.mkdir()
        _write_proc_net(net / "tcp", [(9119, "0A", "1001")])
        # tcp6 absent — should not raise.
        inodes = serve_stop._listening_inodes_for_port(9119, net_root=net)
        assert inodes == {1001}

    def test_parse_empty_file(self, tmp_path: Path) -> None:
        net = tmp_path / "net"
        net.mkdir()
        (net / "tcp").write_text("  sl  local_address rem_address   st\n")
        inodes = serve_stop._listening_inodes_for_port(9119, net_root=net)
        assert inodes == set()

    def test_hex_port_decodes(self) -> None:
        assert serve_stop._hex_port("0100007F:23AF") == 0x23AF
        assert serve_stop._hex_port("0100007F:0050") == 80
        assert serve_stop._hex_port("garbage") is None
        assert serve_stop._hex_port("0100007F:nothex") is None


# ---------- TestPidLookup ----------

class TestPidLookup:
    def test_finds_pid_owning_inode(self, tmp_path: Path) -> None:
        _make_fd_socket_link(tmp_path, 4242, 1001)
        _make_fd_socket_link(tmp_path, 5353, 9999)
        pids = serve_stop._pids_for_inodes({1001}, proc_root=tmp_path)
        assert pids == [4242]

    def test_excludes_self_pid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self_pid = os.getpid()
        _make_fd_socket_link(tmp_path, self_pid, 1001)
        _make_fd_socket_link(tmp_path, 11111, 1001)
        pids = serve_stop._pids_for_inodes({1001}, proc_root=tmp_path)
        assert self_pid not in pids
        assert 11111 in pids

    def test_empty_inodes_returns_empty(self, tmp_path: Path) -> None:
        _make_fd_socket_link(tmp_path, 11111, 1001)
        assert serve_stop._pids_for_inodes(set(), proc_root=tmp_path) == []

    def test_deduplicates_pid_with_multiple_fds(self, tmp_path: Path) -> None:
        pid_dir = tmp_path / "2222" / "fd"
        pid_dir.mkdir(parents=True)
        (pid_dir / "3").symlink_to("socket:[1001]")
        (pid_dir / "4").symlink_to("socket:[1001]")
        pids = serve_stop._pids_for_inodes({1001}, proc_root=tmp_path)
        assert pids == [2222]

    def test_missing_proc_root_returns_empty(self, tmp_path: Path) -> None:
        ghost = tmp_path / "does-not-exist"
        assert serve_stop._pids_for_inodes({1001}, proc_root=ghost) == []


# ---------- TestRun ----------

class TestRun:
    def test_unsupported_platform_when_no_proc(self, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
        # Point proc_root at a dir with no net/tcp.
        empty = tmp_path / "empty_proc"
        empty.mkdir()
        monkeypatch.setenv("TALARIA_PROC_ROOT", str(empty))
        report = serve_stop.run(_paths(tmp_path), port=9119)
        assert report["ok"] is False
        assert report["reason"] == "unsupported"

    def test_none_when_port_silent(self, tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
        proc = tmp_path / "proc"
        _write_proc_net(proc / "net" / "tcp", [(80, "0A", "1002")])  # port 80, not 9119
        monkeypatch.setenv("TALARIA_PROC_ROOT", str(proc))
        report = serve_stop.run(_paths(tmp_path), port=9119)
        assert report["ok"] is True
        assert report["reason"] == "none"
        assert report["found_pids"] == []

    def test_dry_run_reports_detected_without_signalling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proc = tmp_path / "proc"
        _write_proc_net(proc / "net" / "tcp", [(9119, "0A", "1001")])
        _make_fd_socket_link(proc, 12345, 1001)
        monkeypatch.setenv("TALARIA_PROC_ROOT", str(proc))

        kills: list[tuple[int, int]] = []
        monkeypatch.setattr(serve_stop.os, "kill", lambda pid, sig: kills.append((pid, sig)))

        report = serve_stop.run(_paths(tmp_path), port=9119, apply=False)
        assert report["ok"] is True
        assert report["reason"] == "detected"
        assert report["found_pids"] == [12345]
        assert kills == []  # no signal sent

    def test_stop_sends_sigterm_then_sigkill_on_survivor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proc = tmp_path / "proc"
        _write_proc_net(proc / "net" / "tcp", [(9119, "0A", "1001")])
        _make_fd_socket_link(proc, 12345, 1001)
        monkeypatch.setenv("TALARIA_PROC_ROOT", str(proc))

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

    def test_stop_clean_when_process_exits_after_sigterm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proc = tmp_path / "proc"
        _write_proc_net(proc / "net" / "tcp", [(9119, "0A", "1001")])
        _make_fd_socket_link(proc, 12345, 1001)
        monkeypatch.setenv("TALARIA_PROC_ROOT", str(proc))

        # PID exits immediately after SIGTERM — no SIGKILL needed.
        alive = {"state": True}
        monkeypatch.setattr(serve_stop, "_pid_alive",
                            lambda pid: alive["state"])

        def fake_kill(pid: int, sig: int) -> None:
            if sig == signal.SIGTERM:
                alive["state"] = False

        monkeypatch.setattr(serve_stop.os, "kill", fake_kill)

        report = serve_stop.run(_paths(tmp_path), port=9119)
        assert report["ok"] is True
        assert report["reason"] == "stopped"
        assert report["stopped_pids"] == [12345]
        assert report["failed_pids"] == []

    def test_partial_failure_on_permission_denied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proc = tmp_path / "proc"
        _write_proc_net(proc / "net" / "tcp", [(9119, "0A", "1001")])
        _make_fd_socket_link(proc, 12345, 1001)
        monkeypatch.setenv("TALARIA_PROC_ROOT", str(proc))

        def fake_kill(pid: int, sig: int) -> None:
            raise PermissionError("not allowed")

        monkeypatch.setattr(serve_stop.os, "kill", fake_kill)

        report = serve_stop.run(_paths(tmp_path), port=9119)
        assert report["ok"] is False
        assert report["reason"] == "partial"
        assert report["failed_pids"] == [12345]

    def test_processlookup_counts_as_stopped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proc = tmp_path / "proc"
        _write_proc_net(proc / "net" / "tcp", [(9119, "0A", "1001")])
        _make_fd_socket_link(proc, 12345, 1001)
        monkeypatch.setenv("TALARIA_PROC_ROOT", str(proc))

        def fake_kill(pid: int, sig: int) -> None:
            raise ProcessLookupError("gone")

        monkeypatch.setattr(serve_stop.os, "kill", fake_kill)

        report = serve_stop.run(_paths(tmp_path), port=9119)
        assert report["ok"] is True
        assert report["reason"] == "stopped"
        assert report["stopped_pids"] == [12345]

    def test_find_serve_pids_end_to_end(self, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
        proc = tmp_path / "proc"
        _write_proc_net(proc / "net" / "tcp", [(9119, "0A", "1001")])
        _make_fd_socket_link(proc, 7777, 1001)
        _make_fd_socket_link(proc, 8888, 2002)  # unrelated inode
        monkeypatch.setenv("TALARIA_PROC_ROOT", str(proc))
        pids = serve_stop.find_serve_pids(9119)
        assert pids == [7777]


# ---------- TestPidAlive ----------

class TestPidAlive:
    def test_self_is_alive(self) -> None:
        assert serve_stop._pid_alive(os.getpid()) is True

    def test_nonexistent_pid_is_gone(self) -> None:
        # PID 0 is never a valid target on Linux.
        # Use a very high PID unlikely to exist.
        assert serve_stop._pid_alive(999999) is False


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

    def test_unsupported_platform_exits_2(self) -> None:
        code, text = serve_stop.render_human(self._report(
            ok=False, reason="unsupported", found_pids=[], stopped_pids=[],
        ))
        assert code == 2
        assert "Linux-only" in text

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
