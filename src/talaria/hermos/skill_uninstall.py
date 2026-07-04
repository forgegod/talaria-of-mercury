"""Uninstall every skill below a recursive Hermes skill identifier.

Mirror of :mod:`talaria.hermos.skill_install`: expand a recursive
identifier (e.g. ``skills-sh/addyosmani/agent-skills/*``), invoke
``hermes skills uninstall`` for each child skill, then remove the
uninstalled skill names from the profile's ``skills.disabled`` list so
the policy state does not reference skills that are no longer present.

`hermes skills uninstall` takes a skill *name* (not an identifier), so
each expanded identifier is reduced to its trailing component before
delegation.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Any, Callable

from talaria.hermos import skill_install
from talaria.paths import ResolvedPaths
from talaria.sync.writer import write_with_backup
from talaria.sync.yaml_io import dump_yaml, load_yaml, validate_yaml


@dataclass(frozen=True)
class UninstallResult:
    """Result from one ``hermes skills uninstall`` invocation."""

    identifier: str
    name: str
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


Uninstaller = Callable[[str, ResolvedPaths], UninstallResult]


def default_uninstaller(identifier: str, paths: ResolvedPaths) -> UninstallResult:
    """Invoke Hermes to uninstall one skill from the selected profile.

    ``hermes skills uninstall`` has no ``--yes`` flag and prompts for
    confirmation on stdin. We feed ``"y"`` so the call is non-interactive.
    Hermes also exits 0 on several non-success conditions (prompt
    cancelled, skill not installed, skill is a builtin), so we detect the
    failure markers in stdout and convert them to a non-zero return code —
    otherwise Talaria would report success for a skill that was never
    removed.
    """
    _FAILURE_MARKERS = ("Cancelled", "Error:", "not found", "not a hub-installed")
    name = skill_install.skill_name_from_identifier(identifier)
    cmd = ["hermes", "skills", "uninstall", name]
    env = os.environ.copy()
    env["HERMES_PROFILE"] = paths.profile
    proc = subprocess.run(
        cmd, text=True, capture_output=True, check=False, env=env, input="y",
    )
    rc = proc.returncode
    if rc == 0 and any(m in proc.stdout for m in _FAILURE_MARKERS):
        rc = 1
    return UninstallResult(
        identifier=identifier,
        name=name,
        returncode=rc,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def cleanup_disabled_policy(
    config_path: Path,
    uninstalled: list[UninstallResult],
    *,
    apply: bool = True,
    no_backup: bool = False,
) -> dict[str, Any]:
    """Remove successfully uninstalled skill names from ``skills.disabled``."""
    removed_names = {r.name for r in uninstalled if r.ok and r.name}

    config = load_yaml(config_path)
    skills_cfg = config.setdefault("skills", {})
    current_disabled = set(skills_cfg.get("disabled") or [])
    dropped = current_disabled & removed_names
    skills_cfg["disabled"] = sorted(current_disabled - removed_names)

    changed = True
    backup_path = None
    if apply:
        payload = dump_yaml(config)
        ok, err = validate_yaml(payload)
        if not ok:
            raise skill_install.SkillInstallError(
                f"produced YAML failed validation: {err}", kind="write"
            )
        outcome = write_with_backup(config_path, payload, no_backup=no_backup)
        backup_path = str(outcome.backup) if outcome.backup else None

    return {
        "removed_from_disabled": sorted(dropped),
        "config_path": str(config_path),
        "config_changed": changed,
        "backup_path": backup_path,
    }


def run(
    paths: ResolvedPaths,
    *,
    identifier: str,
    apply: bool = True,
    no_backup: bool = False,
    verbose: bool = False,
    out: Any = None,
    uninstaller: Uninstaller = default_uninstaller,
) -> dict[str, Any]:
    """Expand, uninstall, and clean up the disabled-list policy."""
    import sys

    out = sys.stderr if out is None else out

    def _say(msg: str) -> None:
        if verbose:
            out.write(msg.rstrip("\n") + "\n")
            out.flush()

    try:
        _say(f"expanding identifier {identifier!r} ...")
        identifiers = skill_install.expand_recursive_identifier(identifier)
    except skill_install.SkillInstallError as exc:
        _say(f"  expansion failed: {exc}")
        return {
            "ok": False,
            "reason": exc.kind,
            "profile": paths.profile,
            "identifier": identifier,
            "expanded": [],
            "uninstalled": [],
            "removed_from_disabled": [],
            "config_path": str(skill_install.profile_config_path(paths)),
            "error": str(exc),
            "dry_run": not apply,
        }

    _say(f"  found {len(identifiers)} skill(s): {', '.join(identifiers)}")

    uninstalled: list[UninstallResult] = []
    if apply:
        for skill_id in identifiers:
            name = skill_install.skill_name_from_identifier(skill_id)
            _say(f"uninstall {name} ({skill_id}) ...")
            uninstalled.append(uninstaller(skill_id, paths))
            last = uninstalled[-1]
            _say(f"  -> rc={last.returncode}")
    else:
        uninstalled = [
            UninstallResult(skill_id, skill_install.skill_name_from_identifier(skill_id), 0)
            for skill_id in identifiers
        ]

    failed = [r for r in uninstalled if not r.ok]
    policy: dict[str, Any] = {
        "removed_from_disabled": [],
        "config_path": str(skill_install.profile_config_path(paths)),
        "backup_path": None,
    }
    ok_uninstalled = [r for r in uninstalled if r.ok]
    if ok_uninstalled:
        _say(f"cleaning skills.disabled in {skill_install.profile_config_path(paths)} ...")
        try:
            policy = cleanup_disabled_policy(
                skill_install.profile_config_path(paths),
                ok_uninstalled,
                apply=apply,
                no_backup=no_backup,
            )
            _say(f"  removed_from_disabled={policy.get('removed_from_disabled')}")
        except skill_install.SkillInstallError as exc:
            _say(f"  config write failed: {exc}")
            return {
                "ok": False,
                "reason": exc.kind,
                "profile": paths.profile,
                "identifier": identifier,
                "expanded": identifiers,
                "uninstalled": [r.__dict__ for r in uninstalled],
                "removed_from_disabled": [],
                "config_path": str(skill_install.profile_config_path(paths)),
                "error": str(exc),
                "dry_run": not apply,
            }

    return {
        "ok": not failed and bool(ok_uninstalled),
        "reason": "dry_run" if not apply else ("uninstalled" if not failed else "uninstall"),
        "profile": paths.profile,
        "identifier": identifier,
        "expanded": identifiers,
        "uninstalled": [r.__dict__ for r in uninstalled],
        "removed_from_disabled": policy["removed_from_disabled"],
        "config_path": policy["config_path"],
        "backup_path": policy.get("backup_path"),
        "error": None if not failed else "one or more skill uninstalls failed",
        "dry_run": not apply,
    }


def render_human(report: dict[str, Any]) -> tuple[int, str]:
    """Format a recursive uninstall report for terminal output."""
    lines = ["recursive skill uninstall", "=" * 60, ""]
    lines.append(f"profile: {report.get('profile')}")
    lines.append(f"source:  {report.get('identifier')}")
    lines.append(f"config:  {report.get('config_path')}")
    lines.append("")

    if not report.get("ok"):
        lines.append(f"ERROR: recursive skill uninstall failed ({report.get('reason')}).")
        if report.get("error"):
            lines.append(f"  {report['error']}")
        for item in report.get("uninstalled") or []:
            if item.get("returncode"):
                lines.append(f"  failed: {item.get('identifier')} rc={item.get('returncode')}")
                if item.get("stderr"):
                    lines.append(f"    {str(item.get('stderr')).strip()}")
        lines.append("")
        lines.append("=" * 60)
        lines.append("VERDICT: tool error — uninstall did not complete.")
        return 2, "\n".join(lines)

    lines.append(f"Expanded:    {len(report.get('expanded') or [])} skill(s)")
    lines.append(f"Uninstalled: {len(report.get('uninstalled') or [])} skill(s)")
    if report.get("removed_from_disabled"):
        lines.append(
            "Removed from skills.disabled: " + ", ".join(report["removed_from_disabled"])
        )
    if report.get("dry_run"):
        lines.append("Dry run: no hermes uninstall commands or config writes were performed.")
    lines.append("")
    lines.append("=" * 60)
    lines.append("VERDICT: clean — recursive skill uninstall complete.")
    return 0, "\n".join(lines)


def show_resolution(paths: ResolvedPaths, *, identifier: str) -> str:
    """Return JSON showing expansion and target config path."""
    import json

    try:
        expanded = skill_install.expand_recursive_identifier(identifier)
        error = None
    except skill_install.SkillInstallError as exc:
        expanded = []
        error = {"kind": exc.kind, "message": str(exc)}
    return json.dumps(
        {
            "profile": paths.profile,
            "identifier": identifier,
            "expanded": expanded,
            "config_path": str(skill_install.profile_config_path(paths)),
            "error": error,
        },
        indent=2,
    )
