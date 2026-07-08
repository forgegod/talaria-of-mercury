"""Tests for talaria.hermos.skill_index — shared reader for diagnose + prune."""

from __future__ import annotations

import json
from pathlib import Path

from talaria.hermos.skill_index import (
    LOCK_FILENAME,
    LOCK_SUBDIR,
    profile_lock_path,
    profile_skills_root,
    read_filesystem_skill_names,
    read_index,
    read_lock_skill_names,
    read_disabled_skill_names,
)
from talaria.paths import ResolvedPaths
from talaria.sync.yaml_io import dump_yaml


def _paths(root: Path, profile: str = "default") -> ResolvedPaths:
    state = root / "state.db" if profile == "default" else root / "profiles" / profile / "state.db"
    logs = root / "logs" if profile == "default" else root / "profiles" / profile / "logs"
    return ResolvedPaths(profile=profile, hermes_root=root, state_db=state, log_dir=logs)


def _profile_skills_root(root: Path, profile: str) -> Path:
    """The actual skills dir for *profile* (default vs named)."""
    if profile == "default":
        return root / "skills"
    return root / "profiles" / profile / "skills"


def _write_skill(
    root: Path,
    name: str,
    *,
    profile: str = "default",
    category: str | None = None,
) -> Path:
    """Create a fake skill directory with SKILL.md and return its path."""
    skills_root = _profile_skills_root(root, profile)
    if category:
        d = skills_root / category / name
    else:
        d = skills_root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    return d


def _write_lock(root: Path, names: list[str], *, profile: str = "default") -> Path:
    skills_root = _profile_skills_root(root, profile)
    hub = skills_root / LOCK_SUBDIR
    hub.mkdir(parents=True, exist_ok=True)
    lock = hub / LOCK_FILENAME
    installed = {
        n: {
            "source": "official",
            "identifier": f"official/{n}",
            "install_path": n,
            "files": ["SKILL.md"],
        }
        for n in names
    }
    lock.write_text(json.dumps({"version": 1, "installed": installed}))
    return lock


def _write_config(
    root: Path, disabled: list[str], *, profile: str = "default",
) -> Path:
    cfg = root / "config.yaml" if profile == "default" else root / "profiles" / profile / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(dump_yaml({"skills": {"disabled": disabled}}))
    return cfg


