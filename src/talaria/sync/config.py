"""Sync ``config.yaml`` between two profiles.

Supports three modes that match the standalone tool's flags:

* ``exclude`` — copy everything from source except *paths*; target
  keeps its own values for excluded paths.
* ``only`` — copy *only* the listed paths from source; everything
  else in target is preserved.
* ``identity`` — copy source as-is (the default when no
  ``--exclude`` / ``--only`` is set and ``--add-mcp-serve`` is not
  used).

When ``--add-mcp-serve`` is set the phase injects an
``mcp_servers:<name>`` entry pointing at the running Hermes SSE
endpoint, regardless of mode. Idempotent: re-running with the same
host/port reports ``already up to date``.

The phase result records which paths were excluded/only-restricted,
the unified diff (so the verbose renderer can show it), and the
backup path when bytes hit disk.
"""

from __future__ import annotations

import copy
import difflib
import shutil
from pathlib import Path
from typing import Literal

from talaria.sync.dotpath import get_path, sync_exclude, sync_only
from talaria.sync.paths import SyncProfile, mcp_serve_entry
from talaria.sync.result import ConfigPhaseResult, Status
from talaria.sync.writer import write_with_backup
from talaria.sync.yaml_io import dump_yaml, load_yaml, validate_yaml

Mode = Literal["exclude", "only", "identity"]


def sync_config(
    source: SyncProfile,
    target: SyncProfile,
    *,
    excludes: list[str] | None = None,
    only_paths: list[str] | None = None,
    add_mcp_serve: bool = False,
    mcp_serve_name: str = "hermes",
    mcp_serve_port: int = 9119,
    mcp_serve_host: str = "localhost",
    apply: bool = True,
    no_backup: bool = False,
    force: bool = False,
) -> ConfigPhaseResult | None:
    """Run the ``config.yaml`` sync phase and return its result.

    Returns ``None`` when the phase is a no-op: no ``--exclude``,
    ``--only`` or ``--add-mcp-serve`` was requested. The standalone
    tool behaved the same way — the config phase only runs when at
    least one of those flags was set. ``talaria sync`` follows that
    convention so a plain ``talaria sync src dst`` syncs everything
    *except* ``config.yaml`` (matching the operator's expectation
    that "sync" means "propagate deltas", not "nuke target").

    Parameters mirror the standalone tool's flag set; ``apply``
    defaults to ``True`` because ``talaria sync`` is write-by-default
    (the operator passes ``--dry-run`` to opt out — the CLI layer
    inverts this). See module docstring for mode semantics.
    """
    excludes = list(excludes or [])
    only_paths = list(only_paths or [])

    if excludes and only_paths:
        raise ValueError("excludes and only_paths are mutually exclusive")

    # No-op when none of the operator's flags requested config work.
    # Matches the standalone tool's "config phase only runs when at
    # least one filter is set" behaviour.
    if not excludes and not only_paths and not add_mcp_serve:
        return None

    result = ConfigPhaseResult(
        phase="config",
        status="in_sync",
        target_path=target.config_yaml,
        exclude_paths=excludes,
        only_paths=only_paths,
    )

    if not source.config_yaml.exists():
        result.status = "skipped"
        result.logs.append(f"  skip: source has no config.yaml ({source.config_yaml})")
        return result

    if (
        (excludes or only_paths or add_mcp_serve)
        and not force
        and target.config_yaml.exists()
        and source.config_yaml.stat().st_mtime_ns <= target.config_yaml.stat().st_mtime_ns
    ):
        result.status = "skipped"
        result.logs.append(
            "  skip: source config.yaml is not newer than target "
            f"({source.config_yaml} <= {target.config_yaml})"
        )
        return result

    source_data = load_yaml(source.config_yaml)
    target_data = load_yaml(target.config_yaml) if target.config_yaml.exists() else {}

    # Resolve mode and produce the merged dict.
    mode: Mode
    if only_paths:
        mode = "only"
        merged = sync_only(source_data, target_data, only_paths)
        for path in only_paths:
            if not get_path(source_data, path)[0]:
                result.logs.append(f"  warning: '{path}' not found in source — skipped")
    elif excludes:
        mode = "exclude"
        merged = sync_exclude(source_data, target_data, excludes)
    else:
        mode = "identity"
        merged = copy.deepcopy(target_data)

    # Optional: --add-mcp-serve. Inject AFTER the mode merge so the
    # entry always lands in the result, even if it was excluded by
    # --exclude mcp_servers.
    mcp_notes: list[str] = []
    if add_mcp_serve:
        entry = mcp_serve_entry(port=mcp_serve_port, host=mcp_serve_host)
        result.mcp_serve_name = mcp_serve_name
        result.mcp_serve_url = entry["url"]
        mcp_notes.append(f"endpoint: {entry['url']} (transport: {entry['transport']})")

        if "mcp_servers" not in merged or not isinstance(merged.get("mcp_servers"), dict):
            merged["mcp_servers"] = {}

        existing = merged["mcp_servers"].get(mcp_serve_name)
        if existing == entry:
            mcp_notes.append(f"entry '{mcp_serve_name}' already up to date")
        else:
            merged["mcp_servers"][mcp_serve_name] = entry
            if existing is not None:
                mcp_notes.append(f"entry '{mcp_serve_name}' updated (was different)")
            else:
                mcp_notes.append(f"entry '{mcp_serve_name}' added")

    result.mode = mode

    # Compute the diff against the existing target (if any).
    diff = _diff(target_data, merged)
    result.diff_lines = diff.splitlines()

    if not diff:
        result.status = "in_sync"
        result.logs.append("  config.yaml: no changes (already in sync)")
        if mcp_notes:
            for note in mcp_notes:
                result.logs.append(f"        {note}")
        return result

    result.status = "updated"

    if not apply:
        result.logs.append("  (dry run)")
        for note in mcp_notes:
            result.logs.append(f"        {note}")
        return result

    payload = dump_yaml(merged)
    ok, err = validate_yaml(payload)
    if not ok:
        result.status = "error"
        result.logs.append(f"  error: produced YAML failed validation: {err}")
        return result

    outcome = write_with_backup(target.config_yaml, payload, no_backup=no_backup)
    result.write_confirmed = True
    result.backup_path = outcome.backup
    if outcome.backup:
        result.logs.append(f"  backup: {outcome.backup}")
    result.logs.append(f"  written: {outcome.written}")
    for note in mcp_notes:
        result.logs.append(f"        {note}")
    return result


