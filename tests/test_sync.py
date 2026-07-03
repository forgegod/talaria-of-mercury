"""Tests for ``talaria.sync`` — profile-to-profile artefact copying.

Layout per phase:

* class-level ``setup`` writes a small source tree and a target
  tree (often empty) into ``tmp_path`` so each test starts from a
  known state.
* methods exercise the phase function directly *and* via the CLI
  using ``subprocess.run`` so we cover both surfaces.

The CLI tests follow the project convention: invoke via
``python -m talaria.cli`` with ``cwd`` pointing at the repo root so
the installed package is importable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from talaria.sync import (
    SyncOptions,
    SyncProfile,
    list_profiles,
    resolve_profile,
    run_sync,
)
from talaria.sync.config import sync_config
from talaria.sync.context_cache import sync_context_cache
from talaria.sync.env import sync_env
from talaria.sync.skills import sync_skills
from talaria.sync.soul import sync_soul
from talaria.sync.dotpath import (
    del_path,
    get_path,
    list_keys,
    set_path,
    sync_exclude,
    sync_only,
)
from talaria.sync.yaml_io import dump_yaml, load_yaml, validate_yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------- Profile-resolver fixtures ----------
HERMES = ".hermes"
"""Subdirectory under ``$HOME`` that sync treats as the Hermes root.

