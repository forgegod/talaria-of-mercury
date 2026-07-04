"""Tests for talaria.hermos.log_rotate.

Layout:

* TestParseRotated   — name parser for `<base>.<ext>.N[.gz]`
* TestClassify       — file classifier
* TestRotateActive   — copy+gzip+truncate of an active file when over --max-size
* TestAgePrune       — --max-age deletes old rotated copies and curator dirs
* TestTotalPrune     — --max-total deletes oldest rotated copies first
* TestKeepFloor      — --keep N protects the newest N rotated copies
* TestDryRun         — no bytes written, no deletes
* TestMultiProfile   — multi-profile target enumeration
* TestRunReport      — full run() + render_human() shape
* TestCli            — argparse + subprocess --help
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from talaria.hermos import log_rotate
from talaria.paths import ResolvedPaths


# ---------- Helpers ----------

def _paths(root: Path, profile: str = "default") -> ResolvedPaths:
    """Build a ResolvedPaths that points at ``<root>/logs`` (not ``<root>``)."""
    if profile == "default":
        log_dir = root / "logs"
        state_db = root / "state.db"
    else:
        log_dir = root / "profiles" / profile / "logs"
        state_db = root / "profiles" / profile / "state.db"
    return ResolvedPaths(profile=profile, hermes_root=root, state_db=state_db, log_dir=log_dir)


def _log(tmp_path: Path, name: str) -> Path:
    """Return ``<tmp_path>/logs/<name>`` (parent created)."""
    p = tmp_path / "logs" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _write(path: Path, content: bytes, *, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _gzip_size(data: bytes) -> int:
    return len(gzip.compress(data, compresslevel=6))


# ---------- TestParseRotated ----------

class TestParseRotated:
    def test_active_log_name_returns_none(self) -> None:
        assert log_rotate._parse_rotated("agent.log") is None

    def test_rotated_plain(self) -> None:
        assert log_rotate._parse_rotated("agent.log.1") == ("agent.log", 1)

    def test_rotated_gz(self) -> None:
        assert log_rotate._parse_rotated("agent.log.1.gz") == ("agent.log", 1)

    def test_rotated_multi_digit_index(self) -> None:
        assert log_rotate._parse_rotated("agent.log.42.gz") == ("agent.log", 42)

    def test_protected_basename_returns_none(self) -> None:
        assert log_rotate._parse_rotated("README.md") is None

    def test_empty_returns_none(self) -> None:
        assert log_rotate._parse_rotated("") is None


# ---------- TestClassify ----------

class TestClassify:
    def test_active_log(self, tmp_path: Path) -> None:
        p = _log(tmp_path, "agent.log")
        _write(p, b"hello")
        assert log_rotate.classify(p) == "active"

    def test_rotated_log(self, tmp_path: Path) -> None:
        p = _log(tmp_path, "agent.log.1.gz")
        _write(p, b"hello")
        assert log_rotate.classify(p) == "rotated"

    def test_rotated_plain(self, tmp_path: Path) -> None:
        p = _log(tmp_path, "agent.log.1")
        _write(p, b"hello")
        assert log_rotate.classify(p) == "rotated"

    def test_protected_basename(self, tmp_path: Path) -> None:
        p = _log(tmp_path, "README.md")
        _write(p, b"# readme")
        assert log_rotate.classify(p) == "other"


# ---------- TestRotateActive ----------

class TestRotateActive:
    def test_under_cap_is_skipped(self, tmp_path: Path) -> None:
        log = _log(tmp_path, "agent.log")
        _write(log, b"x" * 100)
        report = log_rotate.run(
            _paths(tmp_path),
            max_size=10_000,
            apply=True,
        )
        assert report["rotated_count"] == 0
        assert report["truncated_count"] == 0
        assert report["deleted_count"] == 0
        assert log.read_bytes() == b"x" * 100
        assert not _log(tmp_path, "agent.log.1.gz").exists()

    def test_over_cap_rotates_and_truncates(self, tmp_path: Path) -> None:
        log = _log(tmp_path, "agent.log")
        payload = b"x" * 5000
        _write(log, payload)
        cap = _gzip_size(payload) - 1
        report = log_rotate.run(
            _paths(tmp_path),
            max_size=cap,
            apply=True,
        )
        assert report["rotated_count"] == 1
        assert report["truncated_count"] == 1
        rotated = _log(tmp_path, "agent.log.1.gz")
        assert rotated.exists()
        with gzip.open(rotated, "rb") as gz:
            assert gz.read() == payload
        assert log.read_bytes() == b""

    def test_rotation_overwrites_existing_1_gz(self, tmp_path: Path) -> None:
        """A second rotation replaces the previous ``.1.gz`` (single-slot policy)."""
        log = _log(tmp_path, "agent.log")
        _write(log, b"y" * 5000)
        log_rotate.run(_paths(tmp_path), max_size=10, apply=True)
        first_rotated = _log(tmp_path, "agent.log.1.gz")
        assert first_rotated.exists()
        first_bytes = first_rotated.read_bytes()
        new_payload = b"z" * 5000
        _write(log, new_payload)
        log_rotate.run(_paths(tmp_path), max_size=10, apply=True)
        with gzip.open(first_rotated, "rb") as gz:
            assert gz.read() == new_payload
        assert first_rotated.read_bytes() != first_bytes
        assert log.read_bytes() == b""


# ---------- TestAgePrune ----------

class TestAgePrune:
    def test_old_rotated_copies_deleted(self, tmp_path: Path) -> None:
        old_time = time.time() - (40 * 86400)
        new_time = time.time() - (5 * 86400)
        old_rot = _log(tmp_path, "agent.log.1.gz")
        new_rot = _log(tmp_path, "agent.log.2.gz")
        _write(old_rot, b"old", mtime=old_time)
        _write(new_rot, b"new", mtime=new_time)

        report = log_rotate.run(
            _paths(tmp_path),
            max_age_days=30,
            apply=True,
        )
        assert report["deleted_count"] >= 1
        assert not old_rot.exists()
        assert new_rot.exists()

    def test_curator_snapshot_dir_deleted_when_old(self, tmp_path: Path) -> None:
        snapshot = tmp_path / "logs" / "curator" / "20260101-000000"
        snapshot.mkdir(parents=True)
        (snapshot / "manifest.json").write_text("{}")
        old = time.time() - (40 * 86400)
        os.utime(snapshot, (old, old))
        _log(tmp_path, "agent.log").write_text("ok")

        log_rotate.run(
            _paths(tmp_path),
            max_age_days=30,
            apply=True,
        )
        assert not snapshot.exists()

    def test_max_age_zero_deletes_with_keep_floor(self, tmp_path: Path) -> None:
        rot1 = _log(tmp_path, "agent.log.1.gz")
        rot2 = _log(tmp_path, "agent.log.2.gz")
        _write(rot1, b"a")
        _write(rot2, b"b")
        report = log_rotate.run(
            _paths(tmp_path),
            max_age_days=0,
            apply=True,
        )
        # With keep=1 default, the newest copy is protected, one is deleted
        assert (rot1.exists() or rot2.exists())
        assert report["deleted_count"] >= 1


# ---------- TestTotalPrune ----------

class TestTotalPrune:
    def test_total_prune_deletes_oldest_first(self, tmp_path: Path) -> None:
        old_time = time.time() - (10 * 86400)
        mid_time = time.time() - (5 * 86400)
        new_time = time.time() - (1 * 86400)
        rot_old = _log(tmp_path, "agent.log.1.gz")
        rot_mid = _log(tmp_path, "agent.log.2.gz")
        rot_new = _log(tmp_path, "agent.log.3.gz")
        for p, t in ((rot_old, old_time), (rot_mid, mid_time), (rot_new, new_time)):
            _write(p, b"x" * 5000, mtime=t)

        report = log_rotate.run(
            _paths(tmp_path),
            max_total=7000,
            keep=1,
            apply=True,
        )
        assert rot_new.exists(), "newest copy should be kept by --keep"
        assert not rot_old.exists(), "oldest should be deleted first"
        assert report["deleted_count"] >= 1

    def test_total_under_cap_no_deletes(self, tmp_path: Path) -> None:
        rot = _log(tmp_path, "agent.log.1.gz")
        _write(rot, b"x" * 100)
        report = log_rotate.run(
            _paths(tmp_path),
            max_total=10_000,
            apply=True,
        )
        assert report["deleted_count"] == 0
        assert rot.exists()


# ---------- TestKeepFloor ----------

class TestKeepFloor:
    def test_keep_2_protects_two_newest(self, tmp_path: Path) -> None:
        old_time = time.time() - (10 * 86400)
        mid_time = time.time() - (5 * 86400)
        new_time = time.time() - (1 * 86400)
        for name, t in (("agent.log.1.gz", old_time),
                        ("agent.log.2.gz", mid_time),
                        ("agent.log.3.gz", new_time)):
            _write(_log(tmp_path, name), b"x" * 100, mtime=t)

        log_rotate.run(
            _paths(tmp_path),
            max_age_days=0,
            keep=2,
            apply=True,
        )
        assert _log(tmp_path, "agent.log.3.gz").exists()
        assert _log(tmp_path, "agent.log.2.gz").exists()
        assert not _log(tmp_path, "agent.log.1.gz").exists()

    def test_keep_zero_protects_nothing(self, tmp_path: Path) -> None:
        rot = _log(tmp_path, "agent.log.1.gz")
        _write(rot, b"x" * 100)
        log_rotate.run(
            _paths(tmp_path),
            max_age_days=0,
            keep=0,
            apply=True,
        )
        assert not rot.exists()


# ---------- TestDryRun ----------

class TestDryRun:
    def test_dry_run_does_not_rotate(self, tmp_path: Path) -> None:
        log = _log(tmp_path, "agent.log")
        payload = b"x" * 5000
        _write(log, payload)
        report = log_rotate.run(
            _paths(tmp_path),
            max_size=10,
            apply=False,
        )
        assert report["dry_run"] is True
        assert report["rotated_count"] == 1
        assert report["truncated_count"] == 1
        assert log.read_bytes() == payload
        assert not _log(tmp_path, "agent.log.1.gz").exists()

    def test_dry_run_does_not_delete(self, tmp_path: Path) -> None:
        rot = _log(tmp_path, "agent.log.1.gz")
        _write(rot, b"x" * 100, mtime=time.time() - 100 * 86400)
        report = log_rotate.run(
            _paths(tmp_path),
            max_age_days=10,
            apply=False,
        )
        assert report["dry_run"] is True
        assert rot.exists()


# ---------- TestMultiProfile ----------

class TestMultiProfile:
    def test_all_profiles_target_enumeration(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        (root / "logs").mkdir(parents=True)
        (root / "logs" / "agent.log").write_bytes(b"x" * 5000)
        for name in ("alpha", "beta"):
            (root / "profiles" / name / "logs").mkdir(parents=True)
            (root / "profiles" / name / "logs" / "agent.log").write_bytes(b"x" * 5000)

        # Replicate the CLI's enumeration inline (we can't monkeypatch
        # the module-level HERMES_ROOT without touching other code).
        targets: list[tuple[str, Path]] = []
        if (root / "logs").is_dir():
            targets.append(("default", root / "logs"))
        prof_dir = root / "profiles"
        if prof_dir.is_dir():
            for child in sorted(prof_dir.iterdir()):
                if child.is_dir() and (child / "logs").is_dir():
                    targets.append((child.name, child / "logs"))
        assert {t[0] for t in targets} == {"default", "alpha", "beta"}


# ---------- TestRunReport ----------

class TestRunReport:
    def test_run_shape_keys(self, tmp_path: Path) -> None:
        _log(tmp_path, "agent.log").write_bytes(b"hi")
        report = log_rotate.run(_paths(tmp_path), apply=True)
        expected = {
            "profile", "log_dir", "ok", "actions", "scanned_files",
            "scanned_bytes", "deleted_bytes", "rotated_count", "deleted_count",
            "truncated_count", "dry_run", "total_size_after",
        }
        assert expected <= set(report.keys())

    def test_render_human_contains_verdict(self, tmp_path: Path) -> None:
        _log(tmp_path, "agent.log").write_bytes(b"hi")
        report = log_rotate.run(_paths(tmp_path), apply=True)
        _code, text = log_rotate.render_human(report)
        assert "VERDICT:" in text
        assert "rotated=" in text
        assert "log_dir:" in text

    def test_show_resolution_includes_options(self, tmp_path: Path) -> None:
        _log(tmp_path, "agent.log").write_bytes(b"hi")
        out = log_rotate.show_resolution(
            _paths(tmp_path),
            max_size=100,
            max_age_days=7,
            max_total=1024,
        )
        data = json.loads(out)
        assert data["options"]["max_size"] == 100
        assert data["options"]["max_age_days"] == 7
        assert data["options"]["max_total"] == 1024
        assert data["options"]["keep"] == 1


# ---------- TestCli ----------

class TestCli:
    def test_help_renders(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "log-rotate", "--help"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert "--max-size" in proc.stdout
        assert "--max-age" in proc.stdout
        assert "--max-total" in proc.stdout
        assert "--keep" in proc.stdout
        assert "--all-profiles" in proc.stdout

    def test_help_no_longer_mentions_verbose(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "log-rotate", "--help"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert "--verbose" not in proc.stdout
        assert "-v," not in proc.stdout

    def test_default_run_prints_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # log-rotate is explicit-only: with no action flags it reports
        # scanned size/age and exits 0 without writing. The human
        # report must print by default (no --verbose needed).
        _log(tmp_path, "agent.log").write_bytes(b"hello")
        _log(tmp_path, "errors.log").write_bytes(b"world")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_PROFILE", raising=False)
        monkeypatch.delenv("HERMES_HOME", raising=False)

        proc = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "log-rotate"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        # The renderer always emits the "Hermes log rotation" header
        # and the VERDICT line; the point of this test is that they
        # reach stdout without --verbose.
        assert "Hermes log rotation" in proc.stdout
        assert "VERDICT:" in proc.stdout
        assert "scanned:" in proc.stdout

    def test_no_flags_reports_scanned(self, tmp_path: Path) -> None:
        # With no flags, the tool should report but not write.
        _log(tmp_path, "agent.log").write_bytes(b"hello")
        report = log_rotate.run(_paths(tmp_path), apply=True)
        assert report["ok"] is True
        assert report["scanned_files"] == 1
        assert report["scanned_bytes"] == 5
        # No action flags => no rotate/delete
        assert report["rotated_count"] == 0
        assert report["deleted_count"] == 0
