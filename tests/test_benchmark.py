"""Tests for talaria.hermos.benchmark.

Covers model discovery (dedup, sources), state.db aggregation
(call counts, token averages, cost, reasoning level, first-response
latency), models.dev capability enrichment (slug matching across
provider prefixes), cache TTL logic, smoke-call stubbing, the human
renderer, and the CLI subprocess surface.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from pathlib import Path

import pytest
import yaml

from talaria.hermos import benchmark
from talaria.hermos.benchmark import ModelTarget, discover_model_targets
from talaria.paths import ResolvedPaths
from tests._helpers import make_full_state_db


def _paths(tmp_path: Path, *, state_db: Path | None = None) -> ResolvedPaths:
    return ResolvedPaths(
        profile="test",
        hermes_root=tmp_path,
        state_db=state_db or tmp_path / "state.db",
        log_dir=tmp_path / "logs",
    )


def _write_config(tmp_path: Path, cfg: dict) -> Path:
    config_dir = tmp_path / "profiles" / "test"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = config_dir / "config.yaml"
    config.write_text(yaml.dump(cfg))
    return config


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


# ---------------- Model discovery ----------------

class TestDiscoverModelTargets:
    def test_default_model(self) -> None:
        cfg = {"model": {"default": "org/model-a"}, "provider": "prov"}
        targets = discover_model_targets(cfg)
        assert len(targets) == 1
        assert targets[0].model == "org/model-a"
        assert targets[0].provider == "prov"
        assert "model.default" in targets[0].sources

    def test_default_model_provider_from_model_block(self) -> None:
        # Canonical Hermes layout: provider lives under `model.provider`,
        # not at the top level. Must be picked up for the default model.
        cfg = {"model": {"default": "org/model-a", "provider": "kilocode"}}
        targets = discover_model_targets(cfg)
        assert len(targets) == 1
        assert targets[0].provider == "kilocode"

    def test_top_level_provider_overrides_model_provider(self) -> None:
        # Top-level `provider` wins over `model.provider` (legacy/override).
        cfg = {
            "model": {"default": "org/model-a", "provider": "block-prov"},
            "provider": "top-prov",
        }
        targets = discover_model_targets(cfg)
        assert targets[0].provider == "top-prov"

    def test_aliases_dedup_same_model(self) -> None:
        cfg = {
            "model": {"default": "org/model-a", "aliases": {
                "_curator": "org/model-a",
                "_compression": "org/model-a",
            }},
            "provider": "prov",
        }
        targets = discover_model_targets(cfg)
        assert len(targets) == 1
        t = targets[0]
        assert t.model == "org/model-a"
        assert len(t.sources) == 3  # default + 2 aliases

    def test_alias_provider_from_auxiliary_block(self) -> None:
        cfg = {
            "model": {"aliases": {"_curator": "org/model-a"}},
            "auxiliary": {"curator": {"provider": "custom-prov"}},
            "provider": "top-prov",
        }
        targets = discover_model_targets(cfg)
        assert targets[0].provider == "custom-prov"

    def test_alias_provider_falls_back_to_top(self) -> None:
        cfg = {
            "model": {"aliases": {"_curator": "org/model-a"}},
            "provider": "top-prov",
        }
        targets = discover_model_targets(cfg)
        assert targets[0].provider == "top-prov"

    def test_auxiliary_models_discovered(self) -> None:
        cfg = {
            "model": {"default": "org/main"},
            "auxiliary": {
                "curator": {"model": "org/curator", "provider": "cp"},
                "vision": {"model": "org/vision"},
            },
            "provider": "prov",
        }
        targets = discover_model_targets(cfg)
        models = {t.model: t for t in targets}
        assert "org/main" in models
        assert "org/curator" in models
        assert "org/vision" in models
        assert models["org/curator"].provider == "cp"

    def test_empty_model_skipped(self) -> None:
        cfg = {
            "model": {"default": "", "aliases": {"_x": "", "_y": "org/b"}},
            "provider": "prov",
        }
        targets = discover_model_targets(cfg)
        assert len(targets) == 1
        assert targets[0].model == "org/b"

    def test_no_models(self) -> None:
        assert discover_model_targets({}) == []


# ---------------- State.db aggregation ----------------

class TestStateDbAggregation:
    def test_aggregate_groups_by_model(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        base = {"source": "cli", "started_at": now, "rewind_count": 0,
                "archived": 0, "message_count": 2, "api_call_count": 1,
                "reasoning_tokens": 0, "actual_cost_usd": 0.0,
                "input_tokens": 0, "output_tokens": 0}
        make_full_state_db(db, sessions=[
            {**base, "id": "s1", "model": "org/a",
             "input_tokens": 1000, "output_tokens": 200,
             "reasoning_tokens": 50, "actual_cost_usd": 0.01},
            {**base, "id": "s2", "model": "org/a",
             "input_tokens": 3000, "output_tokens": 400,
             "reasoning_tokens": 150, "actual_cost_usd": 0.03},
            {**base, "id": "s3", "model": "org/b",
             "input_tokens": 500, "output_tokens": 100},
        ])
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            stats = benchmark._aggregate_state_db(con, now - 86400)
        finally:
            con.close()
        assert stats["org/a"]["call_count"] == 2
        assert stats["org/a"]["avg_input_tokens"] == 2000
        assert stats["org/a"]["avg_reasoning_tokens"] == 100
        assert stats["org/a"]["total_cost_usd"] == 0.04
        assert stats["org/b"]["call_count"] == 1

    def test_window_filters_old_sessions(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "old", "source": "cli", "model": "org/a",
             "started_at": now - 86400 * 10, "input_tokens": 9999,
             "rewind_count": 0, "archived": 0, "message_count": 0, "api_call_count": 0},
            {"id": "new", "source": "cli", "model": "org/a",
             "started_at": now, "input_tokens": 100,
             "rewind_count": 0, "archived": 0, "message_count": 0, "api_call_count": 0},
        ])
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            stats = benchmark._aggregate_state_db(con, now - 86400)
        finally:
            con.close()
        assert stats["org/a"]["call_count"] == 1
        assert stats["org/a"]["avg_input_tokens"] == 100

    def test_reasoning_config_extracted(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        cfg_json = json.dumps({
            "reasoning_config": {"enabled": True, "effort": "medium"},
            "max_iterations": 60,
        })
        make_full_state_db(db, sessions=[
            {"id": "s1", "source": "cli", "model": "org/a",
             "started_at": now, "model_config": cfg_json,
             "rewind_count": 0, "archived": 0, "message_count": 0, "api_call_count": 0},
        ])
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rc = benchmark._reasoning_config_for_model(con, now - 86400)
        finally:
            con.close()
        assert "org/a" in rc
        assert rc["org/a"]["reasoning_config"]["effort"] == "medium"

    def test_first_response_latency(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(
            db,
            sessions=[
                {"id": "s1", "source": "cli", "model": "org/a",
                 "started_at": now, "rewind_count": 0, "archived": 0,
                 "message_count": 2, "api_call_count": 1},
            ],
            messages=[
                {"session_id": "s1", "role": "user", "content": "hi",
                 "timestamp": now},
                {"session_id": "s1", "role": "assistant", "content": "hello",
                 "timestamp": now + 3.5},
            ],
        )
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            lat = benchmark._first_response_latency(con, now - 86400)
        finally:
            con.close()
        assert "org/a" in lat
        assert lat["org/a"] == pytest.approx(3.5, abs=0.1)


# ---------------- models.dev enrichment ----------------

class TestSlugMatching:
    def test_slug_simple(self) -> None:
        assert benchmark._slug("org/model-a") == "model-a"

    def test_slug_nested_prefix(self) -> None:
        assert benchmark._slug("kilocode/zai-coding/glm-5.2") == "glm-5.2"

    def test_slug_no_prefix(self) -> None:
        assert benchmark._slug("glm-5.2") == "glm-5.2"


class TestEnrichCapabilities:
    def test_match_by_slug(self) -> None:
        models_dev = {
            "glm-5.2": {"reasoning": True, "tool_call": True,
                        "modalities": {"input": ["text"]},
                        "limit": {"context": 200000, "output": 131072},
                        "cost": {"input": 1.1, "output": 4.0}},
        }
        caps = benchmark._enrich_capabilities("zai-coding/glm-5.2", models_dev)
        assert caps["reasoning"] is True
        assert caps["tool_call"] is True
        assert caps["context_limit"] == 200000
        assert caps["cost_per_mtokens"]["input"] == 1.1

    def test_no_match_returns_empty(self) -> None:
        caps = benchmark._enrich_capabilities("unknown/model", {})
        assert caps == {}

    def test_vision_detected_from_modalities(self) -> None:
        models_dev = {
            "vision-model": {
                "reasoning": False, "tool_call": True, "attachment": False,
                "modalities": {"input": ["text", "image"]},
            }
        }
        caps = benchmark._enrich_capabilities("org/vision-model", models_dev)
        assert caps["vision"] is True


# ---------------- Cache TTL ----------------

class TestCacheTTL:
    def test_fresh_cache_is_fresh(self) -> None:
        entry = {"smoke_ts": time.time()}
        assert benchmark._cache_is_fresh(entry, ttl=1800)

    def test_stale_cache_is_stale(self) -> None:
        entry = {"smoke_ts": time.time() - 3600}
        assert not benchmark._cache_is_fresh(entry, ttl=1800)

    def test_missing_ts_is_stale(self) -> None:
        assert not benchmark._cache_is_fresh({}, ttl=1800)


# ---------------- Orchestrator (run) ----------------

class TestRun:
    def test_no_smoke_reports_state_db_only(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "s1", "source": "cli", "model": "org/a",
             "started_at": now, "input_tokens": 1000, "output_tokens": 500,
             "rewind_count": 0, "archived": 0, "message_count": 0, "api_call_count": 1,
             "reasoning_tokens": 0, "actual_cost_usd": 0.0},
        ])
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=db)
        report = benchmark.run(
            paths,
            days=7,
            smoke=False,
            config_path=config,
            cache_path=tmp_path / "cache.json",
        )
        assert report["ok"] is True
        assert report["smoke_enabled"] is False
        assert report["smoke_calls_made"] == 0
        per = report["per_model"]
        assert len(per) == 1
        assert per[0]["model"] == "org/a"
        assert per[0]["state_db"]["call_count"] == 1
        assert per[0]["smoke"] is None

    def test_smoke_with_stub_runner(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=db)

        def fake_runner(prompt, *, model, provider, timeout, **kw):
            return 0, '{"ok": true}', ""

        report = benchmark.run(
            paths,
            days=7,
            smoke=True,
            smoke_runner=fake_runner,
            config_path=config,
            cache_path=tmp_path / "cache.json",
        )
        assert report["smoke_calls_made"] == 1
        assert report["smoke_calls_cached"] == 0
        smoke = report["per_model"][0]["smoke"]
        assert smoke["ok"] is True

    def test_cached_result_reused_within_ttl(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=db)
        cache = tmp_path / "cache.json"
        call_count = [0]

        def counting_runner(prompt, *, model, provider, timeout, **kw):
            call_count[0] += 1
            return 0, '{"ok": true}', ""

        # First call: fresh smoke
        benchmark.run(paths, days=7, smoke=True, smoke_runner=counting_runner,
                       config_path=config, cache_path=cache)
        assert call_count[0] == 1
        # Second call: should use cache
        benchmark.run(paths, days=7, smoke=True, smoke_runner=counting_runner,
                       config_path=config, cache_path=cache)
        assert call_count[0] == 1  # no new call

    def test_stale_cache_triggers_fresh_smoke(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=db)
        cache = tmp_path / "cache.json"
        # Pre-populate cache with a stale entry
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({
            "org/a--prov": {"ok": True, "latency_s": 1.0, "smoke_ts": time.time() - 3600},
        }))
        call_count = [0]

        def counting_runner(prompt, *, model, provider, timeout, **kw):
            call_count[0] += 1
            return 0, '{"ok": true}', ""

        benchmark.run(paths, days=7, smoke=True, smoke_runner=counting_runner,
                       config_path=config, cache_path=cache, ttl=1800)
        assert call_count[0] == 1  # stale → fresh call

    def test_missing_state_db_does_not_crash(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=tmp_path / "nope.db")
        report = benchmark.run(
            paths, days=7, smoke=False,
            config_path=config, cache_path=tmp_path / "cache.json",
        )
        assert report["ok"] is True
        assert report["state_db_found"] is False

    def test_smoke_failure_recorded(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=db)

        def bad_runner(prompt, *, model, provider, timeout, **kw):
            return 0, "not json at all", ""

        report = benchmark.run(
            paths, days=7, smoke=True, smoke_runner=bad_runner,
            config_path=config, cache_path=tmp_path / "cache.json",
        )
        smoke = report["per_model"][0]["smoke"]
        assert smoke["ok"] is False


# ---------------- Parallelism (jobs) ----------------

class TestParallelism:
    """Smoke and vision calls must actually overlap when jobs > 1.

    Each test uses a runner that sleeps a fixed duration, then
    asserts the wall-clock is well below the sequential sum.
    """

    def test_smoke_calls_run_in_parallel(self, tmp_path: Path) -> None:
        """N smoke calls at jobs=N finish in ~one delay, not N*delay."""
        db = tmp_path / "state.db"
        make_full_state_db(db)
        # 4 models so we can see a 4x speedup
        config = _write_config(tmp_path, {
            "model": {
                "default": "org/a",
                "aliases": {"b": "org/b", "c": "org/c", "d": "org/d"},
            },
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=db)
        delay = 0.4

        def slow_runner(prompt, *, model, provider, timeout, **kw):
            time.sleep(delay)
            return 0, '{"ok": true}', ""

        t0 = time.time()
        report = benchmark.run(
            paths, days=7, smoke=True, smoke_runner=slow_runner,
            config_path=config, cache_path=tmp_path / "cache.json",
            jobs=4,
        )
        elapsed = time.time() - t0

        assert report["smoke_calls_made"] == 4
        # Sequential would be ~1.6s (4 * 0.4). Parallel with 4
        # workers should be ~0.4s. Allow generous slack for CI /
        # scheduler jitter but prove the calls overlapped.
        assert elapsed < delay * 2, (
            f"smoke calls did not run in parallel: elapsed={elapsed:.2f}s, "
            f"expected < {delay * 2:.2f}s (4 calls x {delay}s at jobs=4)"
        )

    def test_vision_calls_run_in_parallel(self, tmp_path: Path, monkeypatch) -> None:
        """Vision fixture calls overlap when jobs covers them."""
        monkeypatch.setattr(benchmark, "_load_models_dev", _vision_models_dev)
        config = _write_config(tmp_path, {
            "model": {"default": "org/vision-model"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=tmp_path / "state.db")
        v_dir = _make_vision_fixtures(tmp_path)
        delay = 0.4

        def slow_vision(prompt, *, model, provider, timeout, image, **kw):
            time.sleep(delay)
            return _good_vision_runner_factory()(prompt, model=model,
                provider=provider, timeout=timeout, image=image)

        t0 = time.time()
        report = benchmark.run(
            paths, days=7, smoke=False, vision=True,
            vision_runner=slow_vision,
            config_path=config, cache_path=tmp_path / "cache.json",
            vision_fixtures_dir=v_dir,
            jobs=len(benchmark.VISION_FIXTURES),
        )
        elapsed = time.time() - t0

        n = len(benchmark.VISION_FIXTURES)
        assert report["vision_calls_made"] == n
        # Sequential would be n*delay; parallel with n workers
        # should be ~delay.
        assert elapsed < delay * 2, (
            f"vision calls did not run in parallel: elapsed={elapsed:.2f}s, "
            f"expected < {delay * 2:.2f}s ({n} calls x {delay}s at jobs={n})"
        )

    def test_jobs_one_is_sequential(self, tmp_path: Path) -> None:
        """jobs=1 restores the old sequential wall-clock."""
        db = tmp_path / "state.db"
        make_full_state_db(db)
        config = _write_config(tmp_path, {
            "model": {
                "default": "org/a",
                "aliases": {"b": "org/b"},
            },
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=db)
        delay = 0.3

        def slow_runner(prompt, *, model, provider, timeout, **kw):
            time.sleep(delay)
            return 0, '{"ok": true}', ""

        t0 = time.time()
        report = benchmark.run(
            paths, days=7, smoke=True, smoke_runner=slow_runner,
            config_path=config, cache_path=tmp_path / "cache.json",
            jobs=1,
        )
        elapsed = time.time() - t0

        assert report["smoke_calls_made"] == 2
        # 2 calls at jobs=1 -> ~0.6s, definitely > one delay.
        assert elapsed >= delay * 1.5, (
            f"jobs=1 did not run sequentially: elapsed={elapsed:.2f}s, "
            f"expected >= {delay * 1.5:.2f}s"
        )

    def test_jobs_in_report(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=tmp_path / "state.db")
        report = benchmark.run(
            paths, days=7, smoke=False,
            config_path=config, cache_path=tmp_path / "cache.json",
            jobs=3,
        )
        assert report["jobs"] == 3

    def test_default_jobs(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=tmp_path / "state.db")
        report = benchmark.run(
            paths, days=7, smoke=False,
            config_path=config, cache_path=tmp_path / "cache.json",
        )
        assert report["jobs"] == benchmark.DEFAULT_JOBS


# ---------------- Vision benchmark ----------------

class TestVisionMatching:
    def test_basic_match(self) -> None:
        missing = benchmark._match_vision_response(
            "red=4 total=10", ["red=4", "total=10"],
        )
        assert missing == []

    def test_case_insensitive(self) -> None:
        missing = benchmark._match_vision_response(
            "RED=4 Total=10", ["red=4", "total=10"],
        )
        assert missing == []

    def test_missing_returned(self) -> None:
        missing = benchmark._match_vision_response(
            "red=3 total=10", ["red=4", "total=10"],
        )
        assert "red=4" in missing

    def test_alternatives_any_match(self) -> None:
        missing = benchmark._match_vision_response(
            "icon=butterfly", ["wings|winged|sandal|butterfly"],
        )
        assert missing == []

    def test_alternatives_no_match(self) -> None:
        missing = benchmark._match_vision_response(
            "icon=rocket", ["wings|winged|sandal|butterfly"],
        )
        assert missing == ["wings|winged|sandal|butterfly"]


def _vision_models_dev() -> dict[str, dict[str, Any]]:
    """models_dev dict where ``org/vision-model`` has vision=True."""
    return {
        "vision-model": {
            "reasoning": False, "tool_call": True, "attachment": False,
            "modalities": {"input": ["text", "image"]},
        },
    }


def _make_vision_fixtures(tmp_path: Path) -> Path:
    """Create a minimal fixture dir with one PNG per VISION_FIXTURES entry."""
    from PIL import Image
    v_dir = tmp_path / "vision_fixtures"
    v_dir.mkdir()
    for img_rel, _, _, _ in benchmark.VISION_FIXTURES:
        p = v_dir / img_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (10, 10), "#000000").save(p)
    return v_dir


def _good_vision_runner_factory() -> Callable:
    """Returns a vision_runner stub that gives the correct answer per fixture.

    Detects which fixture is being asked by matching the prompt text
    against a keyword unique to each fixture's question.
    """
    def runner(prompt, *, model, provider, timeout, image, **kw):
        p = prompt.lower()
        # Order matters: "arrow" before "red" (the arrow prompt
        # contains "red arrow" which would match "red" first).
        if "arrow" in p:
            return 0, "box=B", ""
        if "red" in p:
            return 0, "red=4 total=10", ""
        if "error code" in p:
            return 0, "code=ERR_4042 module=agent.compression", ""
        if "logo" in p or "wordmark" in p:
            return 0, "word=TALARIA icon=wings colour=gold", ""
        return 0, "unknown", ""
    return runner


class TestVisionRun:
    def test_vision_model_tested_non_vision_skipped(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.setattr(benchmark, "_load_models_dev", _vision_models_dev)
        config = _write_config(tmp_path, {
            "model": {"default": "org/vision-model"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=tmp_path / "state.db")
        v_dir = _make_vision_fixtures(tmp_path)
        call_log: list[str] = []

        def vision_runner(prompt, *, model, provider, timeout, image, **kw):
            call_log.append(f"{model}:{image}")
            return _good_vision_runner_factory()(prompt, model=model,
                provider=provider, timeout=timeout, image=image)

        report = benchmark.run(
            paths, days=7, smoke=False, vision=True,
            vision_runner=vision_runner,
            config_path=config, cache_path=tmp_path / "cache.json",
            vision_fixtures_dir=v_dir,
        )
        assert report["vision_models"] == 1
        assert report["vision_calls_made"] == len(benchmark.VISION_FIXTURES)
        # The vision model has a vision result list in its entry.
        vis = report["per_model"][0]["vision"]
        assert vis is not None
        assert all(vf["ok"] for vf in vis)

    def test_no_vision_skips_calls(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(benchmark, "_load_models_dev", _vision_models_dev)
        config = _write_config(tmp_path, {
            "model": {"default": "org/vision-model"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=tmp_path / "state.db")
        v_dir = _make_vision_fixtures(tmp_path)

        def vision_runner(prompt, *, model, provider, timeout, image, **kw):
            raise AssertionError("vision_runner should not be called with --no-vision")

        report = benchmark.run(
            paths, days=7, smoke=False, vision=False,
            vision_runner=vision_runner,
            config_path=config, cache_path=tmp_path / "cache.json",
            vision_fixtures_dir=v_dir,
        )
        assert report["vision_enabled"] is False
        assert report["vision_calls_made"] == 0
        # Vision models are still discovered (so the report shows
        # how many WOULD be tested), but results are skipped.
        assert report["vision_models"] == 1
        vis = report["per_model"][0]["vision"]
        assert all(vf.get("skipped") for vf in vis)

    def test_vision_failure_recorded(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(benchmark, "_load_models_dev", _vision_models_dev)
        config = _write_config(tmp_path, {
            "model": {"default": "org/vision-model"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=tmp_path / "state.db")
        v_dir = _make_vision_fixtures(tmp_path)

        def bad_vision(prompt, *, model, provider, timeout, image, **kw):
            return 0, "wrong answer", ""

        report = benchmark.run(
            paths, days=7, smoke=False, vision=True,
            vision_runner=bad_vision,
            config_path=config, cache_path=tmp_path / "cache.json",
            vision_fixtures_dir=v_dir,
        )
        vis = report["per_model"][0]["vision"]
        assert all(not vf["ok"] for vf in vis)

    def test_vision_cached_within_ttl(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(benchmark, "_load_models_dev", _vision_models_dev)
        config = _write_config(tmp_path, {
            "model": {"default": "org/vision-model"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=tmp_path / "state.db")
        v_dir = _make_vision_fixtures(tmp_path)
        cache = tmp_path / "cache.json"
        call_count = [0]

        def counting_runner(prompt, *, model, provider, timeout, image, **kw):
            call_count[0] += 1
            return _good_vision_runner_factory()(prompt, model=model,
                provider=provider, timeout=timeout, image=image)

        benchmark.run(paths, days=7, smoke=False, vision=True,
                      vision_runner=counting_runner,
                      config_path=config, cache_path=cache,
                      vision_fixtures_dir=v_dir)
        first_calls = call_count[0]
        assert first_calls > 0
        benchmark.run(paths, days=7, smoke=False, vision=True,
                      vision_runner=counting_runner,
                      config_path=config, cache_path=cache,
                      vision_fixtures_dir=v_dir)
        assert call_count[0] == first_calls  # all cached

    def test_missing_fixture_dir_skipped(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(benchmark, "_load_models_dev", _vision_models_dev)
        config = _write_config(tmp_path, {
            "model": {"default": "org/vision-model"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=tmp_path / "state.db")
        report = benchmark.run(
            paths, days=7, smoke=False, vision=True,
            config_path=config, cache_path=tmp_path / "cache.json",
            vision_fixtures_dir=tmp_path / "nonexistent",
        )
        assert report["vision_dir_found"] is False
        vis = report["per_model"][0]["vision"]
        assert all(vf.get("skipped") for vf in vis)


class TestVisionRenderer:
    def test_vision_results_shown(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(benchmark, "_load_models_dev", _vision_models_dev)
        config = _write_config(tmp_path, {
            "model": {"default": "org/vision-model"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=tmp_path / "state.db")
        v_dir = _make_vision_fixtures(tmp_path)

        def good_vision(prompt, *, model, provider, timeout, image, **kw):
            return _good_vision_runner_factory()(prompt, model=model,
                provider=provider, timeout=timeout, image=image)

        report = benchmark.run(
            paths, days=7, smoke=False, vision=True,
            vision_runner=good_vision,
            config_path=config, cache_path=tmp_path / "cache.json",
            vision_fixtures_dir=v_dir,
        )
        code, text = benchmark.render_human(report)
        assert code == 0
        assert "vision:" in text

    def test_vision_failure_changes_verdict(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.setattr(benchmark, "_load_models_dev", _vision_models_dev)
        config = _write_config(tmp_path, {
            "model": {"default": "org/vision-model"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=tmp_path / "state.db")
        v_dir = _make_vision_fixtures(tmp_path)

        def bad_vision(prompt, *, model, provider, timeout, image, **kw):
            return 0, "wrong answer", ""

        report = benchmark.run(
            paths, days=7, smoke=False, vision=True,
            vision_runner=bad_vision,
            config_path=config, cache_path=tmp_path / "cache.json",
            vision_fixtures_dir=v_dir,
        )
        code, text = benchmark.render_human(report)
        assert code == 1
        assert "FAIL" in text
        assert "VERDICT: at least one model failed" in text


# ---------------- Renderer ----------------

class TestRenderer:
    def test_clean_report(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=db)
        report = benchmark.run(
            paths, days=7, smoke=False,
            config_path=config, cache_path=tmp_path / "cache.json",
        )
        code, text = benchmark.render_human(report)
        assert code == 0
        assert "VERDICT: all models healthy." in text
        assert "org/a" in text

    def test_smoke_failure_shown(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        paths = _paths(tmp_path, state_db=db)

        def bad_runner(prompt, *, model, provider, timeout, **kw):
            return 1, "error output", ""

        report = benchmark.run(
            paths, days=7, smoke=True, smoke_runner=bad_runner,
            config_path=config, cache_path=tmp_path / "cache.json",
        )
        code, text = benchmark.render_human(report)
        assert code == 1
        assert "FAIL" in text
        assert "VERDICT: at least one model failed" in text

    def test_no_models_message(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {})
        paths = _paths(tmp_path, state_db=tmp_path / "state.db")
        report = benchmark.run(
            paths, days=7, smoke=False,
            config_path=config, cache_path=tmp_path / "cache.json",
        )
        code, text = benchmark.render_human(report)
        assert code == 0
        assert "no models discovered" in text


# ---------------- CLI subprocess ----------------

class TestCli:
    def test_help_renders(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "benchmark", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--days" in result.stdout
        assert "--ttl" in result.stdout
        assert "--no-smoke" in result.stdout
        assert "--no-vision" in result.stdout

    def test_show_resolution(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "benchmark",
             "--config", str(config),
             "--state-db", str(tmp_path / "nope.db"),
             "--show-resolution"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "discovered models" in result.stdout
        assert "org/a" in result.stdout

    def test_json_report_no_smoke(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        now = _now()
        make_full_state_db(db, sessions=[
            {"id": "s1", "source": "cli", "model": "org/a",
             "started_at": now, "input_tokens": 1000, "output_tokens": 500,
             "rewind_count": 0, "archived": 0, "message_count": 0, "api_call_count": 1,
             "reasoning_tokens": 0, "actual_cost_usd": 0.0},
        ])
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "benchmark",
             "--config", str(config),
             "--state-db", str(db),
             "--no-smoke", "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["per_model"][0]["model"] == "org/a"

    def test_quiet_suppresses_output(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        cache = tmp_path / "cache.json"
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "benchmark",
             "--config", str(config),
             "--state-db", str(db),
             "--cache", str(cache),
             "--no-smoke", "--quiet"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_default_prints_report(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        cache = tmp_path / "cache.json"
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "benchmark",
             "--config", str(config),
             "--state-db", str(db),
             "--cache", str(cache),
             "--no-smoke"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Model benchmark" in result.stdout
        assert "VERDICT" in result.stdout

    def test_no_vision_flag(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        make_full_state_db(db)
        config = _write_config(tmp_path, {
            "model": {"default": "org/a"},
            "provider": "prov",
        })
        cache = tmp_path / "cache.json"
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "benchmark",
             "--config", str(config),
             "--state-db", str(db),
             "--cache", str(cache),
             "--no-smoke", "--no-vision", "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["vision_enabled"] is False
        assert payload["vision_calls_made"] == 0
