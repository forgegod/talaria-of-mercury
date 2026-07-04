"""Fuzzy similarity comparison for skill name collisions.

When a recursive skill install encounters two identifiers with the same
trailing component (e.g. ``cat-a/foo`` and ``cat-b/foo``), Hermes' lock.json
keys by name — so the second install would silently overwrite the first.

This module compares the incoming skill against the already-installed skill
by reading their SKILL.md frontmatter (``name`` + ``description`` fields)
and computing a :class:`difflib.SequenceMatcher` ratio. At or above a
threshold (default 0.65), the skills are considered "similar but not
identical" and the operator can decide to replace the old one via
``--replace-similar-skill``.

Comparison surfaces:

* **Existing skill**: lock.json ``identifier``/``source`` + the installed
  ``SKILL.md`` frontmatter read from disk.
* **Incoming skill**: the GitHub identifier + the upstream ``SKILL.md``
  frontmatter fetched via raw.githubusercontent.com.

No external dependencies — :mod:`difflib` and :mod:`urllib` from stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import json
from pathlib import Path
from typing import Any
from urllib import request as urllib_request

from talaria.paths import DEFAULT_PROFILE_NAME, ResolvedPaths

#: Default similarity threshold for "similar but not identical" classification.
SIMILARITY_THRESHOLD = 0.65

HTTP_TIMEOUT = 30


@dataclass(frozen=True)
class SkillFrontmatter:
    """Parsed SKILL.md frontmatter for similarity comparison."""

    name: str
    description: str
    identifier: str

    @property
    def comparison_text(self) -> str:
        """Concatenated text used for SequenceMatcher comparison."""
        return f"{self.name} {self.description}".strip().lower()


@dataclass(frozen=True)
class SimilarityResult:
    """Outcome of comparing an incoming skill against an installed one."""

    incoming_identifier: str
    installed_identifier: str
    ratio: float
    similar: bool
    threshold: float
    incoming_frontmatter: SkillFrontmatter | None
    installed_frontmatter: SkillFrontmatter | None
    error: str | None = None


# ── Frontmatter reading ──────────────────────────────────────────────


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """Parse YAML frontmatter from a SKILL.md body (best-effort)."""
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    yaml_body = parts[1].strip()
    if not yaml_body:
        return {}
    result: dict[str, Any] = {}
    for line in yaml_body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and value:
            result[key] = value
    return result


def read_installed_frontmatter(
    skills_dir: Path, install_path: str, name: str,
) -> SkillFrontmatter | None:
    """Read the installed skill's frontmatter from disk.

    ``install_path`` comes from lock.json (relative to skills_dir).
    """
    skill_md = skills_dir / install_path / "SKILL.md"
    if not skill_md.is_file():
        # Some installs are flat (install_path == name)
        skill_md = skills_dir / name / "SKILL.md"
        if not skill_md.is_file():
            return None
    try:
        content = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    fm = _parse_frontmatter(content)
    return SkillFrontmatter(
        name=str(fm.get("name", name)),
        description=str(fm.get("description", "")),
        identifier=install_path,
    )


def fetch_incoming_frontmatter(identifier: str) -> SkillFrontmatter | None:
    """Fetch the incoming skill's SKILL.md frontmatter from GitHub.

    Handles identifiers like ``skills-sh/owner/repo/path`` or
    ``owner/repo/path``. Returns ``None`` on any failure (network, parse,
    non-GitHub source).
    """
    base = identifier.strip().strip("/")
    for prefix in ("skills-sh/", "skills.sh/"):
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    parts = base.split("/", 2)
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    skill_path = parts[2] if len(parts) == 3 else ""
    # Determine branch and build raw URL
    branch = "main"
    raw_path = f"{skill_path}/SKILL.md" if skill_path else "SKILL.md"
    raw_url = (
        f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{raw_path}"
    )
    headers = {"User-Agent": "talaria-skill-similarity"}
    import os

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib_request.Request(raw_url, headers=headers)
    try:
        with urllib_request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            content = resp.read().decode("utf-8")
    except Exception:
        # Try 'master' branch as fallback
        raw_url = (
            f"https://raw.githubusercontent.com/{owner}/{repo}/master/{raw_path}"
        )
        req2 = urllib_request.Request(raw_url, headers=headers)
        try:
            with urllib_request.urlopen(req2, timeout=HTTP_TIMEOUT) as resp:
                content = resp.read().decode("utf-8")
        except Exception:
            return None
    fm = _parse_frontmatter(content)
    if not fm:
        return None
    return SkillFrontmatter(
        name=str(fm.get("name", skill_path.rsplit("/", 1)[-1] if skill_path else "")),
        description=str(fm.get("description", "")),
        identifier=identifier,
    )


# ── Lock file reading ────────────────────────────────────────────────


def skills_dir_path(paths: ResolvedPaths) -> Path:
    """Return the ``skills/`` directory for the resolved Hermes profile."""
    if paths.profile == DEFAULT_PROFILE_NAME:
        return paths.hermes_root / "skills"
    return paths.hermes_root / "profiles" / paths.profile / "skills"


def lock_file_path(paths: ResolvedPaths) -> Path:
    """Return the path to Hermes' ``skills/.hub/lock.json``."""
    return skills_dir_path(paths) / ".hub" / "lock.json"


