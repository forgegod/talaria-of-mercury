"""Sync skill directory trees between two profiles.

Skills live at ``<root>/skills/<category>/<skill-name>/`` with
``SKILL.md`` as the canonical entry point. Sync walks the source
tree, compares each skill against its target counterpart, and
copies on three outcomes:

* in sync (byte-for-byte identical) — skip.
* differs — back up the target tree, replace it.
* missing on target — copy fresh.

Filter syntax:

* ``None`` (no ``--sync-skills`` filter) — every source skill.
* a category name (``"github"``) — only skills under that category.
* a ``category/skill-name`` path — only that single skill.

Filtering combines: a category filter selects every skill in that
category; a path filter selects just that skill; both can be
combined across multiple flags.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from talaria.sync.paths import SyncProfile
from talaria.sync.result import SkillsPhaseResult
from talaria.sync.writer import write_with_backup


def _walk_skills(skills_dir: Path) -> dict[str, list[Path]]:
    """Walk *skills_dir* and return ``{category: [skill_dir, ...]}``.

    A category is any subdirectory containing at least one skill
    (where a skill is a directory holding ``SKILL.md``). Hidden
    directories (names starting with ``.``) are skipped to avoid
    copying editor swap files.
    """
    result: dict[str, list[Path]] = {}
    if not skills_dir.is_dir():
        return result
    for cat_dir in sorted(skills_dir.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.startswith("."):
            continue
        skills = []
        for skill_dir in sorted(cat_dir.iterdir()):
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                skills.append(skill_dir)
        if skills:
            result[cat_dir.name] = skills
    return result


def _dir_trees_equal(a: Path, b: Path) -> bool:
    """Compare two directory trees for byte-level equality.

    Both files and structure must match. Used to decide whether a
    skill needs replacing — content comparison (not just mtime)
    means re-running sync on an unchanged tree is a no-op.
    """
    if not b.exists():
        return False
    a_files = {p.relative_to(a): p for p in a.rglob("*") if p.is_file()}
    b_files = {p.relative_to(b): p for p in b.rglob("*") if p.is_file()}
    if set(a_files.keys()) != set(b_files.keys()):
        return False
    for rel, a_path in a_files.items():
        if a_path.read_bytes() != b_files[rel].read_bytes():
            return False
    return True


def sync_skills(
    source: SyncProfile,
    target: SyncProfile,
    *,
    filters: list[str] | None = None,
    apply: bool = True,
    no_backup: bool = False,
) -> SkillsPhaseResult:
    """Copy skill trees from *source* to *target*.

    Parameters
    ----------
    filters:
        Optional list of category names (``"github"``) or
        ``category/skill-name`` paths. ``None`` means every skill.
        An empty list is treated the same as ``None``.
    apply:
        When ``False``, the function reports what would change but
        never modifies the filesystem.
    no_backup:
        Skip the ``.bak`` step when replacing differing skills.
    """
    result = SkillsPhaseResult(
        phase="skills",
        status="in_sync",
        target_path=target.skills_dir,
        filters=list(filters or []),
    )

    source_map = _walk_skills(source.skills_dir)
    if not source_map:
        result.status = "skipped"
        result.logs.append("  skip: source has no skills/")
        return result

    # Build filter sets. A filter that contains "/" targets one
    # skill; otherwise it's a category. Empty sets still count as
    # "no category/skill filter requested" so the corresponding
    # branch does not restrict.
    filter_cats: set[str] | None = None
    filter_skills: set[str] | None = None
    if filters:
        wanted_cats: set[str] = set()
        wanted_skills: set[str] = set()
        for f in filters:
            if "/" in f:
                wanted_skills.add(f)
            else:
                wanted_cats.add(f)
        filter_cats = wanted_cats if wanted_cats else None
        filter_skills = wanted_skills if wanted_skills else None

    has_cat_filter = filter_cats is not None
    has_skill_filter = filter_skills is not None
    active_filter_cats = filter_cats if has_cat_filter else set()
    active_filter_skills = filter_skills if has_skill_filter else set()

    for cat, skills in sorted(source_map.items()):
        if has_cat_filter and cat not in active_filter_cats:
            continue

        for skill_dir in skills:
            skill_rel = f"{cat}/{skill_dir.name}"
            if (
                has_skill_filter
                and skill_rel not in active_filter_skills
                and not (has_cat_filter and cat in active_filter_cats)
            ):
                continue

            target_skill = target.skills_dir / cat / skill_dir.name

            if target_skill.exists():
                if _dir_trees_equal(skill_dir, target_skill):
                    result.skipped += 1
                    continue
                # Differ — back up and replace.
                result.skills_detail.append(f"  update: {skill_rel}")
                if apply:
                    backup = target_skill.with_name(target_skill.name + ".bak")
                    if not no_backup:
                        if backup.exists():
                            shutil.rmtree(backup)
                        shutil.copytree(target_skill, backup)
                        result.skills_detail.append(f"    backup: {backup}")
                    shutil.rmtree(target_skill)
                    shutil.copytree(skill_dir, target_skill)
                    result.skills_detail.append(f"    written: {target_skill}")
                    result.write_confirmed = True
                else:
                    result.skills_detail.append("    (dry run)")
                result.copied += 1
                result.status = "updated"
            else:
                result.skills_detail.append(f"  new: {skill_rel}")
                if apply:
                    target.skills_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(skill_dir, target_skill)
                    result.skills_detail.append(f"    written: {target_skill}")
                    result.write_confirmed = True
                else:
                    result.skills_detail.append("    (dry run)")
                result.new_count += 1
                result.status = "updated"

    summary = (
        f"summary: {result.copied} updated, "
        f"{result.new_count} new, {result.skipped} in sync"
    )
    if not apply:
        summary += " (dry run)"
    result.logs.append(f"  {summary}")

    if result.status == "in_sync" and result.skipped > 0 and result.copied == 0 and result.new_count == 0:
        # Real in-sync, not just "skipped because no source skills".
        pass
    return result