"""Tests for talaria.hermes.skill_uninstall."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from talaria.hermes import skill_install, skill_uninstall
from talaria.paths import ResolvedPaths
from talaria.sync.yaml_io import dump_yaml, load_yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _paths(root: Path, profile: str = "default") -> ResolvedPaths:
    state = root / "state.db" if profile == "default" else root / "profiles" / profile / "state.db"
    logs = root / "logs" if profile == "default" else root / "profiles" / profile / "logs"
    return ResolvedPaths(profile=profile, hermes_root=root, state_db=state, log_dir=logs)


class TestCleanupDisabledPolicy:
    def test_removes_uninstalled_names_from_disabled(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({"skills": {"disabled": ["a", "b", "old"]}}))
        uninstalled = [
            skill_uninstall.UninstallResult("skills-sh/x/y/a", "a", 0),
            skill_uninstall.UninstallResult("skills-sh/x/y/b", "b", 0),
        ]

        report = skill_uninstall.cleanup_disabled_policy(cfg, uninstalled, apply=True, no_backup=True)

        assert report["removed_from_disabled"] == ["a", "b"]
        # 'old' (not uninstalled) is preserved
        assert load_yaml(cfg)["skills"]["disabled"] == ["old"]

    def test_failed_uninstalls_are_not_removed(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({"skills": {"disabled": ["a", "b"]}}))
        # Caller filters to ok only; verify a non-ok result passed in is ignored
        uninstalled = [
            skill_uninstall.UninstallResult("skills-sh/x/y/a", "a", 0),
            skill_uninstall.UninstallResult("skills-sh/x/y/b", "b", 1),
        ]
        ok_only = [r for r in uninstalled if r.ok]

        report = skill_uninstall.cleanup_disabled_policy(cfg, ok_only, apply=True, no_backup=True)

        assert report["removed_from_disabled"] == ["a"]
        assert load_yaml(cfg)["skills"]["disabled"] == ["b"]

    def test_missing_skills_key_is_created(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({"other": 1}))
        uninstalled = [skill_uninstall.UninstallResult("skills-sh/x/y/a", "a", 0)]

        report = skill_uninstall.cleanup_disabled_policy(cfg, uninstalled, apply=True, no_backup=True)

        # Nothing to remove (disabled list was absent/empty), but no crash
        assert report["removed_from_disabled"] == []
        assert load_yaml(cfg)["skills"]["disabled"] == []


class TestRun:
    def test_default_uninstaller_uses_hermes_profile_env_and_name(self, monkeypatch, tmp_path: Path) -> None:
        captured = {}

        class Proc:
            returncode = 0
            stdout = "Uninstalled"
            stderr = ""

        def fake_run(cmd, text, capture_output, check, env, input=None):
            captured["cmd"] = cmd
            captured["env_home"] = env.get("HERMES_HOME")
            captured["input"] = input
            return Proc()

        monkeypatch.setattr(skill_uninstall.subprocess, "run", fake_run)

        result = skill_uninstall.default_uninstaller(
            "skills-sh/x/y/a",
            _paths(tmp_path / ".hermes", profile="vc-client"),
        )

        assert result.ok is True
        # uninstall takes a NAME, not an identifier
        assert captured["cmd"] == ["hermes", "skills", "uninstall", "a"]
        assert captured["env_home"] == str(tmp_path / ".hermes" / "profiles" / "vc-client")
        # confirmation is fed non-interactively
        assert captured["input"] == "y"

    def test_cancelled_prompt_is_detected_as_failure(self, monkeypatch, tmp_path: Path) -> None:
        # Hermes exits 0 on several non-success conditions; we must detect
        # failure markers in stdout and convert to a non-zero rc.
        class Proc:
            returncode = 0
            stdout = "Uninstall 'a'?\nConfirm [y/N]: Cancelled.\n"
            stderr = ""

        def fake_run(cmd, text, capture_output, check, env, input=None):
            return Proc()

        monkeypatch.setattr(skill_uninstall.subprocess, "run", fake_run)

        result = skill_uninstall.default_uninstaller(
            "skills-sh/x/y/a",
            _paths(tmp_path / ".hermes"),
        )

        assert result.ok is False
        assert result.returncode == 1

    def test_not_installed_error_is_detected_as_failure(self, monkeypatch, tmp_path: Path) -> None:
        # Hermes prints "Error: ... not a hub-installed skill" but exits 0
        class Proc:
            returncode = 0
            stdout = "Error: 'a' is not a hub-installed skill (may be a builtin)\n"
            stderr = ""

        def fake_run(cmd, text, capture_output, check, env, input=None):
            return Proc()

        monkeypatch.setattr(skill_uninstall.subprocess, "run", fake_run)

        result = skill_uninstall.default_uninstaller(
            "skills-sh/x/y/a",
            _paths(tmp_path / ".hermes"),
        )

        assert result.ok is False
        assert result.returncode == 1

    def test_dry_run_does_not_call_uninstaller_or_write_config(self, monkeypatch, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        cfg = root / "config.yaml"
        before = dump_yaml({"skills": {"disabled": ["a", "b", "old"]}})
        cfg.write_text(before)
        monkeypatch.setattr(
            skill_install,
            "expand_recursive_identifier",
            lambda ident: ["skills-sh/x/y/a", "skills-sh/x/y/b"],
        )

        def boom(*args, **kwargs):
            raise AssertionError("uninstaller should not run in dry-run mode")

        report = skill_uninstall.run(
            _paths(root),
            identifier="skills-sh/x/y/*",
            apply=False,
            uninstaller=boom,
        )

        assert report["ok"] is True
        assert report["dry_run"] is True
        # dry-run computes the would-be-removed names from the config
        assert report["removed_from_disabled"] == ["a", "b"]
        assert cfg.read_text() == before

    def test_runs_uninstaller_for_each_expanded_skill_and_cleans_disabled(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        root = tmp_path / ".hermes"
        profile_dir = root / "profiles" / "vc-client"
        profile_dir.mkdir(parents=True)
        (profile_dir / "config.yaml").write_text(
            dump_yaml({"skills": {"disabled": ["a", "b", "old"]}}),
        )
        monkeypatch.setattr(
            skill_install,
            "expand_recursive_identifier",
            lambda ident: ["skills-sh/x/y/a", "skills-sh/x/y/b"],
        )
        calls = []

        def fake_uninstaller(identifier, paths):
            name = skill_install.skill_name_from_identifier(identifier)
            calls.append((identifier, paths.profile, name))
            return skill_uninstall.UninstallResult(identifier, name, 0)

        report = skill_uninstall.run(
            _paths(root, profile="vc-client"),
            identifier="skills-sh/x/y/*",
            no_backup=True,
            uninstaller=fake_uninstaller,
        )

        assert report["ok"] is True
        assert calls == [
            ("skills-sh/x/y/a", "vc-client", "a"),
            ("skills-sh/x/y/b", "vc-client", "b"),
        ]
        assert sorted(report["removed_from_disabled"]) == ["a", "b"]
        # 'old' is preserved
        assert load_yaml(profile_dir / "config.yaml")["skills"]["disabled"] == ["old"]

    def test_partial_failure_still_cleans_successful_skills(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        cfg = root / "config.yaml"
        cfg.write_text(dump_yaml({"skills": {"disabled": ["a", "b", "old"]}}))
        monkeypatch.setattr(
            skill_install,
            "expand_recursive_identifier",
            lambda ident: ["skills-sh/x/y/a", "skills-sh/x/y/b"],
        )

        def fake_uninstaller(identifier, paths):
            name = skill_install.skill_name_from_identifier(identifier)
            rc = 0 if name == "a" else 1
            return skill_uninstall.UninstallResult(identifier, name, rc)

        report = skill_uninstall.run(
            _paths(root),
            identifier="skills-sh/x/y/*",
            no_backup=True,
            uninstaller=fake_uninstaller,
        )

        # one uninstall failed → ok is False
        assert report["ok"] is False
        # but the successful one (a) was still cleaned from disabled
        assert report["removed_from_disabled"] == ["a"]
        assert load_yaml(cfg)["skills"]["disabled"] == ["b", "old"]


class TestCli:
    def test_install_help_under_skills_group(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "skills", "install", "--help"],
            cwd=REPO_ROOT, text=True, capture_output=True, check=False,
        )
        assert proc.returncode == 0
        assert "--force-enable" in proc.stdout

    def test_uninstall_help_under_skills_group(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "skills", "uninstall", "--help"],
            cwd=REPO_ROOT, text=True, capture_output=True, check=False,
        )
        assert proc.returncode == 0
        assert "--dry-run" in proc.stdout
        # uninstall has no --force / --enable flags
        assert "--force" not in proc.stdout
