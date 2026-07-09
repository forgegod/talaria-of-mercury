"""Tests for talaria.hermos.doctor, doctor_llm, and doctor_free_flight.

The canonical fixture lives in :mod:`tests._helpers.make_full_state_db`,
which mirrors the production Hermes sessions / messages / compression_locks
schema. Each test class builds a focused scenario; the orchestrator
tests pass a stub ``free_flight_runner`` to avoid any subprocess calls.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from talaria.hermos import doctor, doctor_free_flight, doctor_llm
from talaria.hermos.doctor import DetectorResult
from talaria.paths import ResolvedPaths
from talaria.sync.yaml_io import dump_yaml
from tests._helpers import make_full_state_db


def _paths(tmp_path: Path, *, state_db: Path, log_dir: Path) -> ResolvedPaths:
    return ResolvedPaths(
        profile="test",
        hermes_root=tmp_path,
        state_db=state_db,
        log_dir=log_dir,
    )


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _log_line(level: str, body: str, when: datetime) -> str:
    ts = when.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    return f"{ts} {level} agent.chat_completion_helpers: {body}\n"


# ---------------- Per-detector tests ----------------

class TestTruncationOutput:
    def test_no_sessions_is_info(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = doctor.detector_truncation_output(
                con, (_now() - 86400), doctor.OUTPUT_TOKEN_ALERT,
            )
        finally:
            con.close()
        assert r.id == doctor.TRUNCATION_OUTPUT
        assert r.severity == doctor.SEVERITY_INFO
        assert r.fired is False

    def test_high_output_session_fires(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "s1", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now, "output_tokens": 200_000, "message_count": 100,
             "api_call_count": 100, "rewind_count": 0, "archived": 0,
             "estimated_cost_usd": 1.0, "actual_cost_usd": 1.0,
             "cost_status": "ok", "end_reason": "cli_close", "ended_at": now + 60},
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = doctor.detector_truncation_output(
                con, now - 86400, doctor.OUTPUT_TOKEN_ALERT,
            )
        finally:
            con.close()
        assert r.severity == doctor.SEVERITY_ALERT
        assert r.fired is True
        assert any(s["id"] == "s1" for s in r.evidence["flagged"])

    def test_borderline_band_marks_borderline(self, tmp_path: Path) -> None:
        """Sessions at 0.75x–1.0x of the threshold are borderline but not flagged."""
        db = tmp_path / "state.db"
        now = _now()
        threshold = doctor.OUTPUT_TOKEN_ALERT
        make_full_state_db(db, sessions=[
            {"id": "borderline", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now, "output_tokens": int(threshold * 0.8),
             "rewind_count": 0, "archived": 0, "message_count": 1, "api_call_count": 1},
            {"id": "below_band", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now, "output_tokens": int(threshold * 0.5),
             "rewind_count": 0, "archived": 0, "message_count": 1, "api_call_count": 1},
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = doctor.detector_truncation_output(con, now - 86400, threshold)
        finally:
            con.close()
        # Both sessions present; only the borderline one is in the
        # borderline_band, but no session is flagged, so fired is False.
        # Because fired is False, the borderline flag must also be False
        # (the orchestrator only escalates borderline if fired=True).
        assert r.fired is False
        assert r.borderline is False
        assert any(s["id"] == "borderline" for s in r.evidence["borderline_band"])


class TestCompressionStaleLocks:
    def test_no_locks_is_info(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = doctor.detector_compression_stale_locks(con, _now())
        finally:
            con.close()
        assert r.severity == doctor.SEVERITY_INFO
        assert r.evidence["locks"] == []

    def test_expired_lock_fires(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(
            db,
            compression_locks=[
                {"session_id": "s1", "holder": "compressor", "acquired_at": now - 7200,
                 "expires_at": now - 3600},  # expired 1h ago
            ],
        )
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = doctor.detector_compression_stale_locks(con, now)
        finally:
            con.close()
        assert r.fired is True
        assert r.severity == doctor.SEVERITY_ALERT
        assert r.evidence["locks"][0]["session_id"] == "s1"


class TestCompressionFailures:
    def test_failure_session_fires(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "fail1", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now - 100, "compression_failure_error": "lock timeout",
             "rewind_count": 0, "archived": 0, "message_count": 0, "api_call_count": 0,
             "output_tokens": 0},
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = doctor.detector_compression_failures(con, now - 86400)
        finally:
            con.close()
        assert r.fired is True
        assert r.evidence["sessions"][0]["compression_failure_error"] == "lock timeout"


class TestZombieSessions:
    def test_zombie_with_ended_at_null_fires(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        # 25 hours ago: older than the 24h zombie threshold.
        make_full_state_db(db, sessions=[
            {"id": "zombie", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now - 25 * 3600, "ended_at": None,
             "rewind_count": 0, "archived": 0, "message_count": 1, "api_call_count": 0,
             "output_tokens": 0},
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = doctor.detector_zombie_sessions(con, now)
        finally:
            con.close()
        assert r.fired is True
        assert r.evidence["sessions"][0]["id"] == "zombie"

    def test_recent_ended_at_null_not_a_zombie(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "young", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now - 60, "ended_at": None,
             "rewind_count": 0, "archived": 0, "message_count": 0, "api_call_count": 0,
             "output_tokens": 0},
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = doctor.detector_zombie_sessions(con, now)
        finally:
            con.close()
        assert r.fired is False


class TestGhostSessions:
    def test_session_with_no_messages_fires(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(
            db,
            sessions=[
                {"id": "ghost", "source": "cli", "model": "minimax/minimax-m3",
                 "started_at": now - 100, "message_count": 0,
                 "rewind_count": 0, "archived": 0, "api_call_count": 0,
                 "output_tokens": 0},
            ],
        )
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = doctor.detector_ghost_sessions(con, now - 86400)
        finally:
            con.close()
        assert r.fired is True
        assert r.severity == doctor.SEVERITY_WARN
        assert r.evidence["sessions"][0]["id"] == "ghost"


class TestRewinds:
    def test_high_rewind_count_fires(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "rw1", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now - 100, "rewind_count": 5,
             "message_count": 0, "api_call_count": 0, "output_tokens": 0,
             "archived": 0},
            {"id": "rw2", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now - 100, "rewind_count": 2,  # borderline
             "message_count": 0, "api_call_count": 0, "output_tokens": 0,
             "archived": 0},
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = doctor.detector_rewinds(con, now - 86400)
        finally:
            con.close()
        assert r.fired is True
        assert r.severity == doctor.SEVERITY_WARN
        assert r.borderline is True  # at least one session in the 2–3 band


class TestCostAnomalies:
    def test_divergence_fires(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "cost1", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now - 100, "estimated_cost_usd": 1.0,
             "actual_cost_usd": 0.5, "cost_status": "ok",
             "rewind_count": 0, "archived": 0, "message_count": 0,
             "api_call_count": 0, "output_tokens": 0},
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = doctor.detector_cost_anomalies(con, now - 86400)
        finally:
            con.close()
        assert r.fired is True
        assert r.evidence["alert_sessions"][0]["id"] == "cost1"

    def test_borderline_only(self, tmp_path: Path) -> None:
        """Divergence in the 5–25% band → warn, fired=False, borderline=True."""
        db = tmp_path / "state.db"
        now = _now()
        # 10% divergence — between borderline and alert.
        make_full_state_db(db, sessions=[
            {"id": "c1", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now - 100, "estimated_cost_usd": 1.0,
             "actual_cost_usd": 0.9, "cost_status": "ok",
             "rewind_count": 0, "archived": 0, "message_count": 0,
             "api_call_count": 0, "output_tokens": 0},
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = doctor.detector_cost_anomalies(con, now - 86400)
        finally:
            con.close()
        assert r.severity == doctor.SEVERITY_WARN
        assert r.fired is False  # 10% is not >= 25%
        assert r.borderline is True

    def test_bad_cost_status_fires(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "cs", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now - 100, "estimated_cost_usd": 1.0,
             "actual_cost_usd": 1.0, "cost_status": "denied",
             "rewind_count": 0, "archived": 0, "message_count": 0,
             "api_call_count": 0, "output_tokens": 0},
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = doctor.detector_cost_anomalies(con, now - 86400)
        finally:
            con.close()
        assert r.fired is True


# ---------------- Orchestrator tests ----------------

class TestOrchestrator:
    def test_run_with_clean_state_returns_no_fire(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        paths = _paths(tmp_path, state_db=db, log_dir=log_dir)
        report = doctor.run(paths, days=2, free_flight=False)
        assert report["fired"] is False
        assert len(report["per_detector"]) == len(doctor.DETECTOR_IDS)

    def test_run_with_zombie_fires(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "z", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now - 25 * 3600, "ended_at": None,
             "rewind_count": 0, "archived": 0, "message_count": 0,
             "api_call_count": 0, "output_tokens": 0},
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        paths = _paths(tmp_path, state_db=db, log_dir=log_dir)
        report = doctor.run(paths, days=2, free_flight=False)
        assert report["fired"] is True
        by_id = {d["id"]: d for d in report["per_detector"]}
        assert by_id["zombie_sessions"]["fired"] is True

    def test_run_only_runs_selected_detectors(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        paths = _paths(tmp_path, state_db=db, log_dir=log_dir)
        report = doctor.run(
            paths, days=2,
            only=(doctor.ZOMBIE_SESSIONS,),
            free_flight=False,
        )
        assert report["selected_detectors"] == [doctor.ZOMBIE_SESSIONS]
        assert len(report["per_detector"]) == 1

    def test_run_skip_excludes_detectors(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        paths = _paths(tmp_path, state_db=db, log_dir=log_dir)
        report = doctor.run(
            paths, days=2,
            skip=(doctor.ZOMBIE_SESSIONS, doctor.GHOST_SESSIONS),
        )
        assert doctor.ZOMBIE_SESSIONS not in report["selected_detectors"]
        assert doctor.GHOST_SESSIONS not in report["selected_detectors"]
        assert doctor.ZOMBIE_SESSIONS in report["skipped_detectors"]
        assert doctor.GHOST_SESSIONS in report["skipped_detectors"]

    def test_run_unknown_only_raises(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        paths = _paths(tmp_path, state_db=db, log_dir=log_dir)
        with pytest.raises(ValueError, match="unknown detector"):
            doctor.run(paths, days=2, only=("nonsense",))

    def test_run_one_detector_error_does_not_break_others(self, tmp_path: Path) -> None:
        """A single detector crashing must not prevent the others from running."""
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        # A minimal config.yaml so the free-flight pass has data to
        # assemble and actually reaches the runner call. Without it
        # the pass returns free_flight:no_data before invoking the runner.
        config = tmp_path / "profiles" / "test" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("model:\n  default: gpt-5\n")
        paths = _paths(tmp_path, state_db=db, log_dir=log_dir)

        def boom_runner(prompt, *, timeout, **kw):
            raise RuntimeError("free-flight runner unavailable")

        # With a clean state, no deterministic detector fires. The
        # free-flight pass is the only place the runner is called; a
        # runner exception must degrade to a free_flight:error
        # result, not crash the `talaria hermes doctor`.
        report = doctor.run(
            paths, days=2,
            free_flight_runner=boom_runner,
        )
        assert report["fired"] is False
        assert report["detector_errors"] == {}
        # The free-flight block surfaces the error in the per_detector
        # list as a free_flight:error result (severity=info, fired=False).
        ff_errors = [d for d in report["per_detector"] if d["id"] == "free_flight:error"]
        assert len(ff_errors) == 1
        assert "free-flight runner unavailable" in ff_errors[0]["summary"]

    def test_missing_state_db_does_not_crash(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        paths = _paths(
            tmp_path, state_db=tmp_path / "nope.db", log_dir=log_dir,
        )
        report = doctor.run(paths, days=2, free_flight=False)
        # The state.db-backed detectors surface an "info" result
        # noting the missing DB; the log-backed detectors run normally.
        by_id = {d["id"]: d for d in report["per_detector"]}
        assert by_id[doctor.TRUNCATION_OUTPUT]["severity"] == doctor.SEVERITY_INFO


# ---------------- Renderer tests ----------------

class TestRenderer:
    def test_human_clean(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        paths = _paths(tmp_path, state_db=db, log_dir=log_dir)
        report = doctor.run(paths, days=2, free_flight=False)
        code, text = doctor.render_human(report)
        assert code == 0
        assert "VERDICT: clean" in text
        assert "Signal A" not in text  # no legacy phrasing
        for det in doctor.DETECTOR_IDS:
            assert det in text

    def test_human_fired(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "zombie", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now - 25 * 3600, "ended_at": None,
             "rewind_count": 0, "archived": 0, "message_count": 0,
             "api_call_count": 0, "output_tokens": 0},
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        paths = _paths(tmp_path, state_db=db, log_dir=log_dir)
        report = doctor.run(paths, days=2, free_flight=False)
        code, text = doctor.render_human(report)
        assert code == 1
        assert "VERDICT" in text
        assert "fired" in text.lower()
        assert "zombie_sessions" in text


# ---------------- Apply config_suggestion tests ----------------
class TestApplySuggestions:
    """Tests for doctor.apply_config_suggestions() — the self-heal path."""

    def _paths(self, tmp_path, *, config_path):
        return ResolvedPaths(
            profile="test", hermes_root=tmp_path,
            state_db=tmp_path / "s.db", log_dir=tmp_path / "l",
        )

    def _suggestion(
        self, *, slug, yaml_path, suggested,
        current="unknown", severity="info",
    ):
        return DetectorResult(
            id=f"free_flight:config:{slug}",
            severity=severity,
            summary=f"suggestion {slug}",
            evidence={
                "kind": "config_suggestion",
                "yaml_path": yaml_path,
                "current_value": current,
                "suggested_value": suggested,
                "rationale": "test",
            },
        )

    def test_dry_run_does_not_write(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "model:\n"
            "  default: gpt-5\n"
            "moa:\n"
            "  presets:\n"
            "    coding:\n"
            "      max_tokens: 32768\n"
        )
        original = cfg.read_text()
        paths = self._paths(tmp_path, config_path=cfg)
        sug = self._suggestion(
            slug="lower", yaml_path="moa.presets.coding.max_tokens",
            suggested="16384", current="32768",
        )
        rep = doctor.apply_config_suggestions(paths, [sug], dry_run=True, config_path=cfg)
        assert rep["ok"] is True
        assert rep["dry_run"] is True
        assert len(rep["applied"]) == 1
        assert cfg.read_text() == original  # unchanged
        assert "max_tokens: 16384" in rep["dry_run_diff"]
        assert "max_tokens: 32768" in rep["dry_run_diff"]

    def test_apply_writes_backup_and_new_file(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "model:\n"
            "  default: gpt-5\n"
            "moa:\n"
            "  presets:\n"
            "    coding:\n"
            "      max_tokens: 32768\n"
        )
        paths = self._paths(tmp_path, config_path=cfg)
        sug = self._suggestion(
            slug="lower", yaml_path="moa.presets.coding.max_tokens",
            suggested="16384", current="32768",
        )
        rep = doctor.apply_config_suggestions(paths, [sug], dry_run=False, config_path=cfg)
        assert rep["ok"] is True
        assert rep["backup"] is not None
        bak = Path(rep["backup"])
        assert bak.exists()
        assert "max_tokens: 32768" in bak.read_text()  # backup is the original
        new = cfg.read_text()
        assert "max_tokens: 16384" in new
        assert "max_tokens: 32768" not in new

    def test_apply_creates_missing_parent_block(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "model:\n"
            "  default: gpt-5\n"
        )
        paths = self._paths(tmp_path, config_path=cfg)
        sug = self._suggestion(
            slug="add", yaml_path="moa.presets.coding.max_tokens",
            suggested="16384", current="unknown",
        )
        rep = doctor.apply_config_suggestions(paths, [sug], dry_run=False, config_path=cfg)
        assert rep["ok"] is True
        new = cfg.read_text()
        assert "moa:" in new
        assert "presets:" in new
        assert "coding:" in new
        assert "max_tokens: 16384" in new

    def test_apply_coerces_value_types(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "a: 1\n"
            "b: 1.0\n"
            "c: true\n"
            "d: hello\n"
        )
        paths = self._paths(tmp_path, config_path=cfg)
        sugs = [
            self._suggestion(slug="ai", yaml_path="a", suggested="42"),
            self._suggestion(slug="af", yaml_path="b", suggested="3.14"),
            self._suggestion(slug="ab", yaml_path="c", suggested="false"),
            self._suggestion(slug="as", yaml_path="d", suggested="world"),
        ]
        rep = doctor.apply_config_suggestions(paths, sugs, dry_run=False, config_path=cfg)
        assert rep["ok"] is True
        new = cfg.read_text()
        assert "a: 42\n" in new
        assert "b: 3.14\n" in new
        assert "c: false\n" in new
        assert "d: world\n" in new

    def test_empty_yaml_path_is_skipped(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("a: 1\n")
        paths = self._paths(tmp_path, config_path=cfg)
        sug = self._suggestion(
            slug="empty", yaml_path="", suggested="x", current="1",
        )
        rep = doctor.apply_config_suggestions(paths, [sug], dry_run=False, config_path=cfg)
        assert rep["ok"] is True
        assert rep["applied"] == []
        assert any(s.get("reason") == "empty yaml_path" for s in rep["skipped"])
        assert cfg.read_text() == "a: 1\n"

    def test_apply_no_suggestions_is_noop(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("a: 1\n")
        paths = self._paths(tmp_path, config_path=cfg)
        rep = doctor.apply_config_suggestions(paths, [], dry_run=False, config_path=cfg)
        assert rep["ok"] is True
        assert rep["applied"] == []
        assert rep["backup"] is None
        assert cfg.read_text() == "a: 1\n"

    def test_apply_missing_config_creates_file(self, tmp_path):
        cfg = tmp_path / "config.yaml"  # not written
        paths = self._paths(tmp_path, config_path=cfg)
        sug = self._suggestion(
            slug="add", yaml_path="moa.presets.coding.max_tokens",
            suggested="16384",
        )
        rep = doctor.apply_config_suggestions(paths, [sug], dry_run=False, config_path=cfg)
        assert rep["ok"] is True
        assert cfg.exists()
        assert "max_tokens: 16384" in cfg.read_text()


# ---------------- Tactical-action tests ----------------
class TestTacticalActions:
    """Tests for ``doctor.apply_tactical_actions`` and its per-class helpers.

    Each test seeds a focused state.db via ``make_full_state_db``,
    invokes the apply path, then re-reads the DB to verify the actual
    write. Dry-run previews must NOT change the DB; apply runs must.
    """

    def _paths(self, tmp_path: Path, *, state_db: Path) -> ResolvedPaths:
        return ResolvedPaths(
            profile="test", hermes_root=tmp_path,
            state_db=state_db, log_dir=tmp_path / "logs",
        )

    def _seed_stale_lock(self, db: Path) -> None:
        """Two sessions, each with one lock: one expired, one live.

        ``compression_locks.session_id`` is PRIMARY KEY, so there is at
        most one lock per session. The action must therefore operate on
        per-session rows, not (session_id, holder, expires_at) tuples.

        Both sessions receive a message row so they're not classified
        as ghosts by the unrelated prune_ghost_sessions action — that
        keeps the combined-flag test focused on the lock pruning.
        """
        now = _now()
        make_full_state_db(
            db,
            sessions=[
                {"id": "stale_s", "source": "cli",
                 "model": "minimax/minimax-m3",
                 "started_at": now - 3600, "ended_at": now - 60,
                 "output_tokens": 100, "message_count": 5,
                 "api_call_count": 5, "rewind_count": 0, "archived": 0,
                 "estimated_cost_usd": 0.01, "actual_cost_usd": 0.01,
                 "cost_status": "ok", "end_reason": "cli_close"},
                {"id": "live_s", "source": "cli",
                 "model": "minimax/minimax-m3",
                 "started_at": now - 60, "ended_at": now - 30,
                 "output_tokens": 50, "message_count": 2,
                 "api_call_count": 2, "rewind_count": 0, "archived": 0,
                 "estimated_cost_usd": 0.005, "actual_cost_usd": 0.005,
                 "cost_status": "ok", "end_reason": "cli_close"},
            ],
            messages=[
                {"session_id": "stale_s", "role": "user", "content": "hi",
                 "timestamp": now - 3600, "finish_reason": "stop"},
                {"session_id": "live_s", "role": "user", "content": "hi",
                 "timestamp": now - 60, "finish_reason": "stop"},
            ],
            compression_locks=[
                # Expired: well in the past
                {"session_id": "stale_s", "holder": "compressor-1",
                 "acquired_at": now - 7200, "expires_at": now - 3600},
                # Live: expires in the future
                {"session_id": "live_s", "holder": "compressor-2",
                 "acquired_at": now - 60, "expires_at": now + 3600},
            ],
        )

    def test_prune_stale_locks_dry_run_does_not_write(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        self._seed_stale_lock(db)
        paths = self._paths(tmp_path, state_db=db)
        rep = doctor.apply_tactical_actions(
            paths, prune_stale_locks=True, apply=False,
            state_db_override=db,
        )
        entry = rep[doctor.TACTICAL_PRUNE_STALE_LOCKS]
        assert entry["ok"] is True
        assert entry["dry_run"] is True
        assert len(entry["would_modify"]) == 1
        assert entry["would_modify"][0]["session_id"] == "stale_s"
        assert entry["would_modify"][0]["holder"] == "compressor-1"
        assert entry["applied"] == []
        # DB unchanged: both locks still present
        con = sqlite3.connect(db)
        try:
            rows = con.execute("SELECT COUNT(*) FROM compression_locks").fetchone()
            assert rows[0] == 2
        finally:
            con.close()

    def test_prune_stale_locks_apply_writes(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        self._seed_stale_lock(db)
        paths = self._paths(tmp_path, state_db=db)
        rep = doctor.apply_tactical_actions(
            paths, prune_stale_locks=True, apply=True,
            state_db_override=db,
        )
        entry = rep[doctor.TACTICAL_PRUNE_STALE_LOCKS]
        assert entry["ok"] is True
        assert entry["dry_run"] is False
        assert len(entry["applied"]) == 1
        # Only the live lock remains
        con = sqlite3.connect(db)
        try:
            rows = con.execute(
                "SELECT holder FROM compression_locks"
            ).fetchall()
            holders = [r[0] for r in rows]
            assert holders == ["compressor-2"]
        finally:
            con.close()

    def test_prune_stale_locks_is_idempotent(self, tmp_path: Path) -> None:
        """Second apply must be a no-op (the expired lock is already gone)."""
        db = tmp_path / "state.db"
        self._seed_stale_lock(db)
        paths = self._paths(tmp_path, state_db=db)
        doctor.apply_tactical_actions(
            paths, prune_stale_locks=True, apply=True,
            state_db_override=db,
        )
        rep = doctor.apply_tactical_actions(
            paths, prune_stale_locks=True, apply=True,
            state_db_override=db,
        )
        entry = rep[doctor.TACTICAL_PRUNE_STALE_LOCKS]
        assert entry["ok"] is True
        assert entry["applied"] == []
        assert entry["would_modify"] == []

    def _seed_zombie(self, db: Path) -> None:
        now = _now()
        make_full_state_db(
            db,
            sessions=[
                # Zombie: started >24h ago, never ended
                {"id": "z1", "source": "cli", "model": "minimax/minimax-m3",
                 "started_at": now - 86400 * 3, "ended_at": None,
                 "output_tokens": 0, "message_count": 0,
                 "api_call_count": 0, "rewind_count": 0, "archived": 0,
                 "estimated_cost_usd": 0, "actual_cost_usd": 0,
                 "cost_status": "ok", "end_reason": "cli_close"},
                # Healthy: started recently and ended
                {"id": "h1", "source": "cli", "model": "minimax/minimax-m3",
                 "started_at": now - 60, "ended_at": now,
                 "output_tokens": 10, "message_count": 2,
                 "api_call_count": 2, "rewind_count": 0, "archived": 0,
                 "estimated_cost_usd": 0.001, "actual_cost_usd": 0.001,
                 "cost_status": "ok", "end_reason": "cli_close"},
            ],
        )

    def test_close_zombies_dry_run_preserves_session(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        self._seed_zombie(db)
        paths = self._paths(tmp_path, state_db=db)
        rep = doctor.apply_tactical_actions(
            paths, close_zombies=True, apply=False,
            state_db_override=db,
        )
        entry = rep[doctor.TACTICAL_CLOSE_ZOMBIES]
        assert entry["ok"] is True
        assert entry["dry_run"] is True
        assert len(entry["would_modify"]) == 1
        assert entry["would_modify"][0]["id"] == "z1"
        # Row preserved with ended_at still NULL
        con = sqlite3.connect(db)
        try:
            row = con.execute(
                "SELECT ended_at FROM sessions WHERE id = 'z1'"
            ).fetchone()
            assert row[0] is None
        finally:
            con.close()

    def test_close_zombies_apply_sets_ended_at(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        self._seed_zombie(db)
        paths = self._paths(tmp_path, state_db=db)
        rep = doctor.apply_tactical_actions(
            paths, close_zombies=True, apply=True,
            state_db_override=db,
        )
        entry = rep[doctor.TACTICAL_CLOSE_ZOMBIES]
        assert entry["ok"] is True
        assert entry["dry_run"] is False
        assert len(entry["applied"]) == 1
        assert entry["applied"][0]["id"] == "z1"
        con = sqlite3.connect(db)
        try:
            row = con.execute(
                "SELECT ended_at FROM sessions WHERE id = 'z1'"
            ).fetchone()
            assert row[0] is not None
        finally:
            con.close()

    def _seed_ghost(self, db: Path) -> None:
        now = _now()
        make_full_state_db(
            db,
            sessions=[
                # Ghost: started, no messages, within window
                {"id": "g1", "source": "cli", "model": "minimax/minimax-m3",
                 "started_at": now - 3600, "ended_at": None,
                 "output_tokens": 0, "message_count": 0,
                 "api_call_count": 0, "rewind_count": 0, "archived": 0,
                 "estimated_cost_usd": 0, "actual_cost_usd": 0,
                 "cost_status": "ok", "end_reason": "cli_close"},
                # Healthy: has messages
                {"id": "ok1", "source": "cli", "model": "minimax/minimax-m3",
                 "started_at": now - 60, "ended_at": now,
                 "output_tokens": 100, "message_count": 2,
                 "api_call_count": 2, "rewind_count": 0, "archived": 0,
                 "estimated_cost_usd": 0.001, "actual_cost_usd": 0.001,
                 "cost_status": "ok", "end_reason": "cli_close"},
            ],
            messages=[
                {"session_id": "ok1", "role": "user", "content": "hi",
                 "timestamp": now - 60, "finish_reason": "stop"},
                {"session_id": "ok1", "role": "assistant",
                 "content": "hello", "timestamp": now - 30,
                 "finish_reason": "stop"},
            ],
        )

    def test_prune_ghost_sessions_apply_deletes_row(
        self, tmp_path: Path,
    ) -> None:
        db = tmp_path / "state.db"
        self._seed_ghost(db)
        paths = self._paths(tmp_path, state_db=db)
        rep = doctor.apply_tactical_actions(
            paths, prune_ghost_sessions=True, apply=True,
            days=1, since=None,
            state_db_override=db,
        )
        entry = rep[doctor.TACTICAL_PRUNE_GHOST_SESSIONS]
        assert entry["ok"] is True
        assert entry["dry_run"] is False
        assert len(entry["applied"]) == 1
        assert entry["applied"][0]["id"] == "g1"
        # Ghost gone; healthy session preserved
        con = sqlite3.connect(db)
        try:
            rows = con.execute(
                "SELECT id FROM sessions ORDER BY id"
            ).fetchall()
            assert [r[0] for r in rows] == ["ok1"]
        finally:
            con.close()

    def test_prune_ghost_sessions_preserves_out_of_window(
        self, tmp_path: Path,
    ) -> None:
        """A ghost older than the window must NOT be auto-deleted."""
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(
            db,
            sessions=[
                {"id": "old_ghost", "source": "cli",
                 "model": "minimax/minimax-m3",
                 "started_at": now - 86400 * 30, "ended_at": None,
                 "output_tokens": 0, "message_count": 0,
                 "api_call_count": 0, "rewind_count": 0, "archived": 0,
                 "estimated_cost_usd": 0, "actual_cost_usd": 0,
                 "cost_status": "ok", "end_reason": "cli_close"},
            ],
        )
        paths = self._paths(tmp_path, state_db=db)
        # 1-day window: 30-day-old ghost is out of scope
        rep = doctor.apply_tactical_actions(
            paths, prune_ghost_sessions=True, apply=True,
            days=1, since=None,
            state_db_override=db,
        )
        entry = rep[doctor.TACTICAL_PRUNE_GHOST_SESSIONS]
        assert entry["ok"] is True
        assert entry["would_modify"] == []
        assert entry["applied"] == []

    def test_no_flags_returns_unselected_placeholders(
        self, tmp_path: Path,
    ) -> None:
        """No tactical flag → every key is ``{"selected": False}``."""
        db = tmp_path / "state.db"
        paths = self._paths(tmp_path, state_db=db)
        rep = doctor.apply_tactical_actions(
            paths, apply=False, state_db_override=db,
        )
        for action_id in doctor.TACTICAL_ACTION_IDS:
            assert rep[action_id] == {"selected": False}

    def test_combined_flags_each_get_full_report(
        self, tmp_path: Path,
    ) -> None:
        """All three flags at once → each gets its own report key."""
        db = tmp_path / "state.db"
        self._seed_stale_lock(db)
        paths = self._paths(tmp_path, state_db=db)
        rep = doctor.apply_tactical_actions(
            paths, prune_stale_locks=True, close_zombies=True,
            prune_ghost_sessions=True, apply=False,
            state_db_override=db,
        )
        assert "ok" in rep[doctor.TACTICAL_PRUNE_STALE_LOCKS]
        assert "ok" in rep[doctor.TACTICAL_CLOSE_ZOMBIES]
        assert "ok" in rep[doctor.TACTICAL_PRUNE_GHOST_SESSIONS]
        # Stale lock was seeded; the other two have nothing to do
        assert len(rep[doctor.TACTICAL_PRUNE_STALE_LOCKS]["would_modify"]) == 1
        assert rep[doctor.TACTICAL_CLOSE_ZOMBIES]["would_modify"] == []
        assert rep[doctor.TACTICAL_PRUNE_GHOST_SESSIONS]["would_modify"] == []

    def test_missing_state_db_reports_error(self, tmp_path: Path) -> None:
        db = tmp_path / "missing.db"
        paths = self._paths(tmp_path, state_db=db)
        rep = doctor.apply_tactical_actions(
            paths, prune_stale_locks=True, apply=True,
            state_db_override=db,
        )
        entry = rep[doctor.TACTICAL_PRUNE_STALE_LOCKS]
        assert entry["ok"] is False
        assert "not found" in entry["error"]

    def test_report_keys_are_stable_across_calls(
        self, tmp_path: Path,
    ) -> None:
        """Iterating ``TACTICAL_ACTION_IDS`` must always yield a key."""
        db = tmp_path / "state.db"
        paths = self._paths(tmp_path, state_db=db)
        rep = doctor.apply_tactical_actions(paths, state_db_override=db)
        assert set(rep.keys()) == set(doctor.TACTICAL_ACTION_IDS)


# ---------------- Remediation-hint tests ----------------
class TestRemediationHints:
    """Tests for the ``per_detector[i].remediation`` field and the
    ``fix: …`` line in the human renderer.

    The contract is: every detector that has a tactical action gets a
    remediation hint string; everything else gets ``None``. The renderer
    only prints the hint under fired findings, so telling the operator
    how to "fix" a clean detector is noise.
    """

    def test_with_remediation_hint_known_detector(self) -> None:
        d = {"id": doctor.ZOMBIE_SESSIONS, "severity": "alert",
             "summary": "x", "evidence": {}, "fired": True,
             "borderline": False, "adjudicated": False,
             "model_verdict": None}
        out = doctor._with_remediation_hint(d)
        assert out["remediation"] == "--close-zombies [--apply]"
        # Original dict not mutated (shallow copy contract)
        assert "remediation" not in d

    def test_with_remediation_hint_unknown_detector_is_none(self) -> None:
        d = {"id": "truncation_output", "severity": "alert",
             "summary": "x", "evidence": {}, "fired": True,
             "borderline": False, "adjudicated": False,
             "model_verdict": None}
        out = doctor._with_remediation_hint(d)
        assert out["remediation"] is None

    def test_with_remediation_hint_free_flight_is_none(self) -> None:
        # Curator findings are deliberately NOT enriched — adding
        # ``remediation`` here would falsely imply tactical flags apply.
        d = {"id": "free_flight:anomaly:weird_thing", "severity": "alert",
             "summary": "x", "evidence": {}, "fired": True,
             "borderline": False, "adjudicated": True,
             "model_verdict": {"verdict": "alert"}}
        out = doctor._with_remediation_hint(d)
        assert out["remediation"] is None

        d = {"id": "free_flight:config:bump_max_tokens", "severity": "info",
             "summary": "x", "evidence": {}, "fired": False,
             "borderline": False, "adjudicated": True,
             "model_verdict": {"verdict": "info"}}
        out = doctor._with_remediation_hint(d)
        assert out["remediation"] is None

    def test_each_tactical_detector_has_a_hint(self) -> None:
        # Sanity: every detector with a tactical action must be in
        # the hint map, so the operator never sees a fired finding
        # without a fix hint.
        for det_id, action_id in (
            (doctor.COMPRESSION_STALE_LOCKS,
             doctor.TACTICAL_PRUNE_STALE_LOCKS),
            (doctor.ZOMBIE_SESSIONS, doctor.TACTICAL_CLOSE_ZOMBIES),
            (doctor.GHOST_SESSIONS, doctor.TACTICAL_PRUNE_GHOST_SESSIONS),
        ):
            assert det_id in doctor._DETECTOR_REMEDIATION_HINTS, (
                f"detector {det_id} has a tactical action ({action_id}) "
                "but no entry in _DETECTOR_REMEDIATION_HINTS — operator "
                "would see a fired finding with no fix hint."
            )
            # Tactical hints must be doctor-flag-shaped so the
            # operator can paste them onto their existing
            # ``talaria hermes doctor …`` command line.
            hint = doctor._DETECTOR_REMEDIATION_HINTS[det_id]
            assert hint.startswith("--"), (
                f"{det_id} tactical hint {hint!r} should start with -- "
                "so it appends to the doctor command, not replace it"
            )

    def test_skill_index_drift_hint_points_at_sibling_command(self) -> None:
        # ``skill_index_drift`` has an actionable remediation but it
        # lives in ``talaria skills prune``, not in the doctor's
        # tactical layer. The hint must (a) be present so the operator
        # sees a fix, and (b) start with the sibling command name so
        # the operator cannot mistake it for a doctor flag.
        hint = doctor._DETECTOR_REMEDIATION_HINTS[doctor.SKILL_INDEX_DRIFT]
        assert hint.startswith("talaria skills prune"), (
            f"skill_index_drift hint must start with the sibling "
            f"command name so it cannot be mistaken for a doctor flag; "
            f"got {hint!r}"
        )
        # The sibling command's dry-run-by-default safety model means
        # the hint can include ``--apply`` directly — the operator
        # will preview before committing because the bare command
        # without --apply is dry-run.
        assert "--apply" in hint
        # All three drift classes are listed so the hint is correct
        # for any single-class or multi-class drift finding.
        for flag in (
            "--prune-filesystem-only", "--prune-lock-only",
            "--prune-disabled-orphans",
        ):
            assert flag in hint, (
                f"skill_index_drift hint missing {flag}; the operator "
                "needs to know about every drift class"
            )

    def test_json_report_carries_remediation_field(
        self, tmp_path: Path,
    ) -> None:
        """End-to-end: ``doctor.run()`` attaches ``remediation`` to the
        per-detector JSON entries.

        The hint is attached regardless of fired status — JSON
        consumers can introspect every detector and know what flag
        would apply. The renderer is what gates the hint on
        ``fired=True``; the underlying JSON shape stays uniform.
        """
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(
            db,
            sessions=[{
                "id": "z1", "source": "cli", "model": "minimax/minimax-m3",
                "started_at": now - 86400 * 3, "ended_at": None,
                "output_tokens": 0, "message_count": 0,
                "api_call_count": 0, "rewind_count": 0, "archived": 0,
                "estimated_cost_usd": 0, "actual_cost_usd": 0,
                "cost_status": "ok", "end_reason": "cli_close",
            }],
        )
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        paths = _paths(tmp_path, state_db=db, log_dir=log_dir)
        report = doctor.run(
            paths, days=2, since=None, include_curator=False,
            free_flight=False,
        )
        per = {d["id"]: d for d in report["per_detector"]}
        # Zombie fired → remediation present and matches.
        assert per[doctor.ZOMBIE_SESSIONS]["fired"] is True
        assert per[doctor.ZOMBIE_SESSIONS]["remediation"] == (
            "--close-zombies [--apply]"
        )
        # The other two tactical detectors did NOT fire (no rows in
        # their tables) but still carry the hint so consumers know
        # the remediation shape if they ever do fire.
        for det_id in (
            doctor.COMPRESSION_STALE_LOCKS, doctor.GHOST_SESSIONS,
        ):
            assert per[det_id]["fired"] is False
            assert per[det_id]["remediation"], (
                f"{det_id} should carry its remediation hint even when not fired"
            )
        # Non-tactical detectors get None — they have no remediation.
        # Note: skill_index_drift is NOT in this list because it has
        # a remediation too (talaria skills prune) — see the
        # dedicated test above for that hint's contract.
        non_tactical = (
            "truncation_output", "truncation_finish_reason",
            "truncation_log_markers", "stream_drops",
            "compression_failures", "rewinds", "handoff_errors",
            "cost_anomalies",
        )
        for det_id in non_tactical:
            entry = per.get(det_id)
            if entry is None:
                continue
            assert entry["remediation"] is None, (
                f"{det_id} unexpectedly carries a remediation hint"
            )

    def test_renderer_prints_fix_line_for_fired_finding(
        self, tmp_path: Path,
    ) -> None:
        """The human renderer prints ``fix: <hint>`` after the fired
        finding so the operator sees the remediation next to the
        evidence."""
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(
            db,
            compression_locks=[{
                "session_id": "s1", "holder": "compressor-1",
                "acquired_at": now - 7200, "expires_at": now - 3600,
            }],
            sessions=[{
                "id": "s1", "source": "cli", "model": "minimax/minimax-m3",
                "started_at": now - 3600, "ended_at": now - 60,
                "output_tokens": 100, "message_count": 5,
                "api_call_count": 5, "rewind_count": 0, "archived": 0,
                "estimated_cost_usd": 0.01, "actual_cost_usd": 0.01,
                "cost_status": "ok", "end_reason": "cli_close",
            }],
        )
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        paths = _paths(tmp_path, state_db=db, log_dir=log_dir)
        report = doctor.run(
            paths, days=2, since=None, include_curator=False,
            free_flight=False,
        )
        _, text = doctor.render_human(report)
        # The fix line lives under the compression_stale_locks entry.
        # The summary line + first-flagged-line + fix line all share
        # the leading whitespace.
        assert "fix: --prune-stale-locks [--apply]" in text

    def test_renderer_skips_fix_line_for_clean_detector(
        self, tmp_path: Path,
    ) -> None:
        """A detector that did NOT fire should not emit a fix hint —
        telling the operator how to fix something that isn't broken
        is noise."""
        db = tmp_path / "state.db"
        make_full_state_db(db)  # empty state.db
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        paths = _paths(tmp_path, state_db=db, log_dir=log_dir)
        report = doctor.run(
            paths, days=2, since=None, include_curator=False,
            free_flight=False,
        )
        _, text = doctor.render_human(report)
        # No fired detector → no fix hint anywhere.
        assert "fix:" not in text


# ---------------- Redaction tests ----------------

class TestRedaction:
    def test_secret_parent_redacts_children(self) -> None:
        from talaria.hermos.doctor_free_flight import _redact_raw_yaml
        raw = "auth:\n  token: bearer-abc\n  type: oauth\n"
        out = _redact_raw_yaml(raw)
        assert "auth: ***REDACTED***" in out
        assert "token: ***REDACTED***" in out
        assert "type: ***REDACTED***" in out
        assert "bearer-abc" not in out

    def test_preserves_legitimate_keys(self) -> None:
        from talaria.hermos.doctor_free_flight import _redact_raw_yaml
        raw = "model:\n  default: gpt-5\nmoa:\n  presets:\n    coding:\n      max_tokens: 32768\n"
        out = _redact_raw_yaml(raw)
        assert "max_tokens: 32768" in out
        assert "default: gpt-5" in out

    def test_api_key_redaction(self) -> None:
        from talaria.hermos.doctor_free_flight import _redact_raw_yaml
        out = _redact_raw_yaml("api_key: sk-secret\nmodel: gpt\n")
        assert "api_key: ***REDACTED***" in out
        assert "sk-secret" not in out
        assert "model: gpt" in out

    def test_top_level_secret_key_redaction(self) -> None:
        from talaria.hermos.doctor_free_flight import _redact_raw_yaml
        out = _redact_raw_yaml("token: abc\nmodel: gpt\n")
        assert "token: ***REDACTED***" in out
        assert "abc" not in out

    def test_comments_and_blanks_preserved(self) -> None:
        from talaria.hermos.doctor_free_flight import _redact_raw_yaml
        raw = "# header comment\n\napi_key: sk-x\n# footer\n"
        out = _redact_raw_yaml(raw)
        assert "# header comment" in out
        assert "# footer" in out
        assert "sk-x" not in out


# ---------------- Free-flight tests ----------------

class TestFreeFlight:
    def test_zero_budget_returns_skipped(self, tmp_path: Path) -> None:
        paths = _paths(
            tmp_path, state_db=tmp_path / "s.db", log_dir=tmp_path / "logs",
        )
        results = doctor_free_flight.run(paths, days=2, log_lines=0)
        assert len(results) == 1
        assert results[0].id == "free_flight:skipped"
        assert results[0].fired is False

    def test_anomaly_finding_kind_parsed(self) -> None:
        r = doctor_free_flight._finding_to_result(
            0, {
                "kind": "anomaly",
                "id": "loud_session",
                "severity": "alert",
                "title": "Loud session",
                "summary": "Session s1 produced 800k output tokens",
                "evidence_quote": "session s1 output=800000",
            },
        )
        assert r.id == "free_flight:anomaly:loud_session"
        assert r.severity == "alert"
        assert r.fired is True
        assert r.evidence["kind"] == "anomaly"
        assert r.evidence["evidence_quote"] == "session s1 output=800000"

    def test_config_suggestion_kind_parsed(self) -> None:
        r = doctor_free_flight._finding_to_result(
            0, {
                "kind": "config_suggestion",
                "id": "lower_max_tokens",
                "severity": "warn",
                "title": "Lower max_tokens for coding preset",
                "summary": "Several sessions hit the 32k cap",
                "yaml_path": "moa.presets.coding.max_tokens",
                "current_value": "32768",
                "suggested_value": "16384",
                "rationale": "Most sessions use < 16k; lower reduces cost",
            },
        )
        assert r.id == "free_flight:config:lower_max_tokens"
        # Config suggestions never fire; the operator decides.
        assert r.fired is False
        assert r.evidence["kind"] == "config_suggestion"
        assert r.evidence["yaml_path"] == "moa.presets.coding.max_tokens"
        assert "16384" in r.summary
        assert "32768" in r.summary

    def test_default_kind_is_anomaly(self) -> None:
        """A finding without a 'kind' field defaults to anomaly."""
        r = doctor_free_flight._finding_to_result(
            0, {"id": "x", "severity": "info", "summary": "s"},
        )
        assert r.id == "free_flight:anomaly:x"
        assert r.evidence["kind"] == "anomaly"

    def test_invalid_kind_falls_back_to_anomaly(self) -> None:
        r = doctor_free_flight._finding_to_result(
            0, {"kind": "unicorn", "id": "x", "severity": "info", "summary": "s"},
        )
        assert r.id == "free_flight:anomaly:x"

    def test_parse_findings_handles_fenced_json(self) -> None:
        stdout = 'Here is my response: ```json\n{"findings":[{"id":"a","severity":"warn","summary":"b"}]}\n```'
        findings = doctor_free_flight._parse_findings(stdout)
        assert len(findings) == 1
        assert findings[0]["id"] == "a"

    def test_parse_findings_empty_for_garbage(self) -> None:
        assert doctor_free_flight._parse_findings("not json at all") == []
        assert doctor_free_flight._parse_findings("") == []

    def test_run_with_stub_runner_returns_findings(self, tmp_path: Path) -> None:
        """Free-flight with a stub runner returns the model's findings."""
        # Build a real state.db so evidence assembly succeeds.
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "s1", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now - 60, "output_tokens": 1000,
             "rewind_count": 0, "archived": 0, "message_count": 1, "api_call_count": 1,
             "cost_status": "ok", "estimated_cost_usd": 0.01, "actual_cost_usd": 0.01,
             "end_reason": "cli_close", "ended_at": now},
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        # A config.yaml is required so the free-flight pass has data
        # to assemble (it returns no_data when both logs and config
        # are absent).
        config = tmp_path / "profiles" / "test" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("model:\n  default: gpt-5\n")
        paths = _paths(tmp_path, state_db=db, log_dir=log_dir)

        def stub(prompt, *, timeout, **kw):
            return (
                0,
                '{"findings":[{"kind":"anomaly","id":"loud","severity":"info","summary":"x","evidence_quote":"q"}]}',
                "",
            )

        results = doctor_free_flight.run(
            paths, days=2,
            log_lines=200,
            subprocess_runner=stub,
        )
        assert len(results) == 1
        assert results[0].id == "free_flight:anomaly:loud"
        assert results[0].fired is False  # info severity

    def test_run_unavailable_runner_degrades(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        config = tmp_path / "profiles" / "test" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("model:\n  default: gpt-5\n")
        paths = _paths(tmp_path, state_db=db, log_dir=log_dir)

        def stub(prompt, *, timeout, **kw):
            raise doctor_llm.AdjudicationUnavailable("offline")

        results = doctor_free_flight.run(
            paths, days=2,
            log_lines=200,
            subprocess_runner=stub,
        )
        assert len(results) == 1
        assert results[0].id == "free_flight:unavailable"
        assert "offline" in results[0].summary


class TestDumpDatabaseSlices:
    """Coverage for :func:`doctor_free_flight._dump_database_slices`."""

    def test_writes_all_four_slices(self, tmp_path: Path) -> None:
        """All four JSON slice files are created with correct keys."""
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "s1", "source": "cli", "model": "glm-5.2",
             "started_at": now - 60, "output_tokens": 500,
             "message_count": 10, "rewind_count": 0,
             "estimated_cost_usd": 0.01, "actual_cost_usd": 0.01,
             "cost_status": "ok", "end_reason": "cli_close",
             "ended_at": now},
        ], messages=[
            {"session_id": "s1", "role": "tool", "content": "error: something failed",
             "timestamp": now - 30, "finish_reason": "stop"},
        ], compression_locks=[
            {"session_id": "s1", "holder": "compressor", "acquired_at": now - 10,
             "expires_at": now + 100},
        ])
        paths = _paths(tmp_path, state_db=db, log_dir=tmp_path / "logs")
        slices = doctor_free_flight._dump_database_slices(
            paths, tmp_path / "slices", since_ts=now - 3600,
        )
        for key in ("sessions", "compression_locks",
                    "messages_failures", "messages_truncations"):
            assert key in slices
            assert slices[key].exists()

    def test_sessions_slice_contains_rows(self, tmp_path: Path) -> None:
        """Sessions slice has the inserted session with expected columns."""
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "s1", "source": "cli", "model": "glm-5.2",
             "started_at": now - 60, "output_tokens": 500,
             "message_count": 10, "rewind_count": 2,
             "estimated_cost_usd": 0.01, "actual_cost_usd": 0.01,
             "cost_status": "ok", "end_reason": "cli_close",
             "ended_at": now},
        ])
        paths = _paths(tmp_path, state_db=db, log_dir=tmp_path / "logs")
        slices = doctor_free_flight._dump_database_slices(
            paths, tmp_path / "slices", since_ts=now - 3600,
        )
        data = json.loads(slices["sessions"].read_text())
        assert len(data) == 1
        assert data[0]["id"] == "s1"
        assert data[0]["model"] == "glm-5.2"
        assert data[0]["rewind_count"] == 2

    def test_sessions_slice_respects_time_window(self, tmp_path: Path) -> None:
        """Sessions older than since_ts are excluded."""
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "old", "source": "cli", "model": "m1",
             "started_at": now - 86400 * 30, "output_tokens": 100,
             "message_count": 1, "rewind_count": 0,
             "end_reason": "cli_close", "ended_at": now - 86400 * 30 + 10},
            {"id": "new", "source": "cli", "model": "m1",
             "started_at": now - 60, "output_tokens": 200,
             "message_count": 1, "rewind_count": 0,
             "end_reason": "cli_close", "ended_at": now},
        ])
        paths = _paths(tmp_path, state_db=db, log_dir=tmp_path / "logs")
        slices = doctor_free_flight._dump_database_slices(
            paths, tmp_path / "slices", since_ts=now - 3600,
        )
        data = json.loads(slices["sessions"].read_text())
        assert len(data) == 1
        assert data[0]["id"] == "new"

    def test_failures_slice_captures_error_messages(self, tmp_path: Path) -> None:
        """Messages containing error keywords appear in failures slice."""
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, messages=[
            {"session_id": "s1", "role": "tool",
             "content": "Traceback: ValueError: something failed",
             "timestamp": now - 30, "finish_reason": "stop"},
            {"session_id": "s1", "role": "assistant",
             "content": "All good here",
             "timestamp": now - 20, "finish_reason": "stop"},
        ])
        paths = _paths(tmp_path, state_db=db, log_dir=tmp_path / "logs")
        slices = doctor_free_flight._dump_database_slices(
            paths, tmp_path / "slices", since_ts=now - 3600,
        )
        data = json.loads(slices["messages_failures"].read_text())
        assert len(data) == 1
        assert "failed" in data[0]["content_preview"]

    def test_truncations_slice_captures_length_finish_reason(self, tmp_path: Path) -> None:
        """Messages with finish_reason='length' appear in truncations slice."""
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, messages=[
            {"session_id": "s1", "role": "assistant",
             "content": "x" * 2000,
             "timestamp": now - 30, "finish_reason": "length"},
            {"session_id": "s1", "role": "assistant",
             "content": "short",
             "timestamp": now - 20, "finish_reason": "stop"},
        ])
        paths = _paths(tmp_path, state_db=db, log_dir=tmp_path / "logs")
        slices = doctor_free_flight._dump_database_slices(
            paths, tmp_path / "slices", since_ts=now - 3600,
        )
        data = json.loads(slices["messages_truncations"].read_text())
        assert len(data) == 1
        assert data[0]["finish_reason"] == "length"
        # Content preview should be capped
        assert len(data[0]["content_preview"]) <= doctor_free_flight.CONTENT_PREVIEW_CHARS

    def test_compression_locks_slice_has_rows(self, tmp_path: Path) -> None:
        """Compression locks are dumped."""
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, compression_locks=[
            {"session_id": "s1", "holder": "compressor",
             "acquired_at": now - 10, "expires_at": now + 100},
        ])
        paths = _paths(tmp_path, state_db=db, log_dir=tmp_path / "logs")
        slices = doctor_free_flight._dump_database_slices(
            paths, tmp_path / "slices", since_ts=now - 3600,
        )
        data = json.loads(slices["compression_locks"].read_text())
        assert len(data) == 1
        assert data[0]["session_id"] == "s1"
        assert data[0]["holder"] == "compressor"

    def test_missing_db_produces_empty_slices(self, tmp_path: Path) -> None:
        """When state.db does not exist, all slices are empty arrays."""
        paths = _paths(
            tmp_path, state_db=tmp_path / "nonexistent.db",
            log_dir=tmp_path / "logs",
        )
        slices = doctor_free_flight._dump_database_slices(
            paths, tmp_path / "slices", since_ts=0,
        )
        for key in ("sessions", "compression_locks",
                    "messages_failures", "messages_truncations"):
            data = json.loads(slices[key].read_text())
            assert data == []

    def test_content_preview_is_truncated(self, tmp_path: Path) -> None:
        """Message content previews do not exceed CONTENT_PREVIEW_CHARS."""
        db = tmp_path / "state.db"
        now = _now()
        long_content = "error: " + "x" * 5000
        make_full_state_db(db, messages=[
            {"session_id": "s1", "role": "tool",
             "content": long_content,
             "timestamp": now - 30, "finish_reason": "stop"},
        ])
        paths = _paths(tmp_path, state_db=db, log_dir=tmp_path / "logs")
        slices = doctor_free_flight._dump_database_slices(
            paths, tmp_path / "slices", since_ts=now - 3600,
        )
        data = json.loads(slices["messages_failures"].read_text())
        assert len(data) == 1
        assert len(data[0]["content_preview"]) <= doctor_free_flight.CONTENT_PREVIEW_CHARS


