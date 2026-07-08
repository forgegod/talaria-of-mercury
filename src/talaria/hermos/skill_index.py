"""Skill-index reader — single source of truth for doctor + prune.

Hermes tracks installed skills in two places:

* ``<skills_root>/**/SKILL.md`` — the on-disk directory tree. Each leaf
  directory's *basename* is the skill name (e.g.
  ``skills/devops/kanban-worker/SKILL.md`` → ``kanban-worker``). Category
  subdirectories are part of the path but not part of the name.
* ``<skills_root>/.hub/lock.json`` — the central registry Hermes'
  ``skills list`` and ``skills search`` consult. Keys are skill names.
  Hermes keys the lock by name only, not by ``(category, name)``, so a
  filesystem install that bypassed ``hermes skills install`` shows in
  ``skills list`` (filesystem walk) but not in ``skills search`` (lock
  lookup).

A third place can reference skill names: the profile's
``config.yaml`` carries ``skills.disabled: [name, ...]``. An entry there
for a name that no longer exists on disk and is not in the lock is a
third drift class — operator wrote a disabled policy for a skill that
is gone.

This module exposes pure-Python readers for all three sources plus a
single ``read_index(paths)`` aggregator the doctor detector and the
``talaria skills prune`` tool both call. Compute the drift view once;
both consumers see the same numbers.

All functions are read-only. Writes (deleting orphan directories,
rewriting lock.json, editing ``skills.disabled``) live in
:mod:`talaria.hermos.skill_prune`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from talaria.hermos import skill_install
from talaria.paths import ResolvedPaths


#: Default subdirectory under ``skills_root`` holding the hub registry.
LOCK_SUBDIR = ".hub"
LOCK_FILENAME = "lock.json"


def profile_skills_root(paths: ResolvedPaths) -> Path:
    """Return the skills directory for *paths* (the active profile).

    Mirrors :func:`talaria.hermos.skill_install.profile_hermes_home`:
    the default profile uses ``<hermes_root>/skills``, named profiles
    use ``<hermes_root>/profiles/<name>/skills``.
    """
    home = Path(skill_install.profile_hermes_home(paths))
    return home / "skills"


def profile_lock_path(paths: ResolvedPaths) -> Path:
    """Return the absolute path to the profile's ``lock.json``."""
    return profile_skills_root(paths) / LOCK_SUBDIR / LOCK_FILENAME


def read_filesystem_skill_names(skills_root: Path) -> list[str]:
    """Walk *skills_root* and return the sorted skill names on disk.

    A skill exists when ``<skills_root>/<category>/<name>/SKILL.md`` (or
    ``<skills_root>/<name>/SKILL.md`` at the flat root) is present.
    The skill name is the leaf directory's basename. The category
    subdirectory is not part of the name — Hermes keys everything by
    the trailing component.

    Returns an empty list if *skills_root* does not exist or is not a
    directory. ``.hub/`` is excluded (it's the lock, not a skill).
    """
    if not skills_root.exists() or not skills_root.is_dir():
        return []
    names: set[str] = set()
    for skill_md in skills_root.rglob("SKILL.md"):
        if LOCK_SUBDIR in skill_md.parts:
            continue
        names.add(skill_md.parent.name)
    return sorted(names)


def read_lock_skill_names(lock_path: Path) -> list[str]:
    """Return the sorted skill names listed in *lock_path*.

    Hermes' ``skills list`` filesystem walk and the ``skills search``
    lock lookup disagree when *lock_path* is missing or stale (the bug
    that motivated this module). A missing lock.json is not an error —
    it just means no skill has been installed via ``hermes skills
    install`` in this profile yet. Returns ``[]`` in that case.
    """
    if not lock_path.exists() or not lock_path.is_file():
        return []
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    installed = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(installed, dict):
        return []
    return sorted(installed.keys())


