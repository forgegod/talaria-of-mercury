"""Sync ``context_length_cache.yaml`` between two profiles.

These are factual model context-window measurements. Source wins
on conflict (the source is presumed to have the more recent
measurement) and target-only entries are preserved. The merge
output is rewritten as a single YAML document with
``context_lengths`` as the top-level key — matching the file the
Hermes runtime itself consumes.
"""

from __future__ import annotations

from talaria.sync.paths import SyncProfile
from talaria.sync.result import FilePhaseResult
from talaria.sync.writer import write_with_backup
from talaria.sync.yaml_io import dump_yaml, load_yaml


FILENAME = "context_length_cache.yaml"
"""Filename within a profile root. Centralised so tests and the
renderer reference the same string."""


def sync_context_cache(
    source: SyncProfile,
    target: SyncProfile,
    *,
    apply: bool = True,
    no_backup: bool = False,
) -> FilePhaseResult:
    """Merge *source*'s context cache into *target* with source-wins-on-conflict.

    The phase returns a :class:`~talaria.sync.result.FilePhaseResult`
    with ``new_keys`` (only in source) and ``updated_keys`` (differing
    values, target gets overwritten) populated for the renderer.
    """
    result = FilePhaseResult(
        phase="context_cache",
        status="in_sync",
        target_path=target.root / FILENAME,
    )

    if not (source.root / FILENAME).exists():
        result.status = "skipped"
        result.logs.append(f"  skip: source has no {FILENAME}")
        return result

    source_data = load_yaml(source.root / FILENAME)
    source_cache = source_data.get("context_lengths", {}) or {}
    if not source_cache:
        result.status = "skipped"
        result.logs.append(f"  skip: source {FILENAME} has no context_lengths")
        return result

    target_file = target.root / FILENAME
    if not target_file.exists():
        # No target cache — copy the entire source file.
        result.status = "new"
        result.logs.append(f"  {FILENAME}: new file (copying entire source)")
        if apply:
            payload = dump_yaml({"context_lengths": source_cache})
            outcome = write_with_backup(target_file, payload, no_backup=no_backup)
            result.write_confirmed = True
            if outcome.backup:
                result.logs.append(f"  backup: {outcome.backup}")
            result.logs.append(f"  written: {outcome.written}")
        else:
            result.logs.append("  (dry run)")
        return result

    target_data = load_yaml(target_file)
    target_cache = target_data.get("context_lengths", {}) or {}

    new_keys = [k for k in source_cache if k not in target_cache]
    updated_keys = [
        k for k in source_cache if k in target_cache and source_cache[k] != target_cache[k]
    ]
    kept_count = sum(1 for k in target_cache if k not in source_cache)
    result.new_keys = new_keys
    result.updated_keys = updated_keys

    if not new_keys and not updated_keys:
        result.logs.append(f"  {FILENAME}: already in sync")
        return result

    for key in new_keys:
        result.logs.append(f"  new: {key} = {source_cache[key]}")
    for key in updated_keys:
        result.logs.append(
            f"  update: {key} = {source_cache[key]} (was {target_cache[key]})"
        )
    if kept_count:
        result.logs.append(f"  kept: {kept_count} target-only entries preserved")

    if not apply:
        result.logs.append("  (dry run)")
        return result

    merged = dict(target_cache)
    merged.update(source_cache)
    payload = dump_yaml({"context_lengths": merged})
    outcome = write_with_backup(target_file, payload, no_backup=no_backup)
    result.write_confirmed = True
    if outcome.backup:
        result.logs.append(f"  backup: {outcome.backup}")
    result.logs.append(f"  written: {outcome.written}")
    result.status = "updated"
    return result