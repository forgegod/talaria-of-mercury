"""Skill index pruning — ``talaria skills prune``.

Reconciles the three sources of skill state in the active profile:

* on-disk ``<skills_root>/**/SKILL.md`` (what ``hermes skills list``
  sees),
* ``<skills_root>/.hub/lock.json`` (what ``hermes skills search``
  sees),
* ``skills.disabled`` in the profile's ``config.yaml``.

Drift between the three is reported by the
``talaria hermes doctor`` ``skill_index_drift`` detector; this
module is the write side. Read :mod:`talaria.hermos.skill_index` for
the shared read helpers and the drift taxonomy.

Prune classes (selectable via flags; all default to OFF so a bare
``talaria skills prune`` is a no-op and ``--dry-run`` is the safe
default):

* ``--prune-filesystem-only`` — delete the on-disk directory for
  each name that is in the filesystem walk but missing from
  lock.json. Resolves the bug where ``skills list`` shows a skill
  but ``skills search`` does not, by removing the orphan so both
  views agree (the skill is gone). Use ``hermes skills install
  --force`` afterwards to re-add it through the proper installer
  path if you want it back.
* ``--prune-lock-only`` — drop the lock.json entry for each name
  that is in the lock but not on disk. Resolves the reverse
  drift: the lock survives a manual ``rm -rf`` of the skill
  directory and ``skills search`` keeps returning a phantom.
* ``--prune-disabled-orphans`` — remove names from
  ``skills.disabled`` that are not on disk and not in the lock.
  Cleans stale policy entries; harmless but a clean policy file
  should not reference nothing.

Scope is single-profile: only ``<hermes_root>/<profile>/skills`` and
the profile's own ``config.yaml`` are touched. The cross-profile
"every default-profile skill is shadowed somewhere" question is a
separate tool — see the planning note in ``docs/AGENTS.md`` for the
deferred ``talaria hermes prune-skills --all-profiles`` shape.

Safety:

* Every write goes through :func:`talaria.sync.writer.write_with_backup`
  (YAML ``config.yaml``) or its JSON analogue (lock.json). The
  default is to take a ``.bak`` snapshot before overwriting; pass
  ``--no-backup`` to skip.
* ``--dry-run`` previews every action without writing.
* Without any ``--prune-*`` flag, ``run()`` returns a report with
  empty action lists and a clear ``nothing_to_do`` reason. The
  tool never deletes by accident.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from talaria.hermos.skill_index import (
    LOCK_SUBDIR,
    read_index,
)
from talaria.paths import ResolvedPaths
from talaria.sync.writer import write_with_backup
from talaria.sync.yaml_io import dump_yaml, load_yaml, validate_yaml


@dataclass(frozen=True)
class PruneReport:
    """Action summary returned by :func:`run`."""

    profile: str
    skills_root: Path
    lock_path: Path
    config_path: Path

    prune_filesystem_only: tuple[str, ...]
    prune_lock_only: tuple[str, ...]
    prune_disabled_orphans: tuple[str, ...]

    deleted_dirs: tuple[str, ...]
    lock_backups: tuple[Path, ...]
    config_backups: tuple[Path, ...]

    dry_run: bool

    @property
    def any_action(self) -> bool:
        return bool(
            self.prune_filesystem_only
            or self.prune_lock_only
            or self.prune_disabled_orphans
        )


def _delete_skill_dir(name: str, skills_root: Path) -> bool:
    """Delete every ``<skills_root>/<category>/<name>/`` (or
    ``<skills_root>/<name>/``) directory matching *name*. Returns True
    if anything was actually removed.

    Skips ``.hub/`` (the lock, not a skill).
    """
    deleted = False
    for entry in list(skills_root.iterdir()):
        if entry.name == LOCK_SUBDIR:
            continue
        if not entry.is_dir():
            continue
        if entry.name == name:
            shutil.rmtree(entry)
            deleted = True
            continue
        candidate = entry / name
        if candidate.is_dir():
            shutil.rmtree(candidate)
            deleted = True
    return deleted


def _prune_lock_entries(
    lock_path: Path,
    names: tuple[str, ...],
    *,
    apply: bool,
    no_backup: bool,
) -> tuple[Path | None, list[str]]:
    """Remove *names* from ``lock_path.installed``. Returns
    ``(backup_path_or_None, actually_removed_names)``.
    """
    if not names:
        return None, []
    if not lock_path.exists():
        return None, []
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, []
    if not isinstance(data, dict) or not isinstance(data.get("installed"), dict):
        return None, []
    installed = data["installed"]
    actually_removed = [n for n in names if n in installed]
    if not actually_removed:
        return None, []
    if not apply:
        return None, actually_removed
    for n in actually_removed:
        del installed[n]
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    backup: Path | None = None
    if not no_backup:
        backup = lock_path.with_suffix(lock_path.suffix + ".bak")
        shutil.copy2(lock_path, backup)
    lock_path.write_text(payload, encoding="utf-8")
    return backup, actually_removed


def _prune_disabled_orphans(
    config_path: Path,
    orphans: tuple[str, ...],
    *,
    apply: bool,
    no_backup: bool,
) -> tuple[Path | None, list[str]]:
    """Remove *orphans* from ``config.skills.disabled``. Returns
    ``(backup_path_or_None, actually_removed_names)``.
    """
    if not orphans:
        return None, []
    if not config_path.exists():
        return None, []
    config = load_yaml(config_path)
    if not isinstance(config, dict):
        return None, []
    skills_cfg = config.setdefault("skills", {})
    if not isinstance(skills_cfg, dict):
        return None, []
    current = skills_cfg.get("disabled") or []
    if not isinstance(current, list):
        return None, []
    current_set = {str(n) for n in current if n}
    actually_removed = sorted(current_set & set(orphans))
    if not actually_removed:
        return None, []
    if not apply:
        return None, actually_removed
    skills_cfg["disabled"] = sorted(current_set - set(actually_removed))
    payload = dump_yaml(config)
    ok, err = validate_yaml(payload)
    if not ok:
        raise RuntimeError(f"produced YAML failed validation: {err}")
    outcome = write_with_backup(config_path, payload, no_backup=no_backup)
    backup = outcome.backup if outcome.backup else None
    return backup, actually_removed


def run(
    paths: ResolvedPaths,
    *,
    prune_filesystem_only: bool = False,
    prune_lock_only: bool = False,
    prune_disabled_orphans: bool = False,
    apply: bool = False,
    no_backup: bool = False,
) -> PruneReport:
    """Reconcile the three skill-index sources for *paths*.

    All three ``--prune-*`` flags default to OFF. With none set the
    function returns a report whose ``any_action`` is False — the
    bare command is a no-op. ``--dry-run`` (``apply=False``) is the
    default for safety; pass ``--apply`` to actually delete.

    Reads once via :func:`talaria.hermos.skill_index.read_index`,
    computes the drift sets, then performs the selected writes.
    Filesystem deletes are ``shutil.rmtree`` (recursive, no prompt);
    lock.json writes take a ``.bak`` snapshot before overwriting
    unless ``--no-backup``; config.yaml writes go through
    :func:`talaria.sync.writer.write_with_backup` (atomic write +
    timestamped backup).
    """
    idx = read_index(paths)

    fs_targets = tuple(idx.filesystem_only) if prune_filesystem_only else ()
    lock_targets = tuple(idx.lock_only) if prune_lock_only else ()
    disabled_targets = tuple(idx.disabled_orphans) if prune_disabled_orphans else ()

    deleted_dirs: list[str] = []
    lock_backups: list[Path] = []
    config_backups: list[Path] = []

    if apply and fs_targets:
        for name in fs_targets:
            if _delete_skill_dir(name, idx.skills_root):
                deleted_dirs.append(name)

    lock_backup, _removed_lock = _prune_lock_entries(
        idx.lock_path, lock_targets, apply=apply, no_backup=no_backup,
    )
    if lock_backup is not None:
        lock_backups.append(lock_backup)

    config_backup, _removed_disabled = _prune_disabled_orphans(
        idx.config_path, disabled_targets, apply=apply, no_backup=no_backup,
    )
    if config_backup is not None:
        config_backups.append(config_backup)

    return PruneReport(
        profile=paths.profile,
        skills_root=idx.skills_root,
        lock_path=idx.lock_path,
        config_path=idx.config_path,
        prune_filesystem_only=fs_targets,
        prune_lock_only=lock_targets,
        prune_disabled_orphans=disabled_targets,
        deleted_dirs=tuple(deleted_dirs),
        lock_backups=tuple(lock_backups),
        config_backups=tuple(config_backups),
        dry_run=not apply,
    )


def report_to_dict(report: PruneReport) -> dict[str, Any]:
    """Serialise *report* for ``--json`` output."""
    return {
        "profile": report.profile,
        "skills_root": str(report.skills_root),
        "lock_path": str(report.lock_path),
        "config_path": str(report.config_path),
        "prune_filesystem_only": list(report.prune_filesystem_only),
        "prune_lock_only": list(report.prune_lock_only),
        "prune_disabled_orphans": list(report.prune_disabled_orphans),
        "deleted_dirs": list(report.deleted_dirs),
        "lock_backups": [str(p) for p in report.lock_backups],
        "config_backups": [str(p) for p in report.config_backups],
        "dry_run": report.dry_run,
        "any_action": report.any_action,
    }


def render_human(report: PruneReport) -> tuple[int, str]:
    """Format *report* for terminal output.

    Exit code is 0 when nothing was planned OR when ``--dry-run`` is
    in effect (preview is a clean answer). 1 when writes fired (the
    operator should re-run ``talaria hermes doctor`` to confirm the
    drift is resolved; the verdict of this command is "we changed
    state"). 2 reserved for tool errors (none currently raised from
    here — see ``SkillInstallError`` for related install/uninstall
    error codes).
    """
    lines = ["skill index prune", "=" * 60, ""]
    lines.append(f"profile:     {report.profile}")
    lines.append(f"skills_root: {report.skills_root}")
    lines.append(f"lock_path:   {report.lock_path}")
    lines.append(f"config_path: {report.config_path}")
    lines.append("")

    if not report.any_action:
        lines.append("No prune flags set — nothing planned.")
        lines.append("Pass one or more of --prune-filesystem-only, --prune-lock-only,")
        lines.append("--prune-disabled-orphans to select a prune class.")
        lines.append("")
        lines.append("=" * 60)
        lines.append("VERDICT: nothing to do.")
        return 0, "\n".join(lines)

    if report.prune_filesystem_only:
        verb = "would delete" if report.dry_run else "deleted"
        lines.append(f"{verb} filesystem-only skills ({len(report.prune_filesystem_only)}):")
        for n in report.prune_filesystem_only:
            suffix = ""
            if not report.dry_run and n in report.deleted_dirs:
                suffix = ""
            lines.append(f"  - {n}")
        if not report.dry_run:
            actually = len(report.deleted_dirs)
            lines.append(f"  ({actually} directory tree(s) actually removed)")

    if report.prune_lock_only:
        verb = "would remove" if report.dry_run else "removed"
        lines.append(f"{verb} lock-only entries ({len(report.prune_lock_only)}):")
        for n in report.prune_lock_only:
            lines.append(f"  - {n}")

    if report.prune_disabled_orphans:
        verb = "would remove" if report.dry_run else "removed"
        lines.append(f"{verb} disabled orphans from skills.disabled ({len(report.prune_disabled_orphans)}):")
        for n in report.prune_disabled_orphans:
            lines.append(f"  - {n}")

    lines.append("")
    if report.dry_run:
        lines.append("Dry run: no filesystem or config writes were performed.")
        lines.append("Re-run with --apply to execute the planned actions.")
        lines.append("")
        lines.append("=" * 60)
        lines.append("VERDICT: dry-run preview.")
        return 0, "\n".join(lines)

    if report.lock_backups:
        lines.append("lock.json backups:")
        for p in report.lock_backups:
            lines.append(f"  {p}")
    if report.config_backups:
        lines.append("config.yaml backups:")
        for p in report.config_backups:
            lines.append(f"  {p}")
    lines.append("")
    lines.append("=" * 60)
    lines.append("VERDICT: state changed — re-run `talaria hermes doctor` to verify.")
    return 1, "\n".join(lines)