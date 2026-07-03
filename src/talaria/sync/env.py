"""Sync ``.env`` between two profiles.

Strategy is **additive merge with target precedence**:

* variables present in source but not target → appended to target.
* variables present in both with different values → target value wins
  (target is the operator's running profile; clobbering it would
  silently break a working setup).
* variables present only in target → preserved.

When the target has no ``.env`` at all the entire source file is
copied as-is — there is nothing to merge against. The phase never
overwrites existing target values, matching the standalone tool's
behaviour and the operator's expectation that a sync never breaks a
working profile.
"""

from __future__ import annotations

from pathlib import Path

from talaria.sync.paths import SyncProfile
from talaria.sync.result import FilePhaseResult
from talaria.sync.writer import write_with_backup


def _parse_env(path: Path) -> tuple[list[str], dict[str, str]]:
    """Parse a ``.env`` file preserving comments and line order.

    Returns ``(lines, key_values)``. ``lines`` is the raw split
    content (used to preserve formatting when re-writing);
    ``key_values`` maps ``VAR`` → ``VALUE`` for non-comment,
    non-blank lines. Inline comments and quoting are not handled —
    sync only cares about adding new keys, so the raw line is what
    gets appended.
    """
    if not path.exists():
        return [], {}
    lines = path.read_text(encoding="utf-8").splitlines()
    kv: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, _, val = stripped.partition("=")
            kv[key.strip()] = val
    return lines, kv


def sync_env(
    source: SyncProfile,
    target: SyncProfile,
    *,
    apply: bool = True,
    no_backup: bool = False,
) -> FilePhaseResult:
    """Append new variables from source ``.env`` to target ``.env``.

    Returns a :class:`~talaria.sync.result.FilePhaseResult` with
    ``new_vars`` and ``preserved_vars`` populated for the renderer.
    """
    result = FilePhaseResult(
        phase="env",
        status="in_sync",
        target_path=target.env_file,
    )

    if not source.env_file.exists():
        result.status = "skipped"
        result.logs.append("  skip: source has no .env")
        return result

    _, source_kv = _parse_env(source.env_file)
    if not source_kv:
        result.status = "skipped"
        result.logs.append("  skip: source .env has no variables")
        return result

    if not target.env_file.exists():
        # No target .env — copy the entire source file. Preserve
        # ordering exactly as the source had it.
        result.status = "new"
        result.logs.append("  .env: new file (copying entire source)")
        if apply:
            source_content = source.env_file.read_text(encoding="utf-8")
            outcome = write_with_backup(target.env_file, source_content, no_backup=no_backup)
            result.write_confirmed = True
            if outcome.backup:
                result.logs.append(f"  backup: {outcome.backup}")
            result.logs.append(f"  written: {outcome.written}")
        else:
            result.logs.append("  (dry run)")
        return result

    _, target_kv = _parse_env(target.env_file)
    new_keys = [k for k in source_kv if k not in target_kv]
    preserved = [k for k in source_kv if k in target_kv and source_kv[k] != target_kv[k]]
    result.new_vars = new_keys
    result.preserved_vars = preserved

    if not new_keys:
        if not preserved:
            result.logs.append("  .env: already in sync (all source vars present)")
        else:
            result.logs.append(
                f"  .env: no new vars ({len(preserved)} differ — target values preserved)"
            )
        return result

    for key in new_keys:
        result.logs.append(f"  new: {key}={source_kv[key]}")
    for key in preserved:
        result.logs.append(f"  preserved: {key} (target value kept)")

    if not apply:
        result.logs.append("  (dry run)")
        return result

    # Append new vars to the existing target file. The header
    # comment marks the boundary so the operator can spot the merge.
    # Use a sibling temp + rename for atomic append (read existing,
    # concat new lines, write back) so concurrent readers never see
    # a half-written file.
    payload_lines = ["", "# ── Synced from source profile ──"]
    for key in new_keys:
        payload_lines.append(f"{key}={source_kv[key]}")
    append_payload = "\n".join(payload_lines) + "\n"

    existing_text = target.env_file.read_text(encoding="utf-8")
    new_text = existing_text + append_payload

    outcome = write_with_backup(target.env_file, new_text, no_backup=no_backup)
    result.write_confirmed = True
    if outcome.backup:
        result.logs.append(f"  backup: {outcome.backup}")
    result.logs.append(f"  written: {outcome.written} ({len(new_keys)} vars appended)")
    result.status = "updated"
    return result