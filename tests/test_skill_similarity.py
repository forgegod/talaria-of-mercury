"""Tests for talaria.hermos.skill_similarity."""

from __future__ import annotations

import json
from pathlib import Path

from talaria.hermos import skill_similarity
from talaria.paths import ResolvedPaths


def _paths(root: Path, profile: str = "default") -> ResolvedPaths:
    state = root / "state.db" if profile == "default" else root / "profiles" / profile / "state.db"
    logs = root / "logs" if profile == "default" else root / "profiles" / profile / "logs"
    return ResolvedPaths(profile=profile, hermes_root=root, state_db=state, log_dir=logs)


def _write_lock_json(root: Path, installed: dict) -> None:
    sdir = root / "skills"
    hub = sdir / ".hub"
    hub.mkdir(parents=True, exist_ok=True)
    (hub / "lock.json").write_text(json.dumps({"version": 1, "installed": installed}))


def _write_skill_md(
    root: Path, install_path: str, name: str, description: str,
) -> None:
    skill_dir = root / "skills" / install_path
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: \"{description}\"\n---\n\n# {name}\n"
    )


class TestParseFrontmatter:
    def test_parses_name_and_description(self) -> None:
        content = '---\nname: foo\ndescription: "A foo skill"\n---\nbody'
        fm = skill_similarity._parse_frontmatter(content)
        assert fm["name"] == "foo"
        assert fm["description"] == "A foo skill"

    def test_returns_empty_on_no_frontmatter(self) -> None:
        assert skill_similarity._parse_frontmatter("just body") == {}

    def test_strips_quotes(self) -> None:
        content = "---\nname: bar\ndescription: 'quoted'\n---\n"
        fm = skill_similarity._parse_frontmatter(content)
        assert fm["description"] == "quoted"


class TestReadInstalledFrontmatter:
    def test_reads_from_install_path(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        _write_skill_md(root, "github/my-skill", "my-skill", "GH workflows")
        fm = skill_similarity.read_installed_frontmatter(
            root / "skills", "github/my-skill", "my-skill",
        )
        assert fm is not None
        assert fm.name == "my-skill"
        assert fm.description == "GH workflows"

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        fm = skill_similarity.read_installed_frontmatter(
            tmp_path / "skills", "nonexistent", "nope",
        )
        assert fm is None


class TestCompareSkills:
    def test_identical_skills_are_similar(self) -> None:
        a = skill_similarity.SkillFrontmatter("foo", "GH workflows", "a/foo")
        b = skill_similarity.SkillFrontmatter("foo", "GH workflows", "b/foo")
        result = skill_similarity.compare_skills(a, b)
        assert result.ratio == 1.0
        assert result.similar is True

    def test_very_different_skills_are_not_similar(self) -> None:
        a = skill_similarity.SkillFrontmatter("foo", "GitHub repo management", "a/foo")
        b = skill_similarity.SkillFrontmatter("bar", "Docker container management", "b/bar")
        result = skill_similarity.compare_skills(a, b)
        assert result.ratio < 0.65
        assert result.similar is False

    def test_close_descriptions_are_similar(self) -> None:
        a = skill_similarity.SkillFrontmatter("arxiv", "Search arXiv papers by keyword", "a/arxiv")
        b = skill_similarity.SkillFrontmatter("arxiv", "Search arXiv papers by author", "b/arxiv")
        result = skill_similarity.compare_skills(a, b)
        assert result.similar is True

    def test_custom_threshold(self) -> None:
        a = skill_similarity.SkillFrontmatter("x", "aaa", "a/x")
        b = skill_similarity.SkillFrontmatter("x", "aab", "b/x")
        # ratio ~0.66 default, should be similar at 0.5
        assert skill_similarity.compare_skills(a, b, threshold=0.5).similar is True
        # but not similar at 0.9
        assert skill_similarity.compare_skills(a, b, threshold=0.9).similar is False


class TestAssessCollision:
    def test_similar_installed_skill_detected(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        _write_lock_json(root, {
            "arxiv": {
                "source": "github", "identifier": "old/arxiv",
                "install_path": "research/arxiv",
                "content_hash": "sha256:abc",
            }
        })
        _write_skill_md(root, "research/arxiv", "arxiv", "Search arXiv papers")

        result = skill_similarity.assess_collision(
            _paths(root),
            incoming_identifier="new/arxiv",
            skill_name="arxiv",
            fetch_incoming=False,  # avoid network; synthesize from identifier
        )

        # Incoming frontmatter synthesized: name="arxiv", description=""
        # Installed: name="arxiv", description="Search arXiv papers"
        # ratio will be moderate but name match boosts it
        assert result.installed_identifier == "old/arxiv"
        assert result.error is None

    def test_not_installed_returns_error(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        result = skill_similarity.assess_collision(
            _paths(root),
            incoming_identifier="x/foo",
            skill_name="foo",
            fetch_incoming=False,
        )
        assert result.ratio == 0.0
        assert result.similar is False
        assert result.error is not None

    def test_missing_lock_returns_none_entry(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        result = skill_similarity.get_installed_entry(_paths(root), "nope")
        assert result is None


class TestLockReading:
    def test_read_installed_lock(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        _write_lock_json(root, {"foo": {"identifier": "x/foo"}})
        installed = skill_similarity.read_installed_lock(_paths(root))
        assert "foo" in installed

    def test_missing_lock_returns_empty(self, tmp_path: Path) -> None:
        installed = skill_similarity.read_installed_lock(_paths(tmp_path / ".hermes"))
        assert installed == {}

    def test_named_profile_path(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        sdir = root / "profiles" / "vc-client" / "skills" / ".hub"
        sdir.mkdir(parents=True)
        (sdir / "lock.json").write_text(json.dumps({"version": 1, "installed": {}}))
        path = skill_similarity.lock_file_path(_paths(root, profile="vc-client"))
        assert path.is_file()
