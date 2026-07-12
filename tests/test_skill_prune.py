"""Tests for talaria.hermes.skill_prune — write side of the skill-index reconcile.

The prune tool removes drift between the filesystem walk, lock.json,
and skills.disabled. Tests cover the three prune classes plus the
no-flag no-op path and the dry-run vs apply distinction.
"""

from __future__ import annotations

import json
from pathlib import Path

from talaria.hermes.skill_index import LOCK_FILENAME, LOCK_SUBDIR
from talaria.hermes import skill_prune
from talaria.paths import ResolvedPaths
from talaria.sync.yaml_io import dump_yaml, load_yaml


def _paths(root: Path, profile: str = "default") -> ResolvedPaths:
    state = root / "state.db" if profile == "default" else root / "profiles" / profile / "state.db"
    logs = root / "logs" if profile == "default" else root / "profiles" / profile / "logs"
    return ResolvedPaths(profile=profile, hermes_root=root, state_db=state, log_dir=logs)


def _skills_root(root: Path, profile: str) -> Path:
    return root / "skills" if profile == "default" else root / "profiles" / profile / "skills"


def _write_skill(root: Path, name: str, *, profile: str = "default", category: str | None = None) -> Path:
    sroot = _skills_root(root, profile)
    d = sroot / category / name if category else sroot / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    return d


def _write_lock(root: Path, names: list[str], *, profile: str = "default") -> Path:
    sroot = _skills_root(root, profile)
    hub = sroot / LOCK_SUBDIR
    hub.mkdir(parents=True, exist_ok=True)
    lock = hub / LOCK_FILENAME
    installed = {n: {"source": "official", "install_path": n} for n in names}
    lock.write_text(json.dumps({"version": 1, "installed": installed}))
    return lock


def _write_config(root: Path, disabled: list[str], *, profile: str = "default") -> Path:
    cfg = root / "config.yaml" if profile == "default" else root / "profiles" / profile / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(dump_yaml({"skills": {"disabled": disabled}}))
    return cfg


class TestRunNoOp:
    def test_no_prune_flags_is_noop(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        profile = "vc-client"
        _write_skill(root, "alpha", profile=profile, category="devops")
        _write_lock(root, ["alpha"], profile=profile)
        _write_config(root, disabled=[], profile=profile)

        report = skill_prune.run(_paths(root, profile))
        assert report.prune_filesystem_only == ()
        assert report.prune_lock_only == ()
        assert report.prune_disabled_orphans == ()
        assert report.deleted_dirs == ()
        assert report.lock_backups == ()
        assert report.config_backups == ()
        assert report.dry_run is True
        assert report.any_action is False

    def test_clean_index_with_no_flags_is_still_noop(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        report = skill_prune.run(_paths(root, "vc-client"))
        assert report.any_action is False


class TestPruneFilesystemOnly:
    def test_dry_run_does_not_delete(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        profile = "vc-client"
        alpha = _write_skill(root, "alpha", profile=profile, category="devops")
        _write_lock(root, [], profile=profile)
        _write_config(root, disabled=[], profile=profile)

        report = skill_prune.run(
            _paths(root, profile),
            prune_filesystem_only=True,
            apply=False,
        )
        assert report.prune_filesystem_only == ("alpha",)
        assert report.deleted_dirs == ()
        assert report.dry_run is True
        assert alpha.exists()

    def test_apply_deletes_directories(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        profile = "vc-client"
        alpha = _write_skill(root, "alpha", profile=profile, category="devops")
        beta = _write_skill(root, "beta", profile=profile, category="devops")
        _write_lock(root, ["beta"], profile=profile)
        _write_config(root, disabled=[], profile=profile)

        report = skill_prune.run(
            _paths(root, profile),
            prune_filesystem_only=True,
            apply=True,
        )
        assert report.prune_filesystem_only == ("alpha",)
        assert report.deleted_dirs == ("alpha",)
        assert not alpha.exists()
        # beta is in the lock — must not be touched
        assert beta.exists()

    def test_apply_removes_both_flat_and_category_hits(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        profile = "vc-client"
        flat = _write_skill(root, "flat-skill", profile=profile)
        nested = _write_skill(root, "nested-skill", profile=profile, category="devops")
        _write_lock(root, [], profile=profile)

        report = skill_prune.run(
            _paths(root, profile),
            prune_filesystem_only=True,
            apply=True,
        )
        assert sorted(report.deleted_dirs) == ["flat-skill", "nested-skill"]
        assert not flat.exists()
        assert not nested.exists()


class TestPruneLockOnly:
    def test_apply_removes_lock_entries(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        profile = "vc-client"
        _write_skill(root, "real", profile=profile, category="devops")
        lock = _write_lock(root, ["real", "phantom"], profile=profile)
        _write_config(root, disabled=[], profile=profile)

        report = skill_prune.run(
            _paths(root, profile),
            prune_lock_only=True,
            apply=True,
        )
        assert report.prune_lock_only == ("phantom",)
        assert len(report.lock_backups) == 1
        data = json.loads(lock.read_text())
        assert "phantom" not in data["installed"]
        assert "real" in data["installed"]

    def test_no_backup_skips_bak_file(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        profile = "vc-client"
        _write_skill(root, "real", profile=profile, category="devops")
        lock = _write_lock(root, ["real", "phantom"], profile=profile)
        _write_config(root, disabled=[], profile=profile)

        report = skill_prune.run(
            _paths(root, profile),
            prune_lock_only=True,
            apply=True,
            no_backup=True,
        )
        assert report.lock_backups == ()
        assert not lock.with_suffix(lock.suffix + ".bak").exists()


class TestPruneDisabledOrphans:
    def test_apply_removes_disabled_orphans(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        profile = "vc-client"
        _write_skill(root, "real", profile=profile, category="devops")
        _write_lock(root, ["real"], profile=profile)
        cfg = _write_config(root, disabled=["real", "gone"], profile=profile)

        report = skill_prune.run(
            _paths(root, profile),
            prune_disabled_orphans=True,
            apply=True,
        )
        assert report.prune_disabled_orphans == ("gone",)
        assert len(report.config_backups) == 1
        # 'real' is still in disabled — it IS on disk, must not be touched
        assert load_yaml(cfg)["skills"]["disabled"] == ["real"]


class TestRenderer:
    def test_no_action_returns_zero(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        report = skill_prune.run(_paths(root, "vc-client"))
        exit_code, text = skill_prune.render_human(report)
        assert exit_code == 0
        assert "nothing to do" in text.lower()

    def test_dry_run_returns_zero(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        profile = "vc-client"
        _write_skill(root, "alpha", profile=profile, category="devops")
        report = skill_prune.run(
            _paths(root, profile),
            prune_filesystem_only=True,
            apply=False,
        )
        exit_code, text = skill_prune.render_human(report)
        assert exit_code == 0
        assert "Dry run" in text

    def test_apply_with_action_returns_one(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        profile = "vc-client"
        _write_skill(root, "alpha", profile=profile, category="devops")
        report = skill_prune.run(
            _paths(root, profile),
            prune_filesystem_only=True,
            apply=True,
            no_backup=True,
        )
        exit_code, _text = skill_prune.render_human(report)
        assert exit_code == 1