def read_installed_lock(paths: ResolvedPaths) -> dict[str, dict[str, Any]]:
    """Read Hermes' lock.json and return the ``installed`` dict.

    Returns an empty dict if the file is missing or unparseable.
    """
    path = lock_file_path(paths)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data.get("installed") or {}


def get_installed_entry(
    paths: ResolvedPaths, skill_name: str,
) -> dict[str, Any] | None:
    """Return the lock.json entry for *skill_name*, or ``None``."""
    installed = read_installed_lock(paths)
    return installed.get(skill_name)


# ── Similarity comparison ────────────────────────────────────────────


def compare_skills(
    incoming: SkillFrontmatter, installed: SkillFrontmatter,
    *, threshold: float = SIMILARITY_THRESHOLD,
) -> SimilarityResult:
    """Compare two skill frontmatters and return the similarity ratio."""
    ratio = SequenceMatcher(
        None, incoming.comparison_text, installed.comparison_text,
    ).ratio()
    return SimilarityResult(
        incoming_identifier=incoming.identifier,
        installed_identifier=installed.identifier,
        ratio=round(ratio, 4),
        similar=ratio >= threshold,
        threshold=threshold,
        incoming_frontmatter=incoming,
        installed_frontmatter=installed,
    )


def assess_collision(
    paths: ResolvedPaths,
    incoming_identifier: str,
    skill_name: str,
    *,
    threshold: float = SIMILARITY_THRESHOLD,
    fetch_incoming: bool = True,
) -> SimilarityResult:
    """Full collision assessment: fetch incoming + read installed + compare.

    When ``fetch_incoming`` is ``False``, the incoming frontmatter is
    synthesized from the identifier alone (name = trailing component,
    empty description) — used by tests to avoid network calls.

    Returns a :class:`SimilarityResult`. If either frontmatter can't be
    read, the result has ``ratio=0`` and ``similar=False`` with an
    ``error`` message.
    """
    installed_entry = get_installed_entry(paths, skill_name)

    installed_fm: SkillFrontmatter | None = None
    if installed_entry:
        sdir = skills_dir_path(paths)
        install_path = str(installed_entry.get("install_path") or skill_name)
        installed_fm = read_installed_frontmatter(sdir, install_path, skill_name)
        if installed_fm and installed_entry.get("identifier"):
            installed_fm = SkillFrontmatter(
                name=installed_fm.name,
                description=installed_fm.description,
                identifier=str(installed_entry["identifier"]),
            )

    incoming_fm: SkillFrontmatter | None = None
    if fetch_incoming:
        incoming_fm = fetch_incoming_frontmatter(incoming_identifier)
    if incoming_fm is None:
        # Fallback: synthesize from identifier
        incoming_fm = SkillFrontmatter(
            name=skill_name,
            description="",
            identifier=incoming_identifier,
        )

    if installed_fm is None:
        return SimilarityResult(
            incoming_identifier=incoming_identifier,
            installed_identifier="(not installed)",
            ratio=0.0,
            similar=False,
            threshold=threshold,
            incoming_frontmatter=incoming_fm,
            installed_frontmatter=None,
            error="installed skill frontmatter not readable",
        )

    return compare_skills(incoming_fm, installed_fm, threshold=threshold)