def read_disabled_skill_names(config_path: Path) -> list[str]:
    """Return the sorted skill names in ``skills.disabled`` of *config_path*.

    A missing ``config.yaml`` or absent ``skills.disabled`` list
    returns ``[]`` (no disabled names, not an error).
    """
    if not config_path.exists() or not config_path.is_file():
        return []
    try:
        # Lazy import to avoid a hard dep cycle through skill_install.
        from talaria.sync.yaml_io import load_yaml
        data = load_yaml(config_path)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    skills_cfg = data.get("skills") or {}
    if not isinstance(skills_cfg, dict):
        return []
    disabled = skills_cfg.get("disabled") or []
    if not isinstance(disabled, list):
        return []
    return sorted(str(n) for n in disabled if n)


@dataclass(frozen=True)
class SkillIndex:
    """A point-in-time snapshot of one profile's skill index.

    Three sorted name lists, plus the resolved paths they came from.
    Diff helpers below compute the drift between any two of the three.
    """

    profile: str
    skills_root: Path
    lock_path: Path
    config_path: Path

    filesystem: list[str] = field(default_factory=list)
    lock: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)

    @property
    def filesystem_set(self) -> set[str]:
        return set(self.filesystem)

    @property
    def lock_set(self) -> set[str]:
        return set(self.lock)

    @property
    def disabled_set(self) -> set[str]:
        return set(self.disabled)

    @property
    def filesystem_only(self) -> list[str]:
        """Names on disk but absent from lock.json.

        These show in ``hermes skills list`` but not in
        ``hermes skills search`` — the original bug this module exists
        to detect. Common cause: filesystem ``cp -r`` that bypassed
        ``hermes skills install``.
        """
        return sorted(self.filesystem_set - self.lock_set)

    @property
    def lock_only(self) -> list[str]:
        """Names in lock.json but absent on disk.

        The reverse of the original bug: lock entry survives a manual
        ``rm -rf`` of the skill directory. ``hermes skills list``
        silently omits these because the filesystem walk no longer
        finds them; ``hermes skills search`` still returns them.
        """
        return sorted(self.lock_set - self.filesystem_set)

    @property
    def disabled_orphans(self) -> list[str]:
        """Names in ``skills.disabled`` that are not on disk and not in lock.

        Operator-disabled a skill name that no longer exists in either
        registry. Harmless but stale — a clean policy file should not
        carry names referencing nothing. The diagnostic surfaces these
        so the prune tool can clean them up.
        """
        return sorted(self.disabled_set - self.filesystem_set - self.lock_set)

    @property
    def disabled_present(self) -> list[str]:
        """Names in ``skills.disabled`` that ARE on disk or in lock.

        Names the operator wants disabled; respect them. The prune
        tool must never touch this set — only the orphans above.
        """
        return sorted(self.disabled_set & (self.filesystem_set | self.lock_set))

    @property
    def has_drift(self) -> bool:
        return bool(self.filesystem_only or self.lock_only or self.disabled_orphans)


def read_index(paths: ResolvedPaths) -> SkillIndex:
    """Read all three sources for *paths* and assemble a :class:`SkillIndex`.

    Cheap (one ``rglob`` + one JSON read + one YAML read). The
    detector and the prune tool both call this — one source of truth
    for the drift verdict.
    """
    skills_root = profile_skills_root(paths)
    lock_path = profile_lock_path(paths)
    config_path = skill_install.profile_config_path(paths)
    return SkillIndex(
        profile=paths.profile,
        skills_root=skills_root,
        lock_path=lock_path,
        config_path=config_path,
        filesystem=read_filesystem_skill_names(skills_root),
        lock=read_lock_skill_names(lock_path),
        disabled=read_disabled_skill_names(config_path),
    )


def index_to_report(idx: SkillIndex) -> dict[str, Any]:
    """Serialise *idx* to a JSON-safe dict for the doctor report."""
    return {
        "profile": idx.profile,
        "skills_root": str(idx.skills_root),
        "lock_path": str(idx.lock_path),
        "config_path": str(idx.config_path),
        "filesystem": idx.filesystem,
        "lock": idx.lock,
        "disabled": idx.disabled,
        "filesystem_only": idx.filesystem_only,
        "lock_only": idx.lock_only,
        "disabled_orphans": idx.disabled_orphans,
        "disabled_present": idx.disabled_present,
        "has_drift": idx.has_drift,
    }