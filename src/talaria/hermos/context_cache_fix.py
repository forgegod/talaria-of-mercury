"""Fix known-bad Hermes context length cache entries.

Hermes stores per-model context windows in ``context_length_cache.yaml``
under each profile root. When a provider bug writes a too-small or stale
value, later sessions can keep reusing that value even after Hermes itself
is fixed. This feature applies a small, source-backed repair table to the
selected profile cache.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from talaria.paths import DEFAULT_PROFILE_NAME, ResolvedPaths
from talaria.sync.writer import write_with_backup
from talaria.sync.yaml_io import dump_yaml, load_yaml

FILENAME = "context_length_cache.yaml"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"

#: Curated repairs for cache entries known to be poisoned or stale in Hermes.
#: Keys match Hermes' persistent cache format: ``<model>@<base_url>``.
KNOWN_CONTEXT_FIXES: dict[str, int] = {
    # NousResearch/hermes-agent#5173: Codex-backed gpt-5.4 could persist
    # 32k/272k even though the OpenAI model context is 1,050,000.
    f"gpt-5.4@{CODEX_BASE_URL}": 1_050_000,
    # NousResearch/hermes-agent#27918 was closed not-planned: GPT-5.5 on
    # Codex is provider-capped around 272k, so normalize stale 1M-ish cache
    # entries back to the currently accepted Codex value.
    f"gpt-5.5@{CODEX_BASE_URL}": 272_000,
}


def profile_cache_path(paths: ResolvedPaths) -> Path:
    """Return the ``context_length_cache.yaml`` path for ``paths.profile``."""
    if paths.profile == DEFAULT_PROFILE_NAME:
        return paths.hermes_root / FILENAME
    return paths.hermes_root / "profiles" / paths.profile / FILENAME


def _coerce_context_lengths(data: Any) -> dict[str, int]:
    """Return a shallow int-only copy of ``data.context_lengths``."""
    if not isinstance(data, dict):
        return {}
    raw = data.get("context_lengths") or {}
    if not isinstance(raw, dict):
        return {}

    out: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        try:
            out[key] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def apply_fixes(
    cache_path: Path,
    *,
    fixes: dict[str, int] | None = None,
    apply: bool = True,
    no_backup: bool = False,
    create_missing: bool = True,
) -> dict[str, Any]:
    """Apply known context cache repairs to ``cache_path``.

    The repair table is authoritative: missing keys are inserted when
    ``create_missing`` is true; present keys with different values are
    updated; unrelated target-only entries are preserved.
    """
    cache_path = Path(cache_path)
    fixes = fixes or KNOWN_CONTEXT_FIXES
    existing = _coerce_context_lengths(load_yaml(cache_path))

    new_keys = [key for key in fixes if key not in existing]
    updated_keys = [key for key, value in fixes.items() if key in existing and existing[key] != value]
    changed = bool(updated_keys or (create_missing and new_keys))

    report: dict[str, Any] = {
        "ok": True,
        "changed": changed,
        "dry_run": not apply,
        "cache_path": str(cache_path),
        "new_keys": new_keys if create_missing else [],
        "updated_keys": updated_keys,
        "kept_count": sum(1 for key in existing if key not in fixes),
        "write_confirmed": False,
        "backup": None,
        "written": None,
        "context_lengths": dict(existing),
        "fixes": dict(fixes),
    }

    if not changed:
        return report

    merged = dict(existing)
    if create_missing:
        for key in new_keys:
            merged[key] = fixes[key]
    for key in updated_keys:
        merged[key] = fixes[key]
    report["context_lengths"] = merged

    if apply:
        outcome = write_with_backup(
            cache_path,
            dump_yaml({"context_lengths": merged}),
            no_backup=no_backup,
        )
        report["write_confirmed"] = True
        report["backup"] = str(outcome.backup) if outcome.backup else None
        report["written"] = str(outcome.written)
    return report


def run(
    paths: ResolvedPaths,
    *,
    cache_path: Path | None = None,
    apply: bool = True,
    no_backup: bool = False,
    create_missing: bool = True,
) -> dict[str, Any]:
    """Fix the selected profile's context length cache."""
    path = Path(cache_path) if cache_path else profile_cache_path(paths)
    report = apply_fixes(
        path,
        apply=apply,
        no_backup=no_backup,
        create_missing=create_missing,
    )
    report["profile"] = paths.profile
    return report


def render_human(report: dict[str, Any]) -> tuple[int, str]:
    """Format a context-cache repair report."""
    lines = [
        "Hermes context length cache fix",
        "=" * 60,
        "",
        f"profile: {report.get('profile', '(custom path)')}",
        f"cache:   {report['cache_path']}",
        "",
    ]

    if not report["changed"]:
        lines.append("Cache already matches the known-fix table.")
        lines.append("")
        lines.append("=" * 60)
        lines.append("VERDICT: clean — no changes needed.")
        return 0, "\n".join(lines)

    for key in report["new_keys"]:
        lines.append(f"new:    {key} = {report['fixes'][key]}")
    for key in report["updated_keys"]:
        lines.append(f"update: {key} = {report['fixes'][key]}")
    if report["kept_count"]:
        lines.append(f"kept:   {report['kept_count']} unrelated entries preserved")

    if report["dry_run"]:
        lines.append("dry-run: no bytes written")
    else:
        if report.get("backup"):
            lines.append(f"backup: {report['backup']}")
        lines.append(f"written: {report['written']}")

    lines.append("")
    lines.append("=" * 60)
    verdict = "preview only" if report["dry_run"] else "cache repaired"
    lines.append(f"VERDICT: clean — {verdict}.")
    return 0, "\n".join(lines)


def show_resolution(paths: ResolvedPaths, *, cache_path: Path | None = None) -> str:
    """Return a JSON resolution descriptor for debugging."""
    import json

    path = Path(cache_path) if cache_path else profile_cache_path(paths)
    return json.dumps(
        {
            "profile": paths.profile,
            "cache_path": str(path),
            "known_fixes": KNOWN_CONTEXT_FIXES,
        },
        indent=2,
    )
