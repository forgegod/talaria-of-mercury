"""Sync ``SOUL.md`` between two profiles.

The agent personality file is a single text file. The phase is a
straight copy with a backup, matching the standalone tool's
behaviour:

* source missing → ``status="skipped"`` and a log line.
* target missing → ``status="new"`` and a fresh write.
* identical → ``status="in_sync"``.
* different → ``status="updated"`` and a backup + write.

Diff lines are captured for the verbose renderer; the default
renderer shows the action and lets the operator re-run with
``--verbose`` for the full unified diff.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from talaria.sync.paths import SyncProfile
from talaria.sync.result import PhaseResult
from talaria.sync.writer import write_with_backup


def sync_soul(
    source: SyncProfile,
    target: SyncProfile,
    *,
    apply: bool = True,
    no_backup: bool = False,
) -> PhaseResult:
    """Copy ``SOUL.md`` from *source* to *target*.

    Returns a :class:`~talaria.sync.result.PhaseResult` describing
    the action. ``apply=True`` writes; ``apply=False`` reports what
    would change without touching disk.
    """
    result = PhaseResult(
        phase="soul",
        status="in_sync",
        target_path=target.soul_md,
    )

    if not source.soul_md.exists():
        result.status = "skipped"
        result.logs.append(f"  skip: source has no SOUL.md ({source.soul_md})")
        return result

    source_content = source.soul_md.read_text(encoding="utf-8")
    if target.soul_md.exists():
        target_content = target.soul_md.read_text(encoding="utf-8")
        if source_content == target_content:
            result.logs.append("  SOUL.md: already in sync")
            return result

        diff_lines = list(difflib.unified_diff(
            target_content.splitlines(keepends=True),
            source_content.splitlines(keepends=True),
            fromfile="target SOUL.md",
            tofile="source SOUL.md",
            n=1,
        ))
        result.logs.append("  SOUL.md differs:")
        for line in "".join(diff_lines).splitlines():
            result.logs.append(f"    {line}")
        result.status = "updated"
    else:
        result.logs.append("  SOUL.md: new file (not in target)")
        result.status = "new"

    if not apply:
        result.logs.append("  (dry run)")
        return result

    outcome = write_with_backup(target.soul_md, source_content, no_backup=no_backup)
    result.write_confirmed = True
    if outcome.backup:
        result.logs.append(f"  backup: {outcome.backup}")
    result.logs.append(f"  written: {outcome.written}")
    return result