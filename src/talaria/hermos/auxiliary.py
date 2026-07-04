"""Derive ``model.aliases._<usecase>`` from a profile's ``auxiliary`` block.

Hermes profiles can pin per-usecase models under
``auxiliary.<usecase>.model``. This feature reads the *same* profile's
``config.yaml`` and surfaces those pins as top-level
``model.aliases._<usecase>`` entries so the running profile can reference
them by name. Unlike the retired ``talaria sync`` phase (which copied
auxiliary pins from a *source* profile onto a *target*), this operates on
a single profile's own config — no source/target split.

Semantics:

* Reads ``auxiliary`` from the profile's ``config.yaml``. Every usecase
  whose ``model`` value is not one of the "no override" sentinels
  (``auto``, ``inherit``, ``default``, ...) becomes an alias named
  ``_<usecase>`` under ``model.aliases``.
* Existing ``model.aliases`` keys not in scope are preserved verbatim —
  this feature never clobbers operator-defined aliases.
* No-op when the config has no ``auxiliary`` block, or every usecase uses
  a sentinel/empty value.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from talaria.paths import DEFAULT_PROFILE_NAME, ResolvedPaths
from talaria.sync.writer import write_with_backup
from talaria.sync.yaml_io import dump_yaml, load_yaml, validate_yaml

AUX_AUTO_VALUES = frozenset({
    "auto",
    "inherit",
    "default",
    "use_default",
    "use_model",
    "use_provider",
})
"""Lowercase strings treated as "no specific model" in auxiliary blocks.

