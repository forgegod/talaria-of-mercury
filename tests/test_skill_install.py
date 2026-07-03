"""Tests for talaria.hermos.skill_install."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from talaria.hermos import skill_install
from talaria.paths import ResolvedPaths
from talaria.sync.yaml_io import dump_yaml, load_yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _paths(root: Path, profile: str = "default") -> ResolvedPaths:
    state = root / "state.db" if profile == "default" else root / "profiles" / profile / "state.db"
    logs = root / "logs" if profile == "default" else root / "profiles" / profile / "logs"
    return ResolvedPaths(profile=profile, hermes_root=root, state_db=state, log_dir=logs)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestRecursiveExpansion:
    def test_skills_sh_repo_root_expands_child_skill_dirs(self, monkeypatch) -> None:
        def fake_urlopen(req, timeout=None):
            url = req.full_url
            if url == "https://api.github.com/repos/addyosmani/agent-skills":
                return _Response({"default_branch": "main"})
            assert url == "https://api.github.com/repos/addyosmani/agent-skills/git/trees/main?recursive=1"
            return _Response({"tree": [
                {"type": "blob", "path": "api-and-interface-design/SKILL.md"},
                {"type": "blob", "path": "code-review-and-quality/SKILL.md"},
                {"type": "blob", "path": "context-engineering/SKILL.md"},
                {"type": "blob", "path": ".hidden/SKILL.md"},
                {"type": "blob", "path": "context-engineering/references/x.md"},
            ]})

        monkeypatch.setattr(skill_install.urllib_request, "urlopen", fake_urlopen)

        assert skill_install.expand_recursive_identifier(
            "skills-sh/addyosmani/agent-skills/*"
        ) == [
            "skills-sh/addyosmani/agent-skills/api-and-interface-design",
            "skills-sh/addyosmani/agent-skills/code-review-and-quality",
            "skills-sh/addyosmani/agent-skills/context-engineering",
        ]

    def test_nested_path_expands_below_parent_only(self, monkeypatch) -> None:
        def fake_urlopen(req, timeout=None):
            if req.full_url.endswith("/repos/acme/skills"):
                return _Response({"default_branch": "trunk"})
            return _Response({"tree": [
                {"type": "blob", "path": "agent-skills/a/SKILL.md"},
                {"type": "blob", "path": "agent-skills/b/SKILL.md"},
                {"type": "blob", "path": "other/c/SKILL.md"},
            ]})

        monkeypatch.setattr(skill_install.urllib_request, "urlopen", fake_urlopen)

        assert skill_install.expand_recursive_identifier("acme/skills/agent-skills/*") == [
            "acme/skills/agent-skills/a",
            "acme/skills/agent-skills/b",
        ]


class TestDisabledPolicy:
    def test_default_policy_disables_all_installed_skills(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({"skills": {"disabled": ["old"]}}))
        installed = [
            skill_install.InstallResult("skills-sh/x/y/a", "a", 0),
            skill_install.InstallResult("skills-sh/x/y/b", "b", 0),
        ]

        report = skill_install.apply_disabled_policy(cfg, installed, apply=True, no_backup=True)

        assert report["enabled"] == []
        assert report["disabled"] == ["a", "b"]
        assert load_yaml(cfg)["skills"]["disabled"] == ["a", "b", "old"]

    def test_enable_selectors_enable_only_selected(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({"skills": {"disabled": ["a", "old"]}}))
        installed = [
            skill_install.InstallResult("skills-sh/x/y/a", "a", 0),
            skill_install.InstallResult("skills-sh/x/y/b", "b", 0),
            skill_install.InstallResult("skills-sh/x/y/c", "c", 0),
        ]

        report = skill_install.apply_disabled_policy(
            cfg,
            installed,
            enable=["skills-sh/x/y/a", "c"],
            apply=True,
            no_backup=True,
        )

        assert report["enabled"] == ["a", "c"]
        assert report["disabled"] == ["b"]
        assert load_yaml(cfg)["skills"]["disabled"] == ["b", "old"]

    def test_force_enable_removes_installed_from_disabled(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({"skills": {"disabled": ["a", "old"]}}))

        report = skill_install.apply_disabled_policy(
            cfg,
            [skill_install.InstallResult("skills-sh/x/y/a", "a", 0)],
            force_enable=True,
            apply=True,
            no_backup=True,
        )

        assert report["enabled"] == ["a"]
        assert report["disabled"] == []
        assert load_yaml(cfg)["skills"]["disabled"] == ["old"]


class TestRun:
    def test_default_installer_uses_hermes_profile_env(self, monkeypatch, tmp_path: Path) -> None:
        captured = {}

        class Proc:
            returncode = 0
            stdout = "installed"
            stderr = ""

        def fake_run(cmd, text, capture_output, check, env):
            captured["cmd"] = cmd
            captured["env_profile"] = env.get("HERMES_PROFILE")
            return Proc()

        monkeypatch.setattr(skill_install.subprocess, "run", fake_run)

        result = skill_install.default_installer(
            "skills-sh/x/y/a",
            _paths(tmp_path / ".hermes", profile="vc-client"),
            force=True,
        )

        assert result.ok is True
        assert captured["cmd"] == ["hermes", "skills", "install", "skills-sh/x/y/a", "--yes", "--force"]
        assert captured["env_profile"] == "vc-client"

    def test_dry_run_does_not_call_installer_or_write_config(self, monkeypatch, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        root.mkdir()
        cfg = root / "config.yaml"
        before = dump_yaml({"skills": {"disabled": ["old"]}})
        cfg.write_text(before)
        monkeypatch.setattr(
            skill_install,
            "expand_recursive_identifier",
            lambda ident: ["skills-sh/x/y/a", "skills-sh/x/y/b"],
        )

        def boom(*args, **kwargs):
            raise AssertionError("installer should not run in dry-run mode")

        report = skill_install.run(
            _paths(root),
            identifier="skills-sh/x/y/*",
            apply=False,
            installer=boom,
        )

        assert report["ok"] is True
        assert report["dry_run"] is True
        assert report["disabled"] == ["a", "b"]
        assert cfg.read_text() == before

    def test_runs_installer_for_each_expanded_skill_and_updates_named_profile_config(self, monkeypatch, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        profile_dir = root / "profiles" / "vc-client"
        profile_dir.mkdir(parents=True)
        (profile_dir / "config.yaml").write_text(dump_yaml({"skills": {"disabled": []}}))
        monkeypatch.setattr(
            skill_install,
            "expand_recursive_identifier",
            lambda ident: ["skills-sh/x/y/a", "skills-sh/x/y/b"],
        )
        calls = []

        def fake_installer(identifier, paths, force):
            calls.append((identifier, paths.profile, force))
            return skill_install.InstallResult(identifier, skill_install.skill_name_from_identifier(identifier), 0)

        report = skill_install.run(
            _paths(root, profile="vc-client"),
            identifier="skills-sh/x/y/*",
            force=True,
            enable=["a"],
            no_backup=True,
            installer=fake_installer,
        )

        assert report["ok"] is True
        assert calls == [("skills-sh/x/y/a", "vc-client", True), ("skills-sh/x/y/b", "vc-client", True)]
        assert report["enabled"] == ["a"]
        assert report["disabled"] == ["b"]
        assert load_yaml(profile_dir / "config.yaml")["skills"]["disabled"] == ["b"]


class TestCli:
    def test_help_exposes_enable_flags_and_typo_alias(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "install-skills-recursive", "--help"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        assert proc.returncode == 0
        assert "--force-enable" in proc.stdout
        assert "--force-enalbe" in proc.stdout
        assert "--enable" in proc.stdout