def _diff(old: dict, new: dict) -> str:
    """Return the unified diff between *old* and *new* (dumped as YAML).

    Empty string means no change. ``dump_yaml`` is used for both sides
    so the diff is byte-stable — semantically equivalent YAML that
    dumps differently (key order, etc.) does not appear as a change.
    """
    old_lines = dump_yaml(old).splitlines(keepends=True)
    new_lines = dump_yaml(new).splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="target (current)",
        tofile="target (after sync)",
        n=1,
    )
    return "".join(diff)


def list_config_paths(profile: SyncProfile, *, max_depth: int = 2) -> list[str]:
    """Return dot-notation paths in *profile*'s ``config.yaml``.

    Used by ``--list`` on the ``talaria sync`` CLI. Returns an empty
    list when the file is missing — the CLI surfaces that as a
    human-readable warning.
    """
    if not profile.config_yaml.exists():
        return []
    return _list_keys(load_yaml(profile.config_yaml), max_depth=max_depth)


def _list_keys(data: dict, *, max_depth: int) -> list[str]:
    """Walk *data* down to *max_depth* and return dot-notation paths.

    Thin wrapper over :func:`talaria.sync.dotpath.list_keys` retained
    here so the ``config`` phase owns its introspection surface.
    """
    from talaria.sync.dotpath import list_keys
    return list_keys(data, max_depth=max_depth)
