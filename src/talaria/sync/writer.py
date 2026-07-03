"""Atomic write helpers with optional ``.bak`` backup.

Sync writes are destructive — a stale ``config.yaml`` can break a
profile. Two guarantees back every write:

* a ``.bak`` is created next to the target (unless the caller passes
  ``no_backup=True``). The previous contents are always recoverable.
* the new bytes go through a sibling temp file + ``os.replace``, so a
  concurrent reader never sees a half-written artefact.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WriteOutcome:
    """Result of a sync write.

    ``written`` is the final on-disk path; ``backup`` is the
    ``.bak`` path if one was created; ``bytes_written`` is the
    payload length (handy for ``render_human`` to summarise large
    files). Callers stash this in the phase result so the renderer
    can print ``written: <path>`` / ``backup: <path>``.
    """

    written: Path
    backup: Path | None
    bytes_written: int


def write_with_backup(
    target: Path,
    payload: str,
    *,
    no_backup: bool = False,
    backup_suffix: str = ".bak",
) -> WriteOutcome:
    """Atomically replace *target* with *payload*.

    Parameters
    ----------
    target:
        Destination file path. Created if it does not exist;
        overwritten if it does.
    payload:
        Full file contents to write. Caller is responsible for
        validation (e.g. YAML round-trip via :mod:`yaml_io`).
    no_backup:
        Skip the ``.bak`` step. The operator passes this through
        ``--no-backup`` for re-runnable cron scenarios.
    backup_suffix:
        Suffix appended to the backup file name. ``.bak`` is the
        default and matches the standalone tool's convention.

    Returns
    -------
    WriteOutcome
        ``written``, optional ``backup``, and ``bytes_written``.
    """
    target.parent.mkdir(parents=True, exist_ok=True)

    backup_path: Path | None = None
    if target.exists() and not no_backup:
        backup_path = target.with_name(target.name + backup_suffix)
        shutil.copy2(target, backup_path)

    # Atomic write: dump to a sibling temp file, fsync, then os.replace.
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=target.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    except Exception:
        # Clean up the temp file on failure so we don't leave debris.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    return WriteOutcome(
        written=target,
        backup=backup_path,
        bytes_written=len(payload),
    )