Mirrors the production layout (``~/.hermes/``) so a single
``tmp_path`` fixture can be used both for in-process tests
(``resolve_profile(..., root=tmp_path / HERMES)``) and CLI
subprocess tests (``env={"HOME": tmp_path}``), where the
subprocess computes its root as ``$HOME/.hermes``.
"""


def _make_profile(home: Path, name: str) -> Path:
    """Create a profile at ``<home>/.hermes/profiles/<name>``.

    Both in-process tests and CLI subprocess tests pass the same
    ``home`` value (a ``tmp_path`` fixture). In-process tests then
    pass ``root=home / HERMES`` to :func:`resolve_profile`; CLI
    subprocess tests set ``env={"HOME": str(home)}`` so the
    subprocess's :func:`hermes_root` returns the same path.

    A minimal ``config.yaml`` is written so :func:`resolve_profile`
    can locate the profile. Tests that want a different config
    content should call :func:`_make_source` instead, or overwrite
    the file after the fact.
    """
    profile_dir = home / HERMES / "profiles" / name
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "config.yaml").write_text("model: {}\n")
    return profile_dir


def _make_source(home: Path, name: str, config: dict | None = None) -> Path:
    """Write a populated ``config.yaml`` into a profile directory and return it.

    Used by tests that need the source to have a non-trivial YAML
    shape (e.g. to exercise ``--exclude`` against real keys).
    """
    profile_dir = _make_profile(home, name)
    (profile_dir / "config.yaml").write_text(dump_yaml(config or {"model": {}, "agent": {"name": "hermes"}}))
    return profile_dir


# ---------- Dot-path helpers ----------
class TestDotPath:
    def test_get_path_top_level(self) -> None:
        ok, val = get_path({"a": 1}, "a")
        assert ok is True
        assert val == 1

    def test_get_path_nested(self) -> None:
        ok, val = get_path({"a": {"b": {"c": 42}}}, "a.b.c")
        assert ok is True
        assert val == 42

    def test_get_path_missing(self) -> None:
        ok, val = get_path({"a": 1}, "b")
        assert ok is False
        assert val is None

    def test_get_path_intermediate_missing(self) -> None:
        ok, val = get_path({"a": 1}, "a.b.c")
        assert ok is False

    def test_set_path_creates_intermediates(self) -> None:
        data: dict = {}
        set_path(data, "a.b.c", 1)
        assert data == {"a": {"b": {"c": 1}}}

    def test_set_path_overwrites_scalar(self) -> None:
        data = {"a": {"b": 1}}
        set_path(data, "a.b", 2)
        assert data["a"]["b"] == 2

    def test_del_path_removes(self) -> None:
        data = {"a": {"b": 1, "c": 2}}
        assert del_path(data, "a.b") is True
        assert data == {"a": {"c": 2}}

    def test_del_path_cleans_empty_parents(self) -> None:
        data = {"a": {"b": {"c": 1}}}
        assert del_path(data, "a.b.c") is True
        assert data == {}

    def test_del_path_missing_returns_false(self) -> None:
        assert del_path({"a": 1}, "b.c") is False

    def test_list_keys_depth_one(self) -> None:
        paths = list_keys({"a": 1, "b": {"c": 2}}, max_depth=1)
        assert paths == ["a", "b"]

    def test_list_keys_depth_two(self) -> None:
        paths = list_keys({"a": 1, "b": {"c": 2}}, max_depth=2)
        assert paths == ["a", "b", "b.c"]

    def test_sync_exclude_keeps_target_value(self) -> None:
        source = {"model": {"name": "a"}, "agent": {"x": 1}}
        target = {"model": {"name": "b"}, "extra_top": "kept"}
        result = sync_exclude(source, target, ["model.name"])
        assert result["model"]["name"] == "b"  # target value kept
        # 'x' was never in either model; should be absent.
        assert "x" not in result["model"]
        assert result["agent"]["x"] == 1  # from source
        # target-only top-level keys are preserved
        assert result["extra_top"] == "kept"

    def test_sync_only_copies_listed(self) -> None:
        source = {"model": {"name": "a"}, "agent": {"x": 1}}
        target: dict = {"existing": 1}
        result = sync_only(source, target, ["model.name"])
        assert result["model"]["name"] == "a"
        assert "agent" not in result
        assert result["existing"] == 1


# ---------- YAML I/O ----------
class TestYamlIO:
    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        assert load_yaml(tmp_path / "no.yaml") == {}

    def test_dump_round_trip(self) -> None:
        data = {"a": 1, "b": {"c": [1, 2, 3]}}
        text = dump_yaml(data)
        assert load_yaml_text(text) == data

    def test_validate_yaml_accepts_good(self) -> None:
        ok, err = validate_yaml("a: 1\n")
        assert ok is True
        assert err is None

    def test_validate_yaml_rejects_garbage(self) -> None:
        ok, err = validate_yaml("a: [unclosed")
        assert ok is False
        assert err is not None


def load_yaml_text(text: str) -> dict:
    """Tiny local helper so the round-trip test does not import yaml twice."""
    import yaml
    return yaml.safe_load(text)


# ---------- resolve_profile / list_profiles ----------
class TestResolveProfile:
    def test_default_alias(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / HERMES / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / HERMES / "config.yaml").write_text("model: {}\n")
        monkeypatch.setattr("talaria.sync.paths.HERMES_ROOT", tmp_path / HERMES)
        profile = resolve_profile("default", root=tmp_path / HERMES)
        assert profile.name == "default"
        assert profile.config_yaml == tmp_path / HERMES / "config.yaml"

    def test_named_profile(self, tmp_path: Path) -> None:
        _make_profile(tmp_path, "hermes-vc")
        profile = resolve_profile("hermes-vc", root=tmp_path / HERMES)
        assert profile.name == "hermes-vc"
        assert profile.config_yaml == tmp_path / HERMES / "profiles" / "hermes-vc" / "config.yaml"

    def test_file_path(self, tmp_path: Path) -> None:
        # resolve_profile accepts any path to a config.yaml file
        # (not just paths named exactly that).
        profile_dir = tmp_path / "custom-profile"
        profile_dir.mkdir()
        cfg = profile_dir / "config.yaml"
        cfg.write_text("model: {}\n")
        profile = resolve_profile(str(cfg))
        assert profile.config_yaml == cfg.resolve()

    def test_missing_profile_lists_available(self, tmp_path: Path) -> None:
        _make_profile(tmp_path, "alpha")
        with pytest.raises(FileNotFoundError) as exc:
            resolve_profile("ghost", root=tmp_path / HERMES)
        assert "alpha" in str(exc.value)

    def test_list_profiles(self, tmp_path: Path) -> None:
        (tmp_path / HERMES / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / HERMES / "config.yaml").write_text("model: {}\n")
        _make_profile(tmp_path, "a")
        _make_profile(tmp_path, "b")
        names = list_profiles(root=tmp_path / HERMES)
        assert names == ["default", "a", "b"]


# ---------- config phase ----------
class TestConfigPhase:
    def test_exclude_keeps_target_value(self, tmp_path: Path) -> None:
        _make_source(tmp_path, "src", {"model": {"name": "src-val"}, "agent": {"x": 1}})
        target_dir = _make_profile(tmp_path, "dst")
        (target_dir / "config.yaml").write_text(dump_yaml({"model": {"name": "dst-val"}, "agent": {"x": 99}}))
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_config(src, tgt, excludes=["model.name"], apply=True)
        merged = load_yaml(target_dir / "config.yaml")
        assert merged["model"]["name"] == "dst-val"  # target wins on excluded
        assert merged["agent"]["x"] == 1  # source supplies

    def test_only_copies_listed_paths(self, tmp_path: Path) -> None:
        _make_source(tmp_path, "src", {"model": {"name": "x"}, "agent": {"x": 1}})
        target_dir = _make_profile(tmp_path, "dst")
        (target_dir / "config.yaml").write_text(dump_yaml({"model": {"name": "dst"}, "extra": "kept"}))
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_config(src, tgt, only_paths=["model.name"], apply=True)
        merged = load_yaml(target_dir / "config.yaml")
        assert merged["model"]["name"] == "x"
        assert "agent" not in merged
        assert merged["extra"] == "kept"

    def test_exclude_and_only_mutually_exclusive(self, tmp_path: Path) -> None:
        _make_source(tmp_path, "src", {"model": {}})
        _make_profile(tmp_path, "dst")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        with pytest.raises(ValueError):
            sync_config(src, tgt, excludes=["model"], only_paths=["agent"])

    def test_in_sync_with_exclude_no_op(self, tmp_path: Path) -> None:
        cfg = {"model": {"name": "x"}}
        _make_source(tmp_path, "src", cfg)
        target_dir = _make_profile(tmp_path, "dst")
        (target_dir / "config.yaml").write_text(dump_yaml(cfg))
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_config(src, tgt, excludes=["model.name"], apply=True)
        assert result.status == "in_sync"
        assert result.write_confirmed is False

    def test_add_mcp_serve_idempotent(self, tmp_path: Path) -> None:
        _make_source(tmp_path, "src", {"model": {}})
        target_dir = _make_profile(tmp_path, "dst")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        first = sync_config(src, tgt, add_mcp_serve=True, apply=True)
        assert first.write_confirmed is True
        second = sync_config(src, tgt, add_mcp_serve=True, apply=True)
        assert second.status == "in_sync"
        merged = load_yaml(target_dir / "config.yaml")
        assert merged["mcp_servers"]["hermes"]["transport"] == "sse"


# ---------- soul phase ----------
class TestSoulPhase:
    def test_copies_new(self, tmp_path: Path) -> None:
        src_dir = _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        (src_dir / "SOUL.md").write_text("# Source soul\n")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_soul(src, tgt, apply=True)
        assert result.status == "new"
        assert (target_dir / "SOUL.md").read_text() == "# Source soul\n"

    def test_in_sync_no_op(self, tmp_path: Path) -> None:
        src_dir = _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        (src_dir / "SOUL.md").write_text("# Same\n")
        (target_dir / "SOUL.md").write_text("# Same\n")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_soul(src, tgt, apply=True)
        assert result.status == "in_sync"
        assert result.write_confirmed is False

    def test_update_with_backup(self, tmp_path: Path) -> None:
        src_dir = _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        (src_dir / "SOUL.md").write_text("# New\n")
        (target_dir / "SOUL.md").write_text("# Old\n")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_soul(src, tgt, apply=True)
        assert result.status == "updated"
        assert (target_dir / "SOUL.md").read_text() == "# New\n"
        assert (target_dir / "SOUL.md.bak").read_text() == "# Old\n"

    def test_source_missing_skipped(self, tmp_path: Path) -> None:
        _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        (target_dir / "SOUL.md").write_text("# Keep\n")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_soul(src, tgt, apply=True)
        assert result.status == "skipped"
        assert (target_dir / "SOUL.md").read_text() == "# Keep\n"


# ---------- skills phase ----------
class TestSkillsPhase:
    def _make_skill(self, root: Path, category: str, name: str, body: str = "# skill\n") -> None:
        skill_dir = root / "skills" / category / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(body)

    def test_copies_new_skill(self, tmp_path: Path) -> None:
        src_dir = _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        self._make_skill(src_dir, "github", "demo")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_skills(src, tgt, apply=True)
        assert result.new_count == 1
        assert result.status == "updated"
        assert (target_dir / "skills" / "github" / "demo" / "SKILL.md").exists()

    def test_skips_when_in_sync(self, tmp_path: Path) -> None:
        src_dir = _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        self._make_skill(src_dir, "github", "demo")
        self._make_skill(target_dir, "github", "demo")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_skills(src, tgt, apply=True)
        assert result.skipped == 1
        assert result.write_confirmed is False

    def test_updates_differing_skill(self, tmp_path: Path) -> None:
        src_dir = _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        self._make_skill(src_dir, "github", "demo", body="# new\n")
        self._make_skill(target_dir, "github", "demo", body="# old\n")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_skills(src, tgt, apply=True)
        assert result.copied == 1
        assert result.write_confirmed is True
        # backup made
        assert (target_dir / "skills" / "github" / "demo.bak" / "SKILL.md").exists()
        # new content in place
        assert (target_dir / "skills" / "github" / "demo" / "SKILL.md").read_text() == "# new\n"

    def test_category_filter(self, tmp_path: Path) -> None:
        src_dir = _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        self._make_skill(src_dir, "github", "demo")
        self._make_skill(src_dir, "devops", "other")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_skills(src, tgt, filters=["github"], apply=True)
        assert result.new_count == 1
        assert not (target_dir / "skills" / "devops").exists()

    def test_skill_path_filter(self, tmp_path: Path) -> None:
        src_dir = _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        self._make_skill(src_dir, "github", "wanted")
        self._make_skill(src_dir, "github", "unwanted")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_skills(src, tgt, filters=["github/wanted"], apply=True)
        assert result.new_count == 1
        assert (target_dir / "skills" / "github" / "wanted").exists()
        assert not (target_dir / "skills" / "github" / "unwanted").exists()

    def test_no_source_skills(self, tmp_path: Path) -> None:
        _make_profile(tmp_path, "src")
        _make_profile(tmp_path, "dst")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_skills(src, tgt, apply=True)
        assert result.status == "skipped"


# ---------- env phase ----------
class TestEnvPhase:
    def test_appends_new_vars(self, tmp_path: Path) -> None:
        src_dir = _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        (src_dir / ".env").write_text("FOO=1\nBAR=2\n")
        (target_dir / ".env").write_text("FOO=overridden\n")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_env(src, tgt, apply=True)
        assert "BAR" in result.new_vars
        assert "FOO" not in result.new_vars  # already in target
        assert "FOO" in result.preserved_vars
        target_text = (target_dir / ".env").read_text()
        assert "FOO=overridden" in target_text
        assert "BAR=2" in target_text

    def test_target_missing_copies_whole(self, tmp_path: Path) -> None:
        src_dir = _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        (src_dir / ".env").write_text("X=1\nY=2\n")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_env(src, tgt, apply=True)
        assert result.status == "new"
        assert (target_dir / ".env").read_text() == "X=1\nY=2\n"

    def test_no_new_vars_in_sync(self, tmp_path: Path) -> None:
        src_dir = _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        (src_dir / ".env").write_text("A=1\n")
        (target_dir / ".env").write_text("A=1\n")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_env(src, tgt, apply=True)
        assert result.status == "in_sync"
        assert result.write_confirmed is False

    def test_source_missing_skipped(self, tmp_path: Path) -> None:
        _make_profile(tmp_path, "src")
        _make_profile(tmp_path, "dst")
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_env(src, tgt, apply=True)
        assert result.status == "skipped"


# ---------- context_cache phase ----------
class TestContextCachePhase:
    def test_appends_new_keys(self, tmp_path: Path) -> None:
        src_dir = _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        (src_dir / "context_length_cache.yaml").write_text(
            dump_yaml({"context_lengths": {"new-model": 32000}})
        )
        (target_dir / "context_length_cache.yaml").write_text(
            dump_yaml({"context_lengths": {"existing": 8000}})
        )
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_context_cache(src, tgt, apply=True)
        assert "new-model" in result.new_keys
        merged = load_yaml(target_dir / "context_length_cache.yaml")
        assert merged["context_lengths"]["new-model"] == 32000
        assert merged["context_lengths"]["existing"] == 8000  # target-only preserved

    def test_source_wins_on_conflict(self, tmp_path: Path) -> None:
        src_dir = _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        (src_dir / "context_length_cache.yaml").write_text(
            dump_yaml({"context_lengths": {"m": 32000}})
        )
        (target_dir / "context_length_cache.yaml").write_text(
            dump_yaml({"context_lengths": {"m": 8000}})
        )
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_context_cache(src, tgt, apply=True)
        assert "m" in result.updated_keys
        merged = load_yaml(target_dir / "context_length_cache.yaml")
        assert merged["context_lengths"]["m"] == 32000

    def test_target_missing_copies_whole(self, tmp_path: Path) -> None:
        src_dir = _make_profile(tmp_path, "src")
        target_dir = _make_profile(tmp_path, "dst")
        (src_dir / "context_length_cache.yaml").write_text(
            dump_yaml({"context_lengths": {"x": 1}})
        )
        src = resolve_profile("src", root=tmp_path / HERMES)
        tgt = resolve_profile("dst", root=tmp_path / HERMES)
        result = sync_context_cache(src, tgt, apply=True)
        assert result.status == "new"
        assert (target_dir / "context_length_cache.yaml").exists()


# ---------- run_sync integration ----------
class TestRunSync:
    def _full_setup(self, tmp_path: Path) -> tuple[Path, Path]:
        """Build a source with all artefacts and an empty target. Return both dirs."""
        src = _make_profile(tmp_path, "src")
        dst = _make_profile(tmp_path, "dst")
        (src / "config.yaml").write_text(dump_yaml({"model": {"name": "x"}, "agent": {"x": 1}}))
        (src / "SOUL.md").write_text("# soul\n")
        (src / ".env").write_text("API_KEY=1\nEXTRA=2\n")
        (src / "context_length_cache.yaml").write_text(
            dump_yaml({"context_lengths": {"m": 32000}})
        )
        skill = src / "skills" / "github" / "demo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# demo\n")
        return src, dst

    def test_full_run_writes_everything(self, tmp_path: Path) -> None:
        src_dir, dst_dir = self._full_setup(tmp_path)
        # Excludes so the config phase actually runs (otherwise it
        # is a no-op when no --exclude / --only / --add-mcp-serve
        # is set; see sync_config docstring).
        options = SyncOptions(apply=True, excludes=["agent"])
        report = run_sync(
            resolve_profile("src", root=tmp_path / HERMES),
            resolve_profile("dst", root=tmp_path / HERMES),
            options,
        )
        assert report.ok is True
        assert report.any_writes is True
        assert report.config is not None and report.config.write_confirmed
        assert report.soul is not None and report.soul.write_confirmed
        assert report.skills is not None and report.skills.write_confirmed
        assert report.env is not None and report.env.write_confirmed
        assert report.context_cache is not None and report.context_cache.write_confirmed

    def test_skip_flags(self, tmp_path: Path) -> None:
        src_dir, dst_dir = self._full_setup(tmp_path)
        options = SyncOptions(
            apply=True,
            # Triggers the config phase (otherwise it's a no-op
            # when no --exclude / --only / --add-mcp-serve is set).
            excludes=["agent"],
            skip_soul=True,
            skip_skills=True,
            skip_env=True,
            skip_cache=True,
        )
        report = run_sync(
            resolve_profile("src", root=tmp_path / HERMES),
            resolve_profile("dst", root=tmp_path / HERMES),
            options,
        )
        assert report.soul is None
        assert report.skills is None
        assert report.env is None
        assert report.context_cache is None
        # config still ran (the excludes flag kept it active)
        assert report.config is not None

    def test_same_source_target_rejected(self, tmp_path: Path) -> None:
        _make_source(tmp_path, "src")
        src = resolve_profile("src", root=tmp_path / HERMES)
        with pytest.raises(ValueError):
            run_sync(src, src, SyncOptions())

    def test_exclude_and_only_rejected(self, tmp_path: Path) -> None:
        _make_source(tmp_path, "src")
        _make_profile(tmp_path, "dst")
        options = SyncOptions(excludes=["model"], only_paths=["agent"])
        with pytest.raises(ValueError):
            run_sync(
                resolve_profile("src", root=tmp_path / HERMES),
                resolve_profile("dst", root=tmp_path / HERMES),
                options,
            )


# ---------- CLI surface ----------
class TestSyncCLI:
    def _cli(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        """Run ``python -m talaria.cli sync ...`` and return the result."""
        return subprocess.run(
            [sys.executable, "-m", "talaria.cli", "sync", *args],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
            env=env,
        )

    def test_help_exits_zero(self) -> None:
        result = self._cli("--help")
        assert result.returncode == 0
        assert "sync" in result.stdout

    def test_missing_target_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_source(tmp_path, "src")
        monkeypatch.setattr("talaria.sync.paths.HERMES_ROOT", tmp_path)
        result = self._cli("src", env={"HOME": str(tmp_path)})
        # No target → error to stderr, exit 2
        assert result.returncode == 2
        assert "target" in result.stderr.lower()

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        _make_source(tmp_path, "src", {"model": {"name": "x"}, "agent": {"x": 1}})
        _make_profile(tmp_path, "dst")
        target_yaml = tmp_path / HERMES / "profiles" / "dst" / "config.yaml"
        original = dump_yaml({"model": {"name": "old"}, "agent": {"x": 9}})
        target_yaml.write_text(original)
        # --exclude triggers the config phase; --dry-run must NOT write.
        result = self._cli(
            "src", "dst", "-e", "model", "--dry-run",
            "--skip-soul", "--skip-skills", "--skip-env", "--skip-cache",
            env={"HOME": str(tmp_path)},
        )
        assert result.returncode == 0
        assert target_yaml.read_text() == original

    def test_apply_writes_and_json_exits_zero(self, tmp_path: Path) -> None:
        _make_source(tmp_path, "src", {"model": {"name": "x"}, "agent": {"x": 1}})
        _make_profile(tmp_path, "dst")
        result = self._cli(
            "src", "dst", "-e", "model", "--json",
            "--skip-soul", "--skip-skills", "--skip-env", "--skip-cache",
            env={"HOME": str(tmp_path)},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["any_writes"] is True
        assert payload["config"]["write_confirmed"] is True

    def test_list_paths(self, tmp_path: Path) -> None:
        _make_source(tmp_path, "src", {"model": {"name": "x"}, "agent": {"x": 1}})
        result = self._cli("src", "--list", env={"HOME": str(tmp_path)})
        assert result.returncode == 0
        assert "model" in result.stdout
        assert "agent" in result.stdout

    def test_exclude_flag_writes_target_value(self, tmp_path: Path) -> None:
        _make_source(tmp_path, "src", {"model": {"name": "src"}, "agent": {"x": 1}})
        target_dir = _make_profile(tmp_path, "dst")
        (target_dir / "config.yaml").write_text(dump_yaml({"model": {"name": "dst"}, "agent": {"x": 9}}))
        result = self._cli(
            "src", "dst", "-e", "model.name",
            "--skip-soul", "--skip-skills", "--skip-env", "--skip-cache",
            env={"HOME": str(tmp_path)},
        )
        assert result.returncode == 0
        merged = load_yaml(target_dir / "config.yaml")
        assert merged["model"]["name"] == "dst"  # target wins on excluded path
        assert merged["agent"]["x"] == 1  # source value for non-excluded