The exact word used by the operator may vary ("auto", "inherit",
"default"); the tool considers any of these as a non-overriding value
and skips alias creation for that usecase. Lower-cased for
case-insensitive comparison.
"""


def profile_config_path(paths: ResolvedPaths) -> Path:
    """Return the ``config.yaml`` path for ``paths.profile``."""
    if paths.profile == DEFAULT_PROFILE_NAME:
        return paths.hermes_root / "config.yaml"
    return paths.hermes_root / "profiles" / paths.profile / "config.yaml"


def _collect_auxiliary_aliases(data: Any) -> dict[str, str]:
    """Return ``{_<usecase>: model}`` for every concrete auxiliary pin.

    Usecases whose ``model`` is missing, empty, or one of
    :data:`AUX_AUTO_VALUES` are skipped.
    """
    if not isinstance(data, dict):
        return {}
    auxiliary = data.get("auxiliary")
    if not isinstance(auxiliary, dict) or not auxiliary:
        return {}

    out: dict[str, str] = {}
    for usecase, aux in sorted(auxiliary.items()):
        if not isinstance(aux, dict):
            continue
        model = aux.get("model")
        if not isinstance(model, str):
            continue
        if not model.strip():
            continue
        if model.strip().lower() in AUX_AUTO_VALUES:
            continue
        out[f"_{usecase}"] = model
    return out


def _inject_aliases(merged: dict, aliases: dict[str, str]) -> None:
    """Write *aliases* into ``merged["model"]["aliases"]`` in place."""
    model_block = merged.setdefault("model", {})
    if not isinstance(model_block, dict):
        return
    target_aliases = model_block.setdefault("aliases", {})
    if not isinstance(target_aliases, dict):
        # Existing model.aliases is a scalar — config error; surface as
        # no-op rather than clobbering arbitrary data.
        return
    for key, value in aliases.items():
        target_aliases[key] = value


def apply_auxiliary(
    config_path: Path,
    *,
    apply: bool = True,
    no_backup: bool = False,
) -> dict[str, Any]:
    """Derive ``model.aliases`` from ``auxiliary`` in *config_path*.

    Reads the file, computes the alias set, merges it into the existing
    ``model.aliases`` (preserving unrelated keys), and writes back via
    the atomic backup writer when *apply* is true.
    """
    config_path = Path(config_path)
    data = load_yaml(config_path) if config_path.exists() else {}
    if not isinstance(data, dict):
        data = {}

    aliases = _collect_auxiliary_aliases(data)

    report: dict[str, Any] = {
        "ok": True,
        "changed": False,
        "dry_run": not apply,
        "config_path": str(config_path),
        "aliases": aliases,
        "added": [],
        "updated": [],
        "kept": [],
        "preserved": [],
        "write_confirmed": False,
        "backup": None,
        "written": None,
    }

    if not aliases:
        return report

    target_aliases: dict = {}
    target_model = data.get("model") if isinstance(data.get("model"), dict) else {}
    existing = target_model.get("aliases") if isinstance(target_model, dict) else None
    if isinstance(existing, dict):
        target_aliases = existing

    for key in sorted(aliases):
        if key not in target_aliases:
            report["added"].append(key)
        elif target_aliases[key] != aliases[key]:
            report["updated"].append(key)
        else:
            report["kept"].append(key)
    for key in sorted(target_aliases):
        if key not in aliases:
            report["preserved"].append(key)

    report["changed"] = bool(report["added"] or report["updated"])
    if not report["changed"]:
        return report

    merged = dict(data)
    _inject_aliases(merged, aliases)

    payload = dump_yaml(merged)
    ok, err = validate_yaml(payload)
    if not ok:
        report["ok"] = False
        report["error"] = f"produced YAML failed validation: {err}"
        return report

    if apply:
        outcome = write_with_backup(config_path, payload, no_backup=no_backup)
        report["write_confirmed"] = True
        report["backup"] = str(outcome.backup) if outcome.backup else None
        report["written"] = str(outcome.written)
    return report


def run(
    paths: ResolvedPaths,
    *,
    config_path: Path | None = None,
    apply: bool = True,
    no_backup: bool = False,
) -> dict[str, Any]:
    """Apply auxiliary-derived aliases to the selected profile."""
    path = Path(config_path) if config_path else profile_config_path(paths)
    report = apply_auxiliary(path, apply=apply, no_backup=no_backup)
    report["profile"] = paths.profile
    return report


def render_human(report: dict[str, Any]) -> tuple[int, str]:
    """Format an auxiliary-alias report."""
    lines = [
        "Hermes auxiliary -> model.aliases",
        "=" * 60,
        "",
        f"profile: {report.get('profile', '(custom path)')}",
        f"config:  {report['config_path']}",
        "",
    ]

    if not report["aliases"]:
        lines.append("No auxiliary pins found (no auxiliary block or all sentinels).")
        lines.append("")
        lines.append("=" * 60)
        lines.append("VERDICT: clean — no changes needed.")
        return 0, "\n".join(lines)

    for key in report["added"]:
        lines.append(f"new:    {key} = {report['aliases'][key]}")
    for key in report["updated"]:
        lines.append(f"update: {key} = {report['aliases'][key]}")
    for key in report["kept"]:
        lines.append(f"ok:     {key} = {report['aliases'][key]}")
    if report["preserved"]:
        lines.append(f"kept:   {len(report['preserved'])} unrelated aliases preserved")

    if report["dry_run"]:
        lines.append("dry-run: no bytes written")
    elif report["write_confirmed"]:
        if report.get("backup"):
            lines.append(f"backup: {report['backup']}")
        lines.append(f"written: {report['written']}")
    elif not report["ok"]:
        lines.append(f"error: {report.get('error', 'unknown')}")

    lines.append("")
    lines.append("=" * 60)
    if not report["ok"]:
        verdict = "error"
    elif report["dry_run"]:
        verdict = "preview only"
    elif report["changed"]:
        verdict = "aliases applied"
    else:
        verdict = "clean — no changes needed"
    lines.append(f"VERDICT: {verdict}.")
    return (0 if report["ok"] else 2), "\n".join(lines)


def show_resolution(paths: ResolvedPaths, *, config_path: Path | None = None) -> str:
    """Return a JSON resolution descriptor for debugging."""
    path = Path(config_path) if config_path else profile_config_path(paths)
    data = load_yaml(path) if path.exists() else {}
    return json.dumps(
        {
            "profile": paths.profile,
            "config_path": str(path),
            "would_derive": _collect_auxiliary_aliases(data),
            "sentinels": sorted(AUX_AUTO_VALUES),
        },
        indent=2,
    )
