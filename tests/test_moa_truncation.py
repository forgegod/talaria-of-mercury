"""Tests for talaria.hermos.moa_truncation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from talaria.hermos import moa_truncation
from talaria.paths import ResolvedPaths
from tests._helpers import make_sessions_db


# Helper: produce a fake agent.log line with explicit severity.
def _log_line(level: str, body: str, when: datetime) -> str:
    ts = when.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    return f"{ts} {level} agent.chat_completion_helpers: {body}\n"


class TestSignalA:
    def test_missing_state_db_reports_error(self, tmp_path: Path) -> None:
        result = moa_truncation.signal_a_output_tokens(tmp_path / "no.db", 0.0)
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_returns_top_sessions_descending(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = datetime.now(timezone.utc)
        rows = [
            (1, "model-a", 10_000, 5, 5, now.timestamp()),
            (2, "model-b", 50_000, 12, 12, now.timestamp()),
            (3, "model-c", 200_000, 30, 30, now.timestamp()),  # flagged
            (4, "model-d", 70_000, 15, 15, now.timestamp()),   # flagged
        ]
        make_sessions_db(db, rows)
        result = moa_truncation.signal_a_output_tokens(db, (now - timedelta(days=1)).timestamp())
        assert result["ok"] is True
        assert result["window_sessions"] == 4
        # DESC by output_tokens
        assert [s["output_tokens"] for s in result["sessions"]] == [200_000, 70_000, 50_000, 10_000]
        # Flagged above 64k
        assert {s["id"] for s in result["flagged"]} == {3, 4}

    def test_filters_to_window(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=30)).timestamp()
        recent = now.timestamp()
        rows = [
            (1, "old", 200_000, 1, 1, old),         # excluded by window
            (2, "new", 200_000, 1, 1, recent),      # included, flagged
        ]
        make_sessions_db(db, rows)
        result = moa_truncation.signal_a_output_tokens(db, (now - timedelta(days=1)).timestamp())
        assert [s["id"] for s in result["sessions"]] == [2]


class TestSignalB:
    def test_no_log_files_ok(self, tmp_path: Path) -> None:
        result = moa_truncation.signal_b_log_truncations(tmp_path, 0.0)
        assert result["ok"] is True
        assert result["length_class_hits"] == 0
        assert result["stream_drop_warnings"] == 0

    def test_warning_level_triggers_length_hit(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        now = datetime.now(timezone.utc)
        body = "stream ended: finish_reason='length' output=12345"
        (log_dir / "agent.log").write_text(_log_line("WARNING", body, now))

        result = moa_truncation.signal_b_log_truncations(log_dir, (now - timedelta(hours=1)).timestamp())
        assert result["length_class_hits"] == 1
        assert result["matches"][0]["file"] == "agent.log"
        assert "finish_reason='length'" in result["matches"][0]["line"]

    def test_info_level_does_not_trigger(self, tmp_path: Path) -> None:
        """INFO echoes of user messages must not fire Signal B."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        now = datetime.now(timezone.utc)
        # Same substring as the warning above, but logged at INFO.
        body = "user said: 'Analyse this... Response truncated (finish_reason='length')...'"
        (log_dir / "agent.log").write_text(_log_line("INFO", body, now))

        result = moa_truncation.signal_b_log_truncations(log_dir, (now - timedelta(hours=1)).timestamp())
        assert result["length_class_hits"] == 0

    def test_stream_drop_counted_separately(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        now = datetime.now(timezone.utc)
        body = "Stream ended with no finish_reason while a tool call's arguments were still incomplete"
        (log_dir / "agent.log").write_text(_log_line("WARNING", body, now))

        result = moa_truncation.signal_b_log_truncations(log_dir, (now - timedelta(hours=1)).timestamp())
        assert result["length_class_hits"] == 0
        assert result["stream_drop_warnings"] == 1

    def test_old_lines_filtered_by_window(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        old = datetime.now(timezone.utc) - timedelta(days=30)
        (log_dir / "agent.log").write_text(_log_line(
            "ERROR", "finish_reason='length' (old)", old,
        ))
        result = moa_truncation.signal_b_log_truncations(log_dir, (old + timedelta(days=1)).timestamp())
        assert result["length_class_hits"] == 0

    def test_double_quoted_finish_reason(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        now = datetime.now(timezone.utc)
        (log_dir / "errors.log").write_text(_log_line(
            "ERROR", 'finish_reason="length" output=9999', now,
        ))
        result = moa_truncation.signal_b_log_truncations(log_dir, (now - timedelta(hours=1)).timestamp())
        assert result["length_class_hits"] == 1


class TestRun:
    def _paths(self, tmp_path: Path, *, state_db: Path, log_dir: Path) -> ResolvedPaths:
        return ResolvedPaths(
            profile="test",
            hermes_root=tmp_path,
            state_db=state_db,
            log_dir=log_dir,
        )

    def test_clean_run(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = datetime.now(timezone.utc)
        make_sessions_db(db, [
            (1, "model-a", 5_000, 3, 3, now.timestamp()),
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        report = moa_truncation.run(self._paths(tmp_path, state_db=db, log_dir=log_dir))
        assert report["fired"] is False
        assert report["signal_a_output_tokens"]["ok"] is True
        assert report["signal_b_log_truncations"]["length_class_hits"] == 0

    def test_flagged_session_sets_fired(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = datetime.now(timezone.utc)
        make_sessions_db(db, [
            (1, "model-b", 200_000, 30, 30, now.timestamp()),
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        report = moa_truncation.run(self._paths(tmp_path, state_db=db, log_dir=log_dir))
        assert report["fired"] is True

    def test_length_hit_in_logs_sets_fired(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        now = datetime.now(timezone.utc)
        (log_dir / "agent.log").write_text(_log_line(
            "ERROR", "finish_reason='length'", now,
        ))
        report = moa_truncation.run(self._paths(tmp_path, state_db=db, log_dir=log_dir))
        assert report["fired"] is True


class TestRenderer:
    def test_human_clean(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = datetime.now(timezone.utc)
        make_sessions_db(db, [(1, "model-a", 5_000, 3, 3, now.timestamp())])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        paths = ResolvedPaths(profile="test", hermes_root=tmp_path, state_db=db, log_dir=log_dir)
        report = moa_truncation.run(paths)
        code, text = moa_truncation.render_human(report)
        assert code == 0
        assert "VERDICT: clean" in text
        assert "Signal A" in text
        assert "Signal B" in text

    def test_human_flagged_exits_1(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = datetime.now(timezone.utc)
        make_sessions_db(db, [(1, "model-x", 200_000, 30, 30, now.timestamp())])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        paths = ResolvedPaths(profile="test", hermes_root=tmp_path, state_db=db, log_dir=log_dir)
        report = moa_truncation.run(paths)
        code, text = moa_truncation.render_human(report)
        assert code == 1
        assert "VERDICT" in text
        assert "fired" in text.lower()


class TestCli:
    """End-to-end CLI tests via subprocess — proves the entry point works."""

    def test_help_exits_zero(self) -> None:
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Hermes" in result.stdout

    def test_version_flag(self) -> None:
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "--version"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "talaria" in result.stdout.lower()

    def test_moa_truncation_runs_against_fake_paths(self, tmp_path: Path) -> None:
        import subprocess
        import sys
        db = tmp_path / "state.db"
        now = datetime.now(timezone.utc)
        make_sessions_db(db, [(1, "model-a", 5_000, 3, 3, now.timestamp())])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli",
             "hermes", "moa-truncation",
             "--state-db", str(db),
             "--log-dir", str(log_dir),
             "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["fired"] is False
        assert payload["signal_a_output_tokens"]["window_sessions"] == 1

    def test_moa_truncation_flagged_session_exits_1(self, tmp_path: Path) -> None:
        import subprocess
        import sys
        db = tmp_path / "state.db"
        now = datetime.now(timezone.utc)
        make_sessions_db(db, [(1, "model-b", 200_000, 30, 30, now.timestamp())])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli",
             "hermes", "moa-truncation",
             "--state-db", str(db),
             "--log-dir", str(log_dir),
             "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert payload["fired"] is True
        assert len(payload["signal_a_output_tokens"]["flagged"]) == 1