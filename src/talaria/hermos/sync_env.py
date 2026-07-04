"""Update a Hermes profile ``.env`` from the live environment.

The operator keeps credential variables in a shell-profile secrets file
that is sourced into the environment. This feature mirrors that source of
truth into a Hermes profile's ``.env``: for every key already present in
the target file, the value is overwritten with the matching value from
``os.environ``. Keys that are absent from the target file are **never
added** — the profile keeps the exact variable set the operator defined,
only the values are refreshed.

Contrast with :mod:`talaria.sync.env`, the sync phase that *adds* missing
variables from a source profile. That phase is additive (target wins on
conflict). This feature is the opposite direction: the live environment is
authoritative for values, but the target file defines the variable
*scope*. The two never run together — one copies variables between
profiles, the other refreshes values from the operator's shell.

Opt-in key operations
---------------------

Pass ``add_keys`` (CLI: ``--add-key``, repeatable) to extend the file's
variable *scope* from the live environment. Each named key that is absent
from the file **and** present in the environment with a non-empty value is
appended as a new ``KEY=value`` line. This deliberately lifts the
"never adds new keys" default — it is an explicit per-invocation action.
Keys already present in the file are **not** re-added; they flow through
the normal refresh path. With no ``add_keys`` the behaviour is identical
to the value-only refresh described above.

Pass ``skip_keys`` (CLI: ``--skip-key``, repeatable) to keep specific keys
out of the env-value refresh. A skipped key's file value is preserved
as-is even when the environment has a different value.

Pass ``disable_keys`` (CLI: ``--disable-key``, repeatable) to comment a
key out: ``KEY=value`` becomes ``#KEY=value``. Disabled keys are naturally
hidden from the refresh scan (they no longer match an assignment line),
so they keep their value while inactive. The change is reversible with
``enable_keys``.

Pass ``enable_keys`` (CLI: ``--enable-key``, repeatable) to uncomment a
previously disabled key: ``#KEY=value`` becomes ``KEY=value``. The key is
also excluded from the env-value refresh on the same run so its stored
value is the one restored verbatim.

All four option sets are orthogonal and processed in a single line scan.
Each operation reports per-key results and a ``skipped`` list (with
reasons) so the operator can see what was applied and what was a no-op.

Writes go through :func:`talaria.sync.writer.write_with_backup` (atomic
temp + rename, optional ``.bak``), matching the rest of the write-bearing
Talaria features.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

from talaria.paths import DEFAULT_PROFILE_NAME, ResolvedPaths
from talaria.sync.writer import write_with_backup

#: Regex for a line that assigns to a shell-style variable. Captures the
#: variable name in group 1 and the rest of the line (the value) in group 2.
#: An optional leading ``export`` is tolerated. Keys must start with a
#: letter or underscore and contain only ``[A-Za-z0-9_]`` — the same shape
#: the shell script used.
_ENV_LINE_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

#: Regex for a line that has been commented out. Captures an optional
#: ``export`` (group 1), the variable name (group 2), and the value
#: (group 3). Any leading whitespace is tolerated; exactly one ``#``
#: marks the line as disabled.
_DISABLED_LINE_RE = re.compile(
    r"^\s*#(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$"
)

#: A bare variable name (no ``=value``). Used to validate operator-supplied
#: key names (``add_keys``, ``skip_keys``, ...) before they are acted on.
_KEY_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def profile_env_path(paths: ResolvedPaths) -> Path:
    """Return the ``.env`` path for ``paths.profile``."""
    if paths.profile == DEFAULT_PROFILE_NAME:
        return paths.hermes_root / ".env"
    return paths.hermes_root / "profiles" / paths.profile / ".env"


def _parse_keys(path: Path) -> list[str]:
    """Return the variable names defined in *path*, in file order.

    A variable is any line matching :data:`_ENV_LINE_RE`. Comments,
    blanks, and ``KEY:`` style lines are ignored. Order is preserved so
    the operator can scan the ``updated`` list in file order.
    """
    if not path.exists():
        return []
    keys: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ENV_LINE_RE.match(line.strip())
        if m and m.group(1) not in seen:
            keys.append(m.group(1))
            seen.add(m.group(1))
    return keys


def _parse_disabled_keys(path: Path) -> list[str]:
    """Return the variable names commented out in *path*, in file order.

    A disabled key is any line matching :data:`_DISABLED_LINE_RE`
    (e.g. ``#KEY=value`` or ``# export KEY=value``). Plain comment lines
    such as ``# a note`` do not match because there is no ``=``.
    """
    if not path.exists():
        return []
    keys: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _DISABLED_LINE_RE.match(line)
        if m and m.group(1) not in seen:
            keys.append(m.group(1))
            seen.add(m.group(1))
    return keys


def _refresh_lines(path: Path, env: dict[str, str]) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]], list[str]]:
    """Walk *path* and compute the refreshed line set.

    Returns ``(updated, unchanged, absent)``:

    * ``updated`` — ``(key, old_value, new_value)`` triples where the
      environment had a non-empty value and it differed.
    * ``unchanged`` — ``(key, value)`` pairs where the env value matched
      the file value (or the env value was empty and the file value is
      being kept as-is).
    * ``absent`` — keys present in the file but missing from *env*; the
      file value is preserved unchanged.
    """
    updated: list[tuple[str, str, str]] = []
    unchanged: list[tuple[str, str]] = []
    absent: list[str] = []
    if not path.exists():
        return updated, unchanged, absent
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        m = _ENV_LINE_RE.match(stripped)
        if not m:
            continue
        key = m.group(1)
        old_value = m.group(2)
        if key not in env:
            absent.append(key)
            continue
        new_value = env[key]
        if not new_value:
            # Empty env value: keep the file value (mirrors the shell
            # script's `[ -z "${var_value}" ] && return` skip).
            unchanged.append((key, old_value))
            continue
        if new_value == old_value:
            unchanged.append((key, old_value))
        else:
            updated.append((key, old_value, new_value))
    return updated, unchanged, absent


def _rewrite_file(path: Path, env: dict[str, str]) -> str:
    """Return the new content of *path* with env values applied.

    Only ``KEY=...`` lines whose key is in *env* with a non-empty value
    are rewritten. Comments, blanks, and non-matching lines are preserved
    verbatim. The ``export`` prefix (if present) is retained on the line.
    """
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        m = _ENV_LINE_RE.match(stripped)
        if m:
            key = m.group(1)
            value = env.get(key, "")
            if value and value != m.group(2):
                # Preserve leading ``export`` and the original key spelling.
                prefix = "export " if stripped.startswith("export ") else ""
                out.append(f"{prefix}{key}={value}")
                continue
        out.append(line)
    return "\n".join(out) + ("\n" if path.read_text(encoding="utf-8").endswith("\n") else "")


def _resolve_adds(
    existing_keys: set[str],
    disabled_keys: set[str],
    env: dict[str, str],
    add_keys: Iterable[str],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Decide which ``add_keys`` become new file lines.

    Returns ``(added, skipped)``:

    * ``added`` — ``(key, value)`` pairs to append: the key is a valid
      name, absent from the file (both active and disabled), and present
      in *env* with a non-empty value.
    * ``skipped`` — ``(key, reason)`` pairs. Reasons are
      ``already-present``, ``already-disabled``, ``not-in-env``, and
      ``invalid-name``. Duplicate requests collapse to the first
      occurrence.

    Keys already defined in the file (active or disabled) are never
    re-added — active keys are handled by the normal refresh path and
    disabled keys should be re-enabled, not duplicated.
    """
    added: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in add_keys:
        if key in seen:
            continue
        seen.add(key)
        if not _KEY_NAME_RE.match(key):
            skipped.append((key, "invalid-name"))
            continue
        if key in existing_keys:
            skipped.append((key, "already-present"))
            continue
        if key in disabled_keys:
            skipped.append((key, "already-disabled"))
            continue
        if key not in env:
            skipped.append((key, "not-in-env"))
            continue
        value = env[key]
        if not value:
            skipped.append((key, "empty-value"))
            continue
        added.append((key, value))
    return added, skipped


def _resolve_simple(
    requested: Iterable[str],
    present: set[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Resolve a key list against the keys it should apply to.

    Used for ``skip_keys`` — a repeatable operator-supplied list
    validated against the set of active keys.

    Returns ``(applied, skipped)`` where ``skipped`` entries are
    ``(key, reason)`` with reasons ``not-found`` or ``invalid-name``.
    Duplicates collapse to the first occurrence.
    """
    applied: list[str] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in requested:
        if key in seen:
            continue
        seen.add(key)
        if not _KEY_NAME_RE.match(key):
            skipped.append((key, "invalid-name"))
            continue
        if key not in present:
            skipped.append((key, "not-found"))
            continue
        applied.append(key)
    return applied, skipped


def _resolve_disable(
    requested: Iterable[str],
    active_keys: set[str],
    disabled_keys: set[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Resolve ``disable_keys`` against active and disabled key sets."""
    applied: list[str] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in requested:
        if key in seen:
            continue
        seen.add(key)
        if not _KEY_NAME_RE.match(key):
            skipped.append((key, "invalid-name"))
            continue
        if key in disabled_keys:
            skipped.append((key, "already-disabled"))
            continue
        if key not in active_keys:
            skipped.append((key, "not-found"))
            continue
        applied.append(key)
    return applied, skipped


def _resolve_enable(
    requested: Iterable[str],
    disabled_keys: set[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Resolve ``enable_keys`` against the disabled key set."""
    applied: list[str] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in requested:
        if key in seen:
            continue
        seen.add(key)
        if not _KEY_NAME_RE.match(key):
            skipped.append((key, "invalid-name"))
            continue
        if key not in disabled_keys:
            skipped.append((key, "not-disabled"))
            continue
        applied.append(key)
    return applied, skipped


def _format_added_block(added: list[tuple[str, str]]) -> str:
    """Return the ``KEY=value\\n`` lines to append for *added*."""
    return "".join(f"{k}={v}\n" for k, v in added)


def _build_payload(
    env_file: Path,
    env: dict[str, str],
    *,
    skip_keys: set[str],
    disable_keys: set[str],
    enable_keys: set[str],
    added: list[tuple[str, str]],
) -> str:
    """Compute the new file content, applying all operations in one scan.

    Order of precedence on a single original line (each line is touched
    at most once):

    * an active assignment ``KEY=v`` with ``KEY`` in *disable_keys* →
      ``#KEY=v`` (the ``export`` prefix, if any, is dropped from the
      commented form);
    * an active assignment whose ``KEY`` is in *skip_keys* or
      *enable_keys* → preserved verbatim (skip keeps the file value;
      enable does not apply to active lines);
    * an active assignment otherwise → normal env-value refresh;
    * a commented assignment ``#KEY=v`` with ``KEY`` in *enable_keys* →
      ``KEY=v`` (value restored verbatim, ``export`` prefix not
      reintroduced);
    * any other line → preserved verbatim.

    *added* keys are appended after the scanned content as a trailing
    block (separated by a newline when the content does not already end
    with one).
    """
    original = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    out: list[str] = []
    for line in original.splitlines():
        stripped = line.strip()
        m_active = _ENV_LINE_RE.match(stripped)
        if m_active:
            key = m_active.group(1)
            if key in disable_keys:
                out.append(f"#{key}={m_active.group(2)}")
                continue
            if key in skip_keys or key in enable_keys:
                # Preserve the file value verbatim.
                out.append(line)
                continue
            value = env.get(key, "")
            if value and value != m_active.group(2):
                prefix = "export " if stripped.startswith("export ") else ""
                out.append(f"{prefix}{key}={value}")
                continue
            out.append(line)
            continue
        m_disabled = _DISABLED_LINE_RE.match(line)
        if m_disabled:
            key = m_disabled.group(1)
            if key in enable_keys:
                out.append(f"{key}={m_disabled.group(2)}")
                continue
        out.append(line)

    payload = "\n".join(out)
    if original.endswith("\n"):
        payload += "\n"
    if added:
        if payload and not payload.endswith("\n"):
            payload += "\n"
        payload += _format_added_block(added)
    return payload


def sync_env(
    env_file: Path,
    *,
    env: dict[str, str] | None = None,
    apply: bool = True,
    no_backup: bool = False,
    add_keys: Iterable[str] | None = None,
    skip_keys: Iterable[str] | None = None,
    disable_keys: Iterable[str] | None = None,
    enable_keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Refresh values in *env_file* from the live environment.

    Parameters
    ----------
    env_file:
        Target ``.env`` path. A missing file is a no-op *unless*
        ``add_keys`` names at least one addable key, in which case the
        file is created with those keys.
    env:
        Override the source dictionary. Defaults to ``os.environ`` cast
        to ``str``. Tests pass an explicit mapping.
    apply:
        When ``False``, compute the report but write nothing.
    no_backup:
        Skip the ``.bak`` step when writing.
    add_keys:
        Iterable of variable names to append to the file when they are
        absent and present in *env* with a non-empty value. ``None`` or
        empty preserves the value-only refresh semantics.
    skip_keys:
        Iterable of variable names to exclude from the env-value
        refresh; their file values are preserved as-is.
    disable_keys:
        Iterable of active variable names to comment out
        (``KEY=value`` → ``#KEY=value``).
    enable_keys:
        Iterable of commented-out variable names to uncomment
        (``#KEY=value`` → ``KEY=value``).
    """
    env_file = Path(env_file)
    source = env if env is not None else dict(os.environ)
    keys_to_add = list(add_keys) if add_keys else []
    keys_to_skip = list(skip_keys) if skip_keys else []
    keys_to_disable = list(disable_keys) if disable_keys else []
    keys_to_enable = list(enable_keys) if enable_keys else []

    report: dict[str, Any] = {
        "ok": True,
        "changed": False,
        "dry_run": not apply,
        "env_file": str(env_file),
        "updated": [],
        "unchanged": [],
        "absent": [],
        "added": [],
        "add_skipped": [],
        "skipped": [],
        "skip_skipped": [],
        "disabled": [],
        "disable_skipped": [],
        "enabled": [],
        "enable_skipped": [],
        "write_confirmed": False,
        "backup": None,
        "written": None,
    }

    file_exists = env_file.exists()
    if file_exists:
        active = set(_parse_keys(env_file))
        disabled = set(_parse_disabled_keys(env_file))
        updated, unchanged, absent = _refresh_lines(env_file, source)
    else:
        active = set()
        disabled = set()
        updated, unchanged, absent = [], [], []

    # add_keys are excluded from both active and disabled sets.
    added, add_skipped = _resolve_adds(active, disabled, source, keys_to_add)
    # skip_keys apply only to active keys.
    skip_applied, skip_skipped = _resolve_simple(
        keys_to_skip, active,
    )
    disable_applied, disable_skipped = _resolve_disable(
        keys_to_disable, active, disabled,
    )
    enable_applied, enable_skipped = _resolve_enable(
        keys_to_enable, disabled,
    )

    # Re-classify updated/unchanged/absent in light of skip/disable so the
    # report reflects the *effective* refresh. A skipped or disabled key
    # is removed from updated and counted under its operation instead.
    skip_set = set(skip_applied)
    disable_set = set(disable_applied)
    effective_updated: list[tuple[str, str, str]] = []
    effective_unchanged: list[tuple[str, str]] = []
    effective_absent: list[str] = []
    for k, o, n in updated:
        if k in skip_set or k in disable_set:
            continue
        effective_updated.append((k, o, n))
    for k, v in unchanged:
        if k in skip_set or k in disable_set:
            continue
        effective_unchanged.append((k, v))
    for k in absent:
        if k in skip_set or k in disable_set:
            continue
        effective_absent.append(k)

    report["updated"] = [{"key": k, "old": o, "new": n} for k, o, n in effective_updated]
    report["unchanged"] = [k for k, _ in effective_unchanged]
    report["absent"] = effective_absent
    report["added"] = [{"key": k, "value": v} for k, v in added]
    report["add_skipped"] = [{"key": k, "reason": r} for k, r in add_skipped]
    report["skipped"] = skip_applied
    report["skip_skipped"] = [{"key": k, "reason": r} for k, r in skip_skipped]
    report["disabled"] = disable_applied
    report["disable_skipped"] = [{"key": k, "reason": r} for k, r in disable_skipped]
    report["enabled"] = enable_applied
    report["enable_skipped"] = [{"key": k, "reason": r} for k, r in enable_skipped]

    changed = bool(
        effective_updated or added or disable_applied or enable_applied
    )
    if not changed:
        return report

    report["changed"] = True
    if not apply:
        return report

    payload = _build_payload(
        env_file,
        source,
        skip_keys=skip_set,
        disable_keys=disable_set,
        enable_keys=set(enable_applied),
        added=added,
    )
    outcome = write_with_backup(env_file, payload, no_backup=no_backup)
    report["write_confirmed"] = True
    report["backup"] = str(outcome.backup) if outcome.backup else None
    report["written"] = str(outcome.written)
    return report


def run(
    paths: ResolvedPaths,
    *,
    env_file: Path | None = None,
    env: dict[str, str] | None = None,
    apply: bool = True,
    no_backup: bool = False,
    add_keys: Iterable[str] | None = None,
    skip_keys: Iterable[str] | None = None,
    disable_keys: Iterable[str] | None = None,
    enable_keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Refresh the selected profile's ``.env`` from the environment.

    ``env`` defaults to ``os.environ``; tests pass an explicit mapping.
    ``add_keys``, ``skip_keys``, ``disable_keys``, and ``enable_keys``
    are forwarded to :func:`sync_env`.
    """
    path = Path(env_file) if env_file else profile_env_path(paths)
    report = sync_env(
        path,
        env=env,
        apply=apply,
        no_backup=no_backup,
        add_keys=add_keys,
        skip_keys=skip_keys,
        disable_keys=disable_keys,
        enable_keys=enable_keys,
    )
    report["profile"] = paths.profile
    return report


def render_human(report: dict[str, Any]) -> tuple[int, str]:
    """Format a sync-env report for the terminal."""
    lines: list[str] = [
        "Hermes .env refresh from environment",
        "=" * 60,
        "",
        f"profile: {report.get('profile', '(custom path)')}",
        f".env:    {report['env_file']}",
        "",
    ]

    has_activity = (
        report["updated"] or report["unchanged"] or report["absent"]
        or report["added"] or report["add_skipped"]
        or report["skipped"] or report["skip_skipped"]
        or report["disabled"] or report["disable_skipped"]
        or report["enabled"] or report["enable_skipped"]
    )
    if not has_activity:
        lines.append("Target .env has no variable lines.")
        lines.append("")
        lines.append("=" * 60)
        lines.append("VERDICT: clean — nothing to refresh.")
        return 0, "\n".join(lines)

    updated = report["updated"]
    if updated:
        lines.append(f"updated ({len(updated)}):")
        for entry in updated:
            lines.append(f"  {entry['key']}")

    added = report["added"]
    if added:
        lines.append(f"added ({len(added)}):")
        for entry in added:
            lines.append(f"  {entry['key']}")

    if report["skipped"]:
        lines.append(f"skipped from refresh ({len(report['skipped'])}):")
        for key in report["skipped"]:
            lines.append(f"  {key}")

    if report["disabled"]:
        lines.append(f"disabled ({len(report['disabled'])}):")
        for key in report["disabled"]:
            lines.append(f"  {key}")

    if report["enabled"]:
        lines.append(f"enabled ({len(report['enabled'])}):")
        for key in report["enabled"]:
            lines.append(f"  {key}")

    if report["unchanged"]:
        lines.append(f"unchanged: {len(report['unchanged'])}")

    if report["absent"]:
        lines.append(f"absent from env (preserved): {len(report['absent'])}")

    for label, field in (
        ("add skipped", "add_skipped"),
        ("skip skipped", "skip_skipped"),
        ("disable skipped", "disable_skipped"),
        ("enable skipped", "enable_skipped"),
    ):
        entries = report[field]
        if entries:
            lines.append(f"{label} ({len(entries)}):")
            for entry in entries:
                lines.append(f"  {entry['key']} ({entry['reason']})")

    if report["dry_run"]:
        lines.append("dry-run: no bytes written")
    elif report["write_confirmed"]:
        if report.get("backup"):
            lines.append(f"backup: {report['backup']}")
        lines.append(f"written: {report['written']}")

    lines.append("")
    lines.append("=" * 60)
    if not report["ok"]:
        verdict = "error"
    elif report["dry_run"]:
        verdict = "preview only"
    elif report["changed"]:
        verdict_parts = []
        if report["updated"]:
            verdict_parts.append("values refreshed")
        if report["added"]:
            verdict_parts.append("keys added")
        if report["disabled"]:
            verdict_parts.append("keys disabled")
        if report["enabled"]:
            verdict_parts.append("keys enabled")
        verdict = ", ".join(verdict_parts) if verdict_parts else "changed"
    else:
        verdict = "clean — no changes needed"
    lines.append(f"VERDICT: {verdict}.")
    return (0 if report["ok"] else 2), "\n".join(lines)


def show_resolution(
    paths: ResolvedPaths,
    *,
    env_file: Path | None = None,
    add_keys: Iterable[str] | None = None,
    skip_keys: Iterable[str] | None = None,
    disable_keys: Iterable[str] | None = None,
    enable_keys: Iterable[str] | None = None,
) -> str:
    """Return a JSON resolution descriptor for debugging.

    Lists the target keys and which ones the current environment would
    update, plus the resolution of every requested operation. Values are
    **not** echoed to avoid leaking secrets into logs.
    """
    path = Path(env_file) if env_file else profile_env_path(paths)
    env = dict(os.environ)
    updated, unchanged, absent = _refresh_lines(path, env)
    active = set(_parse_keys(path)) if path.exists() else set()
    disabled = set(_parse_disabled_keys(path)) if path.exists() else set()
    would_add, add_skipped = _resolve_adds(active, disabled, env, add_keys or [])
    skip_applied, skip_skipped = _resolve_simple(
        skip_keys or [], active,
    )
    disable_applied, disable_skipped = _resolve_disable(
        disable_keys or [], active, disabled,
    )
    enable_applied, enable_skipped = _resolve_enable(
        enable_keys or [], disabled,
    )
    return json.dumps(
        {
            "profile": paths.profile,
            "env_file": str(path),
            "active_keys": sorted(active),
            "disabled_keys": sorted(disabled),
            "would_update": [k for k, _, _ in updated],
            "would_add": [k for k, _ in would_add],
            "would_skip": skip_applied,
            "would_disable": disable_applied,
            "would_enable": enable_applied,
            "unchanged": [k for k, _ in unchanged],
            "absent_from_env": absent,
            "add_skipped": [{"key": k, "reason": r} for k, r in add_skipped],
            "skip_skipped": [{"key": k, "reason": r} for k, r in skip_skipped],
            "disable_skipped": [{"key": k, "reason": r} for k, r in disable_skipped],
            "enable_skipped": [{"key": k, "reason": r} for k, r in enable_skipped],
        },
        indent=2,
    )