class TestOrchestratorFreeFlight:
    def _setup(self, tmp_path: Path) -> ResolvedPaths:
        """Shared setup: state.db + log_dir + minimal config.yaml."""
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        config = tmp_path / "profiles" / "test" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("model:\n  default: gpt-5\n")
        return _paths(tmp_path, state_db=db, log_dir=log_dir)

    def test_free_flight_default_on(self, tmp_path: Path) -> None:
        """Free-flight is default-on; the report has a free_flight block.

        The default behavior is to call the curator model. For this
        test we pass a stub runner that returns zero findings, so the
        report is hermes-free and we can assert the block shape
        without a live model call.
        """
        paths = self._setup(tmp_path)

        def stub(prompt, *, timeout, **kw):
            return (0, '{"findings": []}', "")

        # default free_flight=True; pass stub so no hermes chat is called
        report = doctor.run(
            paths, days=2,
            free_flight=True,
            free_flight_runner=stub,
        )
        assert report["free_flight"] is not None
        assert report["free_flight"]["findings_count"] == 0

    def test_free_flight_fires_propagate(self, tmp_path: Path) -> None:
        paths = self._setup(tmp_path)

        def stub(prompt, *, timeout, **kw):
            return (
                0,
                '{"findings":[{"kind":"anomaly","id":"rude","severity":"alert","summary":"rude session","evidence_quote":"q"}]}',
                "",
            )

        report = doctor.run(
            paths, days=2,
            free_flight=True, free_flight_log_lines=200,
            free_flight_runner=stub,
        )
        assert report["free_flight"] is not None
        assert report["free_flight"]["findings_count"] >= 1
        # The alert anomaly propagates to report.fired.
        assert report["fired"] is True
        ff_ids = [d["id"] for d in report["per_detector"]]
        assert any(i.startswith("free_flight:") for i in ff_ids)