class TestProfileSkillsRoot:
    def test_default_profile_uses_root(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        paths = _paths(root, "default")
        assert profile_skills_root(paths) == root / "skills"

    def test_named_profile_uses_subdir(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        paths = _paths(root, "vc-client")
        assert profile_skills_root(paths) == root / "profiles" / "vc-client" / "skills"

    def test_lock_path_matches_skills_root(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        paths = _paths(root, "vc-client")
        assert profile_lock_path(paths) == (
            root / "profiles" / "vc-client" / "skills" / LOCK_SUBDIR / LOCK_FILENAME
        )


class TestReadFilesystemSkillNames:
    def test_missing_root_returns_empty(self, tmp_path: Path) -> None:
        assert read_filesystem_skill_names(tmp_path / "nope") == []

    def test_walk_finds_flat_and_category_skills(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "alpha")
        _write_skill(tmp_path, "beta", category="devops")
        _write_skill(tmp_path, "gamma", category="devops")
        assert read_filesystem_skill_names(tmp_path) == ["alpha", "beta", "gamma"]

    def test_hub_directory_is_excluded(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "alpha")
        hub = tmp_path / LOCK_SUBDIR
        hub.mkdir()
        (hub / "SKILL.md").write_text("---\nname: lock\n---\n")
        assert read_filesystem_skill_names(tmp_path) == ["alpha"]


class TestReadLockSkillNames:
    def test_missing_lock_returns_empty(self, tmp_path: Path) -> None:
        assert read_lock_skill_names(tmp_path / "missing.json") == []

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        bad = tmp_path / "lock.json"
        bad.write_text("not json {")
        assert read_lock_skill_names(bad) == []

    def test_reads_sorted_names(self, tmp_path: Path) -> None:
        lock = _write_lock(tmp_path, ["zeta", "alpha", "mu"])
        assert read_lock_skill_names(lock) == ["alpha", "mu", "zeta"]


class TestReadDisabledSkillNames:
    def test_missing_config_returns_empty(self, tmp_path: Path) -> None:
        assert read_disabled_skill_names(tmp_path / "missing.yaml") == []

    def test_absent_disabled_key_returns_empty(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({"other": 1}))
        assert read_disabled_skill_names(cfg) == []

    def test_reads_disabled_names(self, tmp_path: Path) -> None:
        cfg = _write_config(tmp_path, ["zeta", "alpha"])
        assert read_disabled_skill_names(cfg) == ["alpha", "zeta"]


class TestReadIndex:
    def test_all_three_clean_yields_no_drift(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        profile = "vc-client"
        _write_skill(root, "kanban-worker", profile=profile, category="devops")
        _write_skill(root, "kanban-orchestrator", profile=profile, category="devops")
        _write_lock(root, ["kanban-worker", "kanban-orchestrator"], profile=profile)
        _write_config(root, disabled=[], profile=profile)

        idx = read_index(_paths(root, profile))
        assert idx.profile == "vc-client"
        assert idx.filesystem == ["kanban-orchestrator", "kanban-worker"]
        assert idx.lock == ["kanban-orchestrator", "kanban-worker"]
        assert idx.disabled == []
        assert idx.filesystem_only == []
        assert idx.lock_only == []
        assert idx.disabled_orphans == []
        assert idx.disabled_present == []
        assert idx.has_drift is False

    def test_filesystem_only_detected(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        profile = "vc-client"
        # kanban-orchestrator on disk but not in lock (the original bug)
        _write_skill(root, "kanban-worker", profile=profile, category="devops")
        _write_skill(root, "kanban-orchestrator", profile=profile, category="devops")
        _write_lock(root, ["kanban-worker"], profile=profile)
        _write_config(root, disabled=[], profile=profile)

        idx = read_index(_paths(root, profile))
        assert idx.filesystem_only == ["kanban-orchestrator"]
        assert idx.lock_only == []
        assert idx.has_drift is True

    def test_lock_only_detected(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        profile = "vc-client"
        # lock entry survives a manual rm -rf
        _write_skill(root, "kanban-worker", profile=profile, category="devops")
        _write_lock(root, ["kanban-worker", "phantom-skill"], profile=profile)
        _write_config(root, disabled=[], profile=profile)

        idx = read_index(_paths(root, profile))
        assert idx.filesystem_only == []
        assert idx.lock_only == ["phantom-skill"]
        assert idx.has_drift is True

    def test_disabled_orphans_detected(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        profile = "vc-client"
        _write_skill(root, "real", profile=profile, category="devops")
        _write_lock(root, ["real"], profile=profile)
        # 'gone' is in skills.disabled but not on disk and not in lock
        _write_config(root, disabled=["real", "gone"], profile=profile)

        idx = read_index(_paths(root, profile))
        assert idx.disabled_orphans == ["gone"]
        assert idx.disabled_present == ["real"]
        assert idx.has_drift is True

    def test_all_three_classes_simultaneously(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        profile = "vc-client"
        _write_skill(root, "alpha", profile=profile, category="devops")
        _write_skill(root, "beta", profile=profile, category="devops")
        _write_lock(root, ["beta", "phantom"], profile=profile)
        _write_config(root, disabled=["beta", "gone"], profile=profile)

        idx = read_index(_paths(root, profile))
        assert idx.filesystem_only == ["alpha"]
        assert idx.lock_only == ["phantom"]
        assert idx.disabled_orphans == ["gone"]
        assert idx.disabled_present == ["beta"]
        assert idx.has_drift is True

    def test_missing_skills_root_is_clean(self, tmp_path: Path) -> None:
        """A fresh profile with no skills directory is not an anomaly."""
        root = tmp_path / ".hermes"
        root.mkdir()
        idx = read_index(_paths(root, "vc-client"))
        assert idx.filesystem == []
        assert idx.lock == []
        assert idx.disabled == []
        assert idx.has_drift is False