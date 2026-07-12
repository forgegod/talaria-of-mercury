"""Create skill category directories under a Hermes profile's ``skills/`` tree.

Hermes organises skills by category — a plain directory under ``skills/``
that groups related skills (e.g. ``software-development``, ``mlops/training``).
The system prompt renders each category name verbatim and optionally appends
a description read from a ``DESCRIPTION.md`` file inside the category directory:

.. code-block:: yaml

    ---
    description: Skills for ...
    ---

Creating a category is therefore: validate the directory name, create the
directory, and write a ``DESCRIPTION.md`` with the operator-supplied
description. Skills are then installed into the category with
``talaria skills install --category <name>``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from talaria.paths import DEFAULT_PROFILE_NAME, ResolvedPaths
from talaria.sync.writer import write_with_backup

#: Hermes' category validation regex (``hermes_cli/skills_hub.py``).
#: Lowercase-start, lowercase letters / digits / hyphens / underscores /
#: slashes (for nested categories). No uppercase, no spaces.
_VALID_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_/-]*$")

#: Frontmatter template for a freshly created DESCRIPTION.md.
_DESCRIPTION_TEMPLATE = "---\ndescription: {desc}\n---\n"


class SkillCategoryError(RuntimeError):
    """Raised for invalid category names or filesystem failures."""

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class CategoryResult:
    """Outcome of a category-create operation."""

    category: str
    directory: str
    description_file: str
    created: bool
    description_written: bool


def skills_dir_path(paths: ResolvedPaths) -> Path:
    """Return the ``skills/`` directory for the resolved Hermes profile."""
    if paths.profile == DEFAULT_PROFILE_NAME:
        return paths.hermes_root / "skills"
    return paths.hermes_root / "profiles" / paths.profile / "skills"


def validate_category_name(category: str) -> str:
    """Validate *category* against Hermes' category regex.

    Returns the stripped name. Raises :class:`SkillCategoryError` on invalid
    input so the caller can surface a clean error.
    """
    name = (category or "").strip()
    if not name:
        raise SkillCategoryError("category name must not be empty", kind="config")
    if not _VALID_CATEGORY_RE.match(name):
        raise SkillCategoryError(
            f"invalid category name {name!r}: must match {_VALID_CATEGORY_RE.pattern} "
            "(lowercase letters, digits, hyphens, underscores, slashes)",
            kind="config",
        )
    return name


def _description_payload(description: str) -> str:
    """Build the DESCRIPTION.md file body from the operator description."""
    desc = (description or "").strip()
    return _DESCRIPTION_TEMPLATE.format(desc=desc)


def create_category(
    paths: ResolvedPaths,
    category: str,
    *,
    description: str = "",
    apply: bool = True,
    no_backup: bool = False,
) -> dict[str, Any]:
    """Create a skill category directory and optional DESCRIPTION.md.

    Parameters
    ----------
    paths:
        Resolved Hermes profile paths.
    category:
        Directory name, validated against Hermes' category regex.
    description:
        Human-readable description written to ``DESCRIPTION.md`` frontmatter.
        When empty, no ``DESCRIPTION.md`` is written (the category still
        appears in the skills tree once it contains at least one skill).
    apply:
        When ``False``, report what would happen without touching the disk.
    no_backup:
        Skip ``.bak`` backup when overwriting an existing ``DESCRIPTION.md``.

    Returns a report dict with ``ok``, ``category``, ``directory``,
    ``description_file``, ``created``, ``description_written``, ``dry_run``.
    """
    name = validate_category_name(category)
    sdir = skills_dir_path(paths)
    cat_dir = sdir / name
    desc_path = cat_dir / "DESCRIPTION.md"

    dir_exists = cat_dir.is_dir()
    desc_exists = desc_path.is_file()
    dir_created = False
    desc_written = False
    backup_path: str | None = None

    if apply:
        if not dir_exists:
            cat_dir.mkdir(parents=True, exist_ok=True)
            dir_created = True
        if description.strip():
            payload = _description_payload(description)
            outcome = write_with_backup(desc_path, payload, no_backup=no_backup)
            desc_written = True
            backup_path = str(outcome.backup) if outcome.backup else None

    return {
        "ok": True,
        "reason": "created" if not dir_exists else "exists",
        "profile": paths.profile,
        "category": name,
        "directory": str(cat_dir),
        "description_file": str(desc_path),
        "created": dir_created if apply else not dir_exists,
        "description_written": desc_written if apply else bool(description.strip()),
        "backup_path": backup_path,
        "dry_run": not apply,
    }


def render_human(report: dict[str, Any]) -> tuple[int, str]:
    """Format a category-create report for terminal output."""
    lines = ["skill category create", "=" * 60, ""]
    lines.append(f"profile:     {report.get('profile')}")
    lines.append(f"category:    {report.get('category')}")
    lines.append(f"directory:   {report.get('directory')}")
    if report.get("description_written"):
        lines.append(f"description: {report.get('description_file')}")
    lines.append("")

    if report.get("dry_run"):
        lines.append("Dry run — no directories or files were created.")
    elif report.get("created"):
        lines.append(f"Created category directory.")
        if report.get("description_written"):
            lines.append("Wrote DESCRIPTION.md.")
    else:
        lines.append("Category directory already existed.")
        if report.get("description_written"):
            lines.append("Updated DESCRIPTION.md.")
    if report.get("backup_path"):
        lines.append(f"Backup: {report['backup_path']}")

    lines.append("")
    lines.append("=" * 60)
    lines.append("VERDICT: clean — category ready.")
    return 0, "\n".join(lines)


def show_resolution(paths: ResolvedPaths, *, category: str, description: str = "") -> str:
    """Return JSON showing the resolved directory and validation result."""
    try:
        name = validate_category_name(category)
        error = None
    except SkillCategoryError as exc:
        name = category
        error = {"kind": exc.kind, "message": str(exc)}
    sdir = skills_dir_path(paths)
    cat_dir = sdir / name
    return json.dumps(
        {
            "profile": paths.profile,
            "category": name,
            "directory": str(cat_dir),
            "description_file": str(cat_dir / "DESCRIPTION.md"),
            "would_write_description": bool(description.strip()),
            "error": error,
        },
        indent=2,
    )
