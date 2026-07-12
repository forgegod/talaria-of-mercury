"""Tests for talaria.hermes.skill_category."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from talaria.hermes import skill_category
from talaria.paths import ResolvedPaths

REPO_ROOT = Path(__file__).resolve().parent.parent


def _paths(root: Path, profile: str = "default") -> ResolvedPaths:
    state = root / "state.db" if profile == "default" else root / "profiles" / profile / "state.db"
    logs = root / "logs" if profile == "default" else root / "profiles" / profile / "logs"
    return ResolvedPaths(profile=profile, hermes_root=root, state_db=state, log_dir=logs)


class TestValidateCategoryName:
    def test_accepts_simple_lowercase(self) -> None:
        assert skill_category.validate_category_name("software-development") == "software-development"

    def test_accepts_nested_with_slash(self) -> None:
        assert skill_category.validate_category_name("mlops/training") == "mlops/training"

    def test_accepts_digits_and_underscores(self) -> None:
        assert skill_category.validate_category_name("dev_ops-2") == "dev_ops-2"

    def test_rejects_empty(self) -> None:
        try:
            skill_category.validate_category_name("")
            raise AssertionError("should have raised")
        except skill_category.SkillCategoryError as exc:
            assert exc.kind == "config"

    def test_rejects_uppercase(self) -> None:
        try:
            skill_category.validate_category_name("Software-Development")
            raise AssertionError("should have raised")
        except skill_category.SkillCategoryError as exc:
            assert exc.kind == "config"

    def test_rejects_spaces(self) -> None:
        try:
            skill_category.validate_category_name("software development")
            raise AssertionError("should have raised")
        except skill_category.SkillCategoryError as exc:
            assert exc.kind == "config"

    def test_rejects_leading_digit(self) -> None:
        try:
            skill_category.validate_category_name("2things")
            raise AssertionError("should have raised")
        except skill_category.SkillCategoryError as exc:
            assert exc.kind == "config"


class TestSkillsDirPath:
    def test_default_profile(self, tmp_path: Path) -> None:
        sdir = skill_category.skills_dir_path(_paths(tmp_path / ".hermes"))
        assert sdir == tmp_path / ".hermes" / "skills"

    def test_named_profile(self, tmp_path: Path) -> None:
        sdir = skill_category.skills_dir_path(_paths(tmp_path / ".hermes", profile="vc-client"))
        assert sdir == tmp_path / ".hermes" / "profiles" / "vc-client" / "skills"


class TestCreateCategory:
    def test_creates_directory_and_description(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        report = skill_category.create_category(
            _paths(root),
            "software-development",
            description="Software engineering workflows and tools.",
        )

        assert report["ok"] is True
        assert report["created"] is True
        assert report["description_written"] is True
        cat_dir = root / "skills" / "software-development"
        assert cat_dir.is_dir()
        desc = cat_dir / "DESCRIPTION.md"
        assert desc.is_file()
        content = desc.read_text()
        assert "---" in content
        assert "Software engineering workflows and tools." in content

    def test_creates_directory_without_description(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        report = skill_category.create_category(_paths(root), "misc")

        assert report["ok"] is True
        assert report["created"] is True
        assert report["description_written"] is False
        assert (root / "skills" / "misc").is_dir()
        assert not (root / "skills" / "misc" / "DESCRIPTION.md").exists()

    def test_nested_category_creates_parents(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        report = skill_category.create_category(
            _paths(root), "mlops/training", description="Training tools.",
        )

        assert report["ok"] is True
        assert (root / "skills" / "mlops" / "training").is_dir()
        assert (root / "skills" / "mlops" / "training" / "DESCRIPTION.md").is_file()

    def test_existing_directory_not_recreated(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        cat_dir = root / "skills" / "existing"
        cat_dir.mkdir(parents=True)

        report = skill_category.create_category(_paths(root), "existing")

        assert report["ok"] is True
        assert report["created"] is False

    def test_overwrites_description_with_backup(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        cat_dir = root / "skills" / "dev"
        cat_dir.mkdir(parents=True)
        desc = cat_dir / "DESCRIPTION.md"
        desc.write_text("---\ndescription: old\n---\n")

        report = skill_category.create_category(
            _paths(root), "dev", description="new desc", no_backup=False,
        )

        assert report["description_written"] is True
        assert report["backup_path"] is not None
        content = desc.read_text()
        assert "new desc" in content
        bak = cat_dir / "DESCRIPTION.md.bak"
        assert bak.is_file()
        assert "old" in bak.read_text()

    def test_no_backup_skips_bak(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        cat_dir = root / "skills" / "dev"
        cat_dir.mkdir(parents=True)
        (cat_dir / "DESCRIPTION.md").write_text("---\ndescription: old\n---\n")

        report = skill_category.create_category(
            _paths(root), "dev", description="new", no_backup=True,
        )

        assert report["backup_path"] is None
        assert not (cat_dir / "DESCRIPTION.md.bak").exists()

    def test_dry_run_creates_nothing(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        report = skill_category.create_category(
            _paths(root), "preview", description="test", apply=False,
        )

        assert report["ok"] is True
        assert report["dry_run"] is True
        assert report["created"] is True  # would-be
        assert not (root / "skills" / "preview").exists()

    def test_named_profile_writes_to_profile_skills(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        report = skill_category.create_category(
            _paths(root, profile="vc-client"), "github", description="GH tools.",
        )

        cat_dir = root / "profiles" / "vc-client" / "skills" / "github"
        assert cat_dir.is_dir()
        assert (cat_dir / "DESCRIPTION.md").is_file()

    def test_invalid_name_raises(self, tmp_path: Path) -> None:
        try:
            skill_category.create_category(_paths(tmp_path), "Bad Name!")
            raise AssertionError("should have raised")
        except skill_category.SkillCategoryError as exc:
            assert exc.kind == "config"


class TestRenderHuman:
    def test_clean_create_output(self) -> None:
        report = {
            "ok": True,
            "profile": "default",
            "category": "software-development",
            "directory": "/tmp/skills/software-development",
            "description_file": "/tmp/skills/software-development/DESCRIPTION.md",
            "created": True,
            "description_written": True,
            "backup_path": None,
            "dry_run": False,
        }
        code, text = skill_category.render_human(report)
        assert code == 0
        assert "software-development" in text
        assert "Created category" in text
        assert "DESCRIPTION.md" in text

    def test_dry_run_output(self) -> None:
        report = {
            "ok": True,
            "profile": "default",
            "category": "preview",
            "directory": "/tmp/skills/preview",
            "description_file": "/tmp/skills/preview/DESCRIPTION.md",
            "created": True,
            "description_written": False,
            "backup_path": None,
            "dry_run": True,
        }
        code, text = skill_category.render_human(report)
        assert code == 0
        assert "Dry run" in text


class TestShowResolution:
    def test_valid_category_resolves(self, tmp_path: Path) -> None:
        result = json.loads(skill_category.show_resolution(
            _paths(tmp_path / ".hermes"), category="software-development",
        ))
        assert result["category"] == "software-development"
        assert result["error"] is None
        assert result["directory"].endswith("skills/software-development")

    def test_invalid_category_reports_error(self, tmp_path: Path) -> None:
        result = json.loads(skill_category.show_resolution(
            _paths(tmp_path / ".hermes"), category="BAD!",
        ))
        assert result["error"] is not None
        assert result["error"]["kind"] == "config"


class TestCli:
    def test_help_under_skills_group(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "skills", "create-category", "--help"],
            cwd=REPO_ROOT, text=True, capture_output=True, check=False,
        )
        assert proc.returncode == 0
        assert "--description" in proc.stdout
        assert "--dry-run" in proc.stdout