# ---------------- CLI tests ----------------

class TestCli:
    def test_help_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "doctor", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--no-free-flight" in result.stdout
        assert "--apply-curator-suggestions" in result.stdout
        assert "--dry-run" in result.stdout
        assert "--only" in result.stdout
        assert "--skip" in result.stdout
        assert "--prune-stale-locks" in result.stdout
        assert "--close-zombies" in result.stdout
        assert "--prune-ghost-sessions" in result.stdout
        assert "--apply" in result.stdout

    def test_version_flag(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "--version"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "talaria" in result.stdout.lower()

    def test_clean_run_exits_zero(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "doctor",
             "--state-db", str(db), "--log-dir", str(log_dir),
             "--no-free-flight", "--json",
             # skill_index_drift reads paths.hermes_root (not the
             # overridden state_db / log_dir), so it inspects the live
             # ~/.hermes/ of the operator running the test. Skip it
             # in CLI subprocess tests; coverage lives in the unit
             # tests under TestSkillIndexDrift with a hermes_root
             # built under tmp_path.
             "--skip", doctor.SKILL_INDEX_DRIFT],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["fired"] is False
        # SKILL_INDEX_DRIFT is skipped (see comment above); remaining
        # detectors are present.
        assert len(payload["per_detector"]) == len(doctor.DETECTOR_IDS) - 1

    def test_fired_run_exits_1(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "zombie", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now - 25 * 3600, "ended_at": None,
             "rewind_count": 0, "archived": 0, "message_count": 0,
             "api_call_count": 0, "output_tokens": 0},
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "doctor",
             "--state-db", str(db), "--log-dir", str(log_dir),
             "--no-free-flight", "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert payload["fired"] is True

    def test_only_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "doctor",
             "--state-db", str(db), "--log-dir", str(log_dir),
             "--no-free-flight", "--only", "zombie_sessions", "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["selected_detectors"] == ["zombie_sessions"]
        assert len(payload["per_detector"]) == 1

    def test_unknown_only_exits_2(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "doctor",
             "--state-db", str(db), "--log-dir", str(log_dir),
             "--no-free-flight", "--only", "nonsense", "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "unknown detector" in result.stderr

    def test_quiet_suppresses_human_report(self, tmp_path: Path) -> None:
        """With -q/--quiet, the human report is suppressed (exit code only)."""
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "zombie", "source": "cli", "model": "minimax/minimax-m3",
             "started_at": now - 25 * 3600, "ended_at": None,
             "rewind_count": 0, "archived": 0, "message_count": 0,
             "api_call_count": 0, "output_tokens": 0},
        ])
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "doctor",
             "--state-db", str(db), "--log-dir", str(log_dir),
             "--no-free-flight", "--quiet"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        # No JSON, --quiet: stdout is empty.
        assert result.stdout == ""

    def test_default_run_prints_human_report(self, tmp_path: Path) -> None:
        """The default run prints the human-readable report (verbose-by-default)."""
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "doctor",
             "--state-db", str(db), "--log-dir", str(log_dir),
             "--no-free-flight", "--skip", doctor.SKILL_INDEX_DRIFT],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Profile doctor" in result.stdout
        assert "VERDICT: clean" in result.stdout

    def test_verbose_prints_human_report(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "doctor",
             "--state-db", str(db), "--log-dir", str(log_dir),
             "--no-free-flight", "-v", "--skip", doctor.SKILL_INDEX_DRIFT],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Profile doctor" in result.stdout
        assert "VERDICT: clean" in result.stdout

    def test_skipped_header_in_report(self, tmp_path: Path) -> None:
        """--skip surfaces a 'skipped:' header listing excluded detectors."""
        db = tmp_path / "state.db"
        make_full_state_db(db)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "doctor",
             "--state-db", str(db), "--log-dir", str(log_dir),
             "--no-free-flight",
             "--skip", f"{doctor.ZOMBIE_SESSIONS},{doctor.GHOST_SESSIONS},{doctor.SKILL_INDEX_DRIFT}",
             "-v"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "skipped:" in result.stdout
        assert doctor.ZOMBIE_SESSIONS in result.stdout
        assert doctor.GHOST_SESSIONS in result.stdout

    def test_show_resolution(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "doctor",
             "--state-db", str(tmp_path / "nope.db"),
             "--log-dir", str(tmp_path / "nope"),
             "--show-resolution"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "detector_ids" in result.stdout


# ---------------- Log discovery + scan tests ----------------

class TestDiscoverLogFiles:
    def test_empty_dir(self, tmp_path: Path) -> None:
        assert doctor.discover_log_files(tmp_path) == []

    def test_missing_dir(self, tmp_path: Path) -> None:
        assert doctor.discover_log_files(tmp_path / "no-such") == []

    def test_picks_up_active_and_rotated(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "agent.log").write_text("")
        (log_dir / "agent.log.1").write_text("")
        (log_dir / "agent.log.1.gz").write_text("")
        (log_dir / "errors.log").write_text("")
        (log_dir / "tui_gateway_crash.log").write_text("")
        (log_dir / "README.md").write_text("")  # non-log, must be ignored
        names = [p.name for p in doctor.discover_log_files(log_dir)]
        assert names == [
            "agent.log", "agent.log.1", "agent.log.1.gz",
            "errors.log", "tui_gateway_crash.log",
        ]

    def test_excludes_curator_by_default(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "agent.log").write_text("")
        curator = log_dir / "curator" / "20260704_205000"
        curator.mkdir(parents=True)
        (curator / "agent.log").write_text("")
        names = [p.name for p in doctor.discover_log_files(log_dir)]
        assert names == ["agent.log"]

    def test_include_curator_walks_snapshot_tree(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "agent.log").write_text("")
        curator = log_dir / "curator" / "20260704_205000"
        curator.mkdir(parents=True)
        (curator / "agent.log").write_text("")
        (curator / "errors.log").write_text("")
        names = [p.name for p in doctor.discover_log_files(log_dir, include_curator=True)]
        assert names.count("agent.log") == 2
        assert "errors.log" in names


class TestScanLogTruncations:
    def test_no_log_files_ok(self) -> None:
        result = doctor.scan_log_truncations([], 0.0)
        assert result["ok"] is True
        assert result["length_class_hits"] == 0
        assert result["stream_drop_warnings"] == 0
        assert result["files_scanned"] == 0

    def test_warning_level_triggers_length_hit(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        now = _now_dt()
        agent = log_dir / "agent.log"
        agent.write_text(_log_line("WARNING", "finish_reason='length' output=12345", now))
        result = doctor.scan_log_truncations([agent], (now - timedelta(hours=1)).timestamp())
        assert result["length_class_hits"] == 1
        assert result["matches"][0]["file"] == "agent.log"

    def test_info_level_does_not_trigger(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        now = _now_dt()
        body = "user said: 'Response truncated (finish_reason='length')...'"
        agent = log_dir / "agent.log"
        agent.write_text(_log_line("INFO", body, now))
        result = doctor.scan_log_truncations([agent], (now - timedelta(hours=1)).timestamp())
        assert result["length_class_hits"] == 0

    def test_stream_drop_counted_separately(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        now = _now_dt()
        body = "Stream ended with no finish_reason while a tool call's arguments were still incomplete"
        agent = log_dir / "agent.log"
        agent.write_text(_log_line("WARNING", body, now))
        result = doctor.scan_log_truncations([agent], (now - timedelta(hours=1)).timestamp())
        assert result["length_class_hits"] == 0
        assert result["stream_drop_warnings"] == 1

    def test_old_lines_filtered_by_window(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        old = _now_dt() - timedelta(days=30)
        agent = log_dir / "agent.log"
        agent.write_text(_log_line("ERROR", "finish_reason='length' (old)", old))
        result = doctor.scan_log_truncations([agent], (old + timedelta(days=1)).timestamp())
        assert result["length_class_hits"] == 0

    def test_double_quoted_finish_reason(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        now = _now_dt()
        errors = log_dir / "errors.log"
        errors.write_text(_log_line("ERROR", 'finish_reason="length" output=9999', now))
        result = doctor.scan_log_truncations([errors], (now - timedelta(hours=1)).timestamp())
        assert result["length_class_hits"] == 1

    def test_aggregates_across_multiple_files(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        now = _now_dt()
        agent = log_dir / "agent.log"
        errors = log_dir / "gateway.log"
        tui = log_dir / "tui_gateway_crash.log"
        agent.write_text(_log_line("WARNING", "finish_reason='length' a", now))
        errors.write_text(_log_line("ERROR", "finish_reason='length' b", now))
        with errors.open("a") as fh:
            fh.write(_log_line("ERROR", "hit max output tokens c", now))
        tui.write_text(_log_line("CRITICAL", "finish_reason='length' d", now))
        result = doctor.scan_log_truncations(
            [agent, errors, tui], (now - timedelta(hours=1)).timestamp(),
        )
        assert result["length_class_hits"] == 4
        assert result["files_scanned"] == 3
        per_file = {Path(p["path"]).name: p for p in result["per_file"]}
        assert per_file["agent.log"]["length_class_hits"] == 1
        assert per_file["gateway.log"]["length_class_hits"] == 2
        assert per_file["tui_gateway_crash.log"]["length_class_hits"] == 1

    def test_window_applied_per_line_in_long_file(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        old = _now_dt() - timedelta(days=10)
        recent = _now_dt() - timedelta(hours=1)
        agent = log_dir / "agent.log"
        agent.write_text(
            _log_line("ERROR", "finish_reason='length' (old)", old)
            + _log_line("ERROR", "finish_reason='length' (recent)", recent)
        )
        result = doctor.scan_log_truncations(
            [agent], (_now_dt() - timedelta(days=2)).timestamp(),
        )
        assert result["length_class_hits"] == 1
        assert "(recent)" in result["matches"][0]["line"]

    def test_missing_file_reported_not_crashed(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = doctor.scan_log_truncations([log_dir / "nope.log"], 0.0)
        assert result["ok"] is True
        assert result["files_scanned"] == 0
        assert result["files_missing"] == 1
        assert result["per_file"][0]["scanned"] is False


class TestSkillIndexDrift:
    """The skill_index_drift detector surfaces three classes of drift.

    Mirrors :mod:`talaria.hermos.skill_index` — the detector is a thin
    read-only consumer of :func:`read_index`.
    """

    def _setup(self, root: Path, *, profile: str = "vc-client"):
        """Build a hermes-root layout and return a ResolvedPaths."""
        root.mkdir(parents=True, exist_ok=True)
        if profile == "default":
            state = root / "state.db"
            logs = root / "logs"
            cfg = root / "config.yaml"
            sroot = root / "skills"
        else:
            state = root / "profiles" / profile / "state.db"
            logs = root / "profiles" / profile / "logs"
            cfg = root / "profiles" / profile / "config.yaml"
            sroot = root / "profiles" / profile / "skills"
        state.parent.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        cfg.parent.mkdir(parents=True, exist_ok=True)
        paths = ResolvedPaths(profile=profile, hermes_root=root, state_db=state, log_dir=logs)
        return paths, sroot, cfg

    def _make_skill(self, sroot: Path, name: str, *, category: str | None = None) -> Path:
        d = sroot / category / name if category else sroot / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
        return d

    def _make_lock(self, sroot: Path, names: list[str]) -> Path:
        hub = sroot / ".hub"
        hub.mkdir(parents=True, exist_ok=True)
        lock = hub / "lock.json"
        installed = {n: {"source": "official", "install_path": n} for n in names}
        lock.write_text(json.dumps({"version": 1, "installed": installed}))
        return lock

    def _make_config(self, cfg: Path, disabled: list[str]) -> None:
        cfg.write_text(dump_yaml({"skills": {"disabled": disabled}}))

    def test_clean_index_is_info(self, tmp_path: Path) -> None:
        paths, sroot, cfg = self._setup(tmp_path / ".hermes")
        self._make_skill(sroot, "alpha", category="devops")
        self._make_lock(sroot, ["alpha"])
        self._make_config(cfg, [])

        r = doctor.detector_skill_index_drift(paths)
        assert r.id == doctor.SKILL_INDEX_DRIFT
        assert r.severity == doctor.SEVERITY_INFO
        assert r.fired is False
        assert "consistent" in r.summary.lower()

    def test_filesystem_only_fires_alert(self, tmp_path: Path) -> None:
        paths, sroot, cfg = self._setup(tmp_path / ".hermes")
        self._make_skill(sroot, "alpha")
        self._make_skill(sroot, "beta", category="devops")
        self._make_lock(sroot, ["alpha"])
        self._make_config(cfg, [])

        r = doctor.detector_skill_index_drift(paths)
        assert r.severity == doctor.SEVERITY_ALERT
        assert r.fired is True
        assert r.evidence["filesystem_only"] == ["beta"]

    def test_lock_only_fires_alert(self, tmp_path: Path) -> None:
        paths, sroot, cfg = self._setup(tmp_path / ".hermes")
        self._make_skill(sroot, "alpha")
        self._make_lock(sroot, ["alpha", "phantom"])
        self._make_config(cfg, [])

        r = doctor.detector_skill_index_drift(paths)
        assert r.fired is True
        assert r.evidence["lock_only"] == ["phantom"]

    def test_disabled_orphans_fires_alert(self, tmp_path: Path) -> None:
        paths, sroot, cfg = self._setup(tmp_path / ".hermes")
        self._make_skill(sroot, "real")
        self._make_lock(sroot, ["real"])
        self._make_config(cfg, ["real", "gone"])

        r = doctor.detector_skill_index_drift(paths)
        assert r.fired is True
        assert r.evidence["disabled_orphans"] == ["gone"]
        assert r.evidence["disabled_present"] == ["real"]

    def test_all_three_classes_summary(self, tmp_path: Path) -> None:
        paths, sroot, cfg = self._setup(tmp_path / ".hermes")
        self._make_skill(sroot, "alpha")
        self._make_skill(sroot, "beta")
        self._make_lock(sroot, ["beta", "phantom"])
        self._make_config(cfg, ["beta", "gone"])

        r = doctor.detector_skill_index_drift(paths)
        assert r.fired is True
        summary = r.summary.lower()
        assert "filesystem-only" in summary
        assert "lock-only" in summary
        assert "disabled-orphans" in summary
