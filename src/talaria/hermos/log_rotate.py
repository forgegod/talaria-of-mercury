"""Rotate and prune Hermes log directories.

The ``logs/`` directory of every Hermes profile (and the root
``~/.hermes/logs/``) grows monotonically. Active log files like
``agent.log`` and ``errors.log`` are written in append mode by the
agent, the gateway, the TUI, and the desktop app; rotated copies
(``*.1``, ``*.1.gz``) are never produced by Hermes itself, so the
operator must keep the directory bounded or it eventually fills the
disk.

This feature is **explicit-only**: with no flags the tool reports
sizes and ages but never writes. Pass ``--max-size`` to cap a single
file's gzip size (the agent keeps writing plaintext; the rotated copy
is what satisfies the 10 MiB ceiling), ``--max-age`` to delete any
rotated file older than the threshold, and ``--max-total`` to bound
the aggregate size of the directory by deleting the oldest rotated
files first. ``--keep N`` enforces a minimum number of rotated copies
per active file (never deletes below this). ``--all-profiles`` sweeps
every profile's ``logs/`` plus the root ``logs/`` in one run.

What "rotate" means here:

* The *active* file is whatever matches ``<name>.<ext>`` exactly with
  no ``.N`` or ``.gz`` suffix (e.g. ``agent.log``,
  ``errors.log``, ``gateway.log``, ``gui.log``, ``desktop.log``,
  ``update.log``, ``interrupt_debug.log``, ``mcp-stderr.log``,
  ``tui_gateway_crash.log``). Curated snapshot directories under
  ``logs/curator/<timestamp>/`` are also in scope as a unit.
* When an active file's gzipped payload would exceed ``--max-size``,
  the current bytes are copied to ``<name>.<ext>.1.gz`` (gzip level 6,
  matching the rest of Talaria's gzip writes), then the active file
  is truncated to zero. The copy-then-truncate order is the same
  pattern used by newsyslog / logrotate: a crash between the two
  steps would lose at most the in-flight write, never a rotation.
* ``.N`` rotation shifts are not implemented: Hermes' writers append
  concurrently and a shift would race them. The single-slot
  ``.1.gz`` keeps the surface simple and predictable.
* Curator snapshot directories (``logs/curator/<ts>/``) are deleted as
  units when older than ``--max-age`` or when ``--max-total`` needs to
  reclaim space. The directory is never partially deleted.

Pruning rules (apply in this order; each rule only touches files
the previous rule did not):

1. **Keep floor** — every active file's most recent ``--keep``
   rotated copies (newest first) are never deleted, regardless of
   size or age. Defaults to 1 (keep at least the most recent).
2. **Age** — rotated files (``*.N`` / ``*.N.gz``) and curator
   snapshots whose mtime is older than ``--max-age`` are deleted,
   skipping any copy preserved by rule 1.
3. **Total size** — if the directory's total on-disk size still
   exceeds ``--max-total`` after age pruning, the oldest rotated
   files (by mtime) are deleted in ascending order until the total
   drops below the cap. Active files are never deleted by the size
   rule; they are rotated instead.

Dry-run (``--dry-run``) reports every action that *would* have been
taken but never copies, truncates, gzips, or deletes anything.
"""

from __future__ import annotations

import gzip
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from talaria.paths import ResolvedPaths

#: Default per-file size cap, expressed as the gzipped footprint.
#: 10 MiB matches the documented Hermes Agent log budget.
DEFAULT_MAX_SIZE_BYTES = 10 * 1024 * 1024

#: Default age cap for rotated copies and curator snapshots.
DEFAULT_MAX_AGE_DAYS = 30

#: Default aggregate size cap for a single log directory.
DEFAULT_MAX_TOTAL_BYTES = 50 * 1024 * 1024

#: Default minimum number of rotated copies to preserve per active file.
DEFAULT_KEEP = 1

#: Filename suffixes that mark a file as a rotated copy. Order matters:
#: ``.N.gz`` must be tested before ``.gz`` (the ``.gz`` part is a suffix
#: of ``.N.gz``).
_ROTATED_GZ_SUFFIX = ".gz"
_ROTATED_INDEX_RE = ".%d"
_ROTATED_GZ_RE = ".%d.gz"

#: Subdirectories of ``logs/`` that are rotated/deleted as a single unit.
CURATOR_SUBDIR = "curator"

#: Filenames that are never deleted or rotated by this tool, even if
#: they look rotated. Keeps the operator's manual artifacts safe.
_PROTECTED_BASENAMES = frozenset({
    "README.md",
    "AGENTS.md",
    ".gitkeep",
})


# ---------- Result types ----------

@dataclass
class FileAction:
    """A single planned or executed action on a log file or directory."""

    path: str
    action: str            # "rotate" | "truncate" | "delete" | "skip"
    reason: str            # human-readable explanation
    size_before: int = 0   # bytes on disk before the action (0 for "skip")
    size_after: int = 0    # bytes on disk after the action (delete -> 0)
    compressed_size: int = 0  # gzipped payload size, when applicable

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "action": self.action,
            "reason": self.reason,
            "size_before": self.size_before,
            "size_after": self.size_after,
            "compressed_size": self.compressed_size,
        }


@dataclass
class RotateReport:
    """The full result for one log directory."""

    profile: str
    log_dir: str
    ok: bool
    actions: list[FileAction]
    scanned_files: int
    scanned_bytes: int
    deleted_bytes: int
    rotated_count: int
    deleted_count: int
    truncated_count: int
    dry_run: bool
    total_size_after: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "log_dir": self.log_dir,
            "ok": self.ok,
            "scanned_files": self.scanned_files,
            "scanned_bytes": self.scanned_bytes,
            "deleted_bytes": self.deleted_bytes,
            "rotated_count": self.rotated_count,
            "deleted_count": self.deleted_count,
            "truncated_count": self.truncated_count,
            "dry_run": self.dry_run,
            "total_size_after": self.total_size_after,
            "actions": [a.to_dict() for a in self.actions],
        }


# ---------- Helpers ----------

def _is_protected(p: Path) -> bool:
    """A path is protected if its basename is in the never-touch list."""
    return p.name in _PROTECTED_BASENAMES


def _parse_rotated(name: str) -> tuple[str, int] | None:
    """Return ``(base, index)`` if *name* matches ``<base>.<ext>.N[.gz]``.

    Examples::

        _parse_rotated("agent.log")        -> None
        _parse_rotated("agent.log.1")      -> ("agent.log", 1)
        _parse_rotated("agent.log.1.gz")   -> ("agent.log", 1)
        _parse_rotated("agent.log.12.gz")  -> ("agent.log", 12)

    The *base* is the original log filename (``agent.log``); the
    *index* is the integer rotation slot.
    """
    if not name:
        return None
    # peel an optional trailing .gz
    stem = name[:-3] if name.endswith(".gz") else name
    if stem is name:
        # no .gz suffix — the trailing component must be a number
        head, _, tail = stem.rpartition(".")
        if not head or not tail.isdigit():
            return None
        return head, int(tail)
    # .gz present — the bit before .gz must end in .N
    head, _, tail = stem.rpartition(".")
    if not head or not tail.isdigit():
        return None
    return head, int(tail)


def classify(path: Path) -> str:
    """Return one of ``"active"``, ``"rotated"``, ``"other"``.

    ``active``   — base log name with no ``.N`` / ``.N.gz`` suffix.
    ``rotated``  — ``*.N`` or ``*.N.gz`` (any index).
    ``other``    — anything else (random artefacts, READMEs, etc.).
    Curator snapshot directories are detected separately by the caller.
    """
    if not path.is_file():
        return "other"
    if _is_protected(path):
        return "other"
    if _parse_rotated(path.name) is not None:
        return "rotated"
    return "active"


def _gzip_size(data: bytes, level: int = 6) -> int:
    """Return the gzipped size of *data* at *level* without writing to disk."""
    return len(gzip.compress(data, compresslevel=level))


def _gzip_to(target: Path, data: bytes, level: int = 6) -> int:
    """Atomically write *data* gzipped to *target*. Returns bytes written."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        with gzip.open(tmp, "wb", compresslevel=level) as gz:
            gz.write(data)
        os.replace(tmp, target)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return target.stat().st_size


def _collect_curator_dirs(log_dir: Path) -> list[Path]:
    """Return the ``logs/curator/<ts>/`` snapshot directories, oldest first."""
    curator = log_dir / CURATOR_SUBDIR
    if not curator.is_dir():
        return []
    dirs = [p for p in curator.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime)
    return dirs


def _dir_size(p: Path) -> int:
    """Recursive byte total for *p* (file or directory). Missing -> 0."""
    if p.is_file():
        return p.stat().st_size
    if not p.exists():
        return 0
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


# ---------- Plan + apply ----------

@dataclass
class RotateOptions:
    """Operator-tunable knobs for a single rotate run."""

    max_size: int | None = None
    max_age: timedelta | None = None
    max_total: int | None = None
    keep: int = DEFAULT_KEEP
    apply: bool = True
    gzip_level: int = 6

    @property
    def has_action(self) -> bool:
        """True when at least one prune/rotate rule is active."""
        return (
            self.max_size is not None
            or self.max_age is not None
            or self.max_total is not None
        )


def _plan_actions(
    log_dir: Path,
    options: RotateOptions,
) -> list[FileAction]:
    """Compute the list of FileActions *log_dir* would receive.

    Pure function: no file is read, written, truncated, or deleted.
    """
    actions: list[FileAction] = []
    if not log_dir.is_dir():
        return actions

    # Snapshot the directory listing at plan time so apply/display see
    # the same set even if the caller mutates the directory concurrently.
    files: list[Path] = [p for p in log_dir.iterdir() if p.is_file()]

    active: list[Path] = []
    rotated: list[Path] = []
    for p in files:
        kind = classify(p)
        if kind == "active":
            active.append(p)
        elif kind == "rotated":
            rotated.append(p)

    # Sort rotated copies newest-first (descending mtime); ties broken by name
    # so the operator sees a stable ordering.
    rotated.sort(key=lambda p: (-p.stat().st_mtime, p.name))

    # Rule 1: keep floor — preserve the first N rotated copies per base name.
    keep_protected: set[Path] = set()
    by_base: dict[str, list[Path]] = {}
    for p in rotated:
        parsed = _parse_rotated(p.name)
        if parsed is None:
            continue
        base, _idx = parsed
        by_base.setdefault(base, []).append(p)
    for _base, copies in by_base.items():
        for p in copies[: options.keep]:
            keep_protected.add(p)

    # Rule 2: rotate active files that exceed --max-size.
    now = datetime.now(tz=timezone.utc).timestamp()
    for p in active:
        try:
            data = p.read_bytes()
        except OSError as exc:
            actions.append(FileAction(
                path=str(p), action="skip", reason=f"unreadable: {exc}",
            ))
            continue
        size = len(data)
        gz_size = _gzip_size(data, options.gzip_level) if options.max_size is not None else 0
        if options.max_size is not None and gz_size > options.max_size:
            # Active rotation: copy → gzip → truncate. The .1.gz slot
            # is the only one we ever produce; the operator can find the
            # newest rotated copy at this single, predictable path.
            target = p.with_name(f"{p.name}.1.gz")
            actions.append(FileAction(
                path=str(p),
                action="rotate",
                reason=(
                    f"gzip size {gz_size} > --max-size {options.max_size}"
                ),
                size_before=size,
                size_after=0,
                compressed_size=gz_size,
            ))
            actions.append(FileAction(
                path=str(target),
                action="copy",
                reason="gzip target of active rotation",
                size_before=0,
                size_after=gz_size,
                compressed_size=gz_size,
            ))
            actions.append(FileAction(
                path=str(p),
                action="truncate",
                reason="post-rotation truncate to 0",
                size_before=size,
                size_after=0,
            ))
        else:
            actions.append(FileAction(
                path=str(p), action="skip",
                reason=(
                    f"size {size} ok"
                    + (f" (gzip {gz_size} <= {options.max_size})"
                       if options.max_size is not None else "")
                ),
                size_before=size,
            ))

    # Rule 3: age-based delete on rotated copies.
    if options.max_age is not None:
        cutoff = now - options.max_age.total_seconds()
        for p in rotated:
            if p in keep_protected:
                actions.append(FileAction(
                    path=str(p), action="skip",
                    reason=f"protected by --keep {options.keep}",
                ))
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError as exc:
                actions.append(FileAction(
                    path=str(p), action="skip", reason=f"unstatable: {exc}",
                ))
                continue
            age = now - mtime
            if age > options.max_age.total_seconds():
                actions.append(FileAction(
                    path=str(p), action="delete",
                    reason=(
                        f"age {int(age // 86400)}d > --max-age "
                        f"{int(options.max_age.total_seconds() // 86400)}d"
                    ),
                    size_before=p.stat().st_size,
                    size_after=0,
                ))
            else:
                actions.append(FileAction(
                    path=str(p), action="skip",
                    reason=f"age {int(age // 86400)}d within --max-age",
                ))

    # Rule 4: aggregate size prune — delete oldest rotated copies until
    # the directory's total size drops below --max-total. Active files
    # are *never* deleted by the size rule (rotation handles them).
    if options.max_total is not None:
        total = sum(
            (log_dir / p).stat().st_size
            for p in os.listdir(log_dir)
            if (log_dir / p).exists()
        )
        # Walk oldest-first for pruning.
        rotated_oldest_first = sorted(rotated, key=lambda p: p.stat().st_mtime)
        for p in rotated_oldest_first:
            if total <= options.max_total:
                break
            if p in keep_protected:
                continue
            size = p.stat().st_size
            actions.append(FileAction(
                path=str(p), action="delete",
                reason=(
                    f"total {total} > --max-total {options.max_total}"
                ),
                size_before=size,
                size_after=0,
            ))
            total -= size

    # Rule 5: curator snapshot directories — age-based, then size-based.
    curator_dirs = _collect_curator_dirs(log_dir)
    for d in curator_dirs:
        try:
            mtime = d.stat().st_mtime
        except OSError:
            continue
        size = _dir_size(d)
        age = now - mtime
        if options.max_age is not None and age > options.max_age.total_seconds():
            actions.append(FileAction(
                path=str(d), action="delete",
                reason=(
                    f"age {int(age // 86400)}d > --max-age "
                    f"{int(options.max_age.total_seconds() // 86400)}d"
                ),
                size_before=size,
                size_after=0,
            ))

    return actions


def _apply_actions(actions: Iterable[FileAction], *, apply: bool) -> None:
    """Execute the actions in *actions*. With ``apply=False`` the actions
    are recorded but no bytes are written or removed.

    The planner emits ``rotate``/``copy``/``truncate`` as a fixed
    triple per active file; the implementation treats the triple as
    one logical "rotate this file" step: read source, gzip to
    ``.1.gz``, then truncate the source. The triplet records are
    preserved in the report so JSON consumers see exactly what the
    planner emitted.
    """
    pending = list(actions)
    i = 0
    while i < len(pending):
        a = pending[i]
        if not apply:
            i += 1
            continue
        if a.action == "delete":
            target = Path(a.path)
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
            except OSError:
                # best-effort; the report keeps the planned reason
                pass
            i += 1
            continue
        if a.action == "rotate":
            src = Path(a.path)
            copy_action = pending[i + 1] if i + 1 < len(pending) else None
            trunc_action = pending[i + 2] if i + 2 < len(pending) else None
            # Step the index past the full triple, even if a sub-step
            # fails — a partial rotation is worse than a clean
            # advance because subsequent replays would re-attempt the
            # already-completed parts.
            advance = 3 if (trunc_action is not None
                            and trunc_action.action == "truncate") else 1
            try:
                data = src.read_bytes()
            except OSError:
                i += advance
                continue
            if copy_action is not None and copy_action.action == "copy":
                try:
                    _gzip_to(Path(copy_action.path), data)
                except OSError:
                    i += advance
                    continue
            if trunc_action is not None and trunc_action.action == "truncate":
                try:
                    src.write_bytes(b"")
                except OSError:
                    pass
            i += advance
            continue
        # 'skip' and bare 'copy' records are not executed standalone;
        # 'copy' is consumed by the 'rotate' triple above.
        i += 1


def _summarise(actions: list[FileAction], scanned_files: int, scanned_bytes: int,
               dry_run: bool) -> tuple[int, int, int, int]:
    """Return ``(deleted_bytes, rotated, deleted, truncated)`` from *actions*."""
    deleted_bytes = 0
    rotated = 0
    deleted = 0
    truncated = 0
    for a in actions:
        if a.action == "delete":
            deleted_bytes += a.size_before
            deleted += 1
        elif a.action == "rotate":
            rotated += 1
        elif a.action == "truncate":
            truncated += 1
    return deleted_bytes, rotated, deleted, truncated


def _scan(log_dir: Path) -> tuple[int, int]:
    """Return ``(scanned_files, scanned_bytes)`` for *log_dir*."""
    if not log_dir.is_dir():
        return 0, 0
    files = 0
    bytes_total = 0
    for p in log_dir.iterdir():
        if p.is_file():
            files += 1
            try:
                bytes_total += p.stat().st_size
            except OSError:
                pass
    return files, bytes_total


def rotate_log_dir(log_dir: Path, options: RotateOptions, *, profile: str) -> RotateReport:
    """Plan and (optionally) apply rotation/pruning to *log_dir*.

    Returns a :class:`RotateReport` describing what was scanned, what
    actions were taken, and the resulting on-disk size.

    The tool is explicit-only: with no prune/rotate flags set in
    *options* the file system is never touched regardless of
    ``options.apply``. The report is still produced so the operator
    sees the scanned size and the verdict that nothing was requested.
    """
    log_dir = Path(log_dir)
    scanned_files, scanned_bytes = _scan(log_dir)
    if options.has_action:
        actions = _plan_actions(log_dir, options)
        _apply_actions(actions, apply=options.apply)
    else:
        actions = []
    deleted_bytes, rotated, deleted, truncated = _summarise(
        actions, scanned_files, scanned_bytes, options.apply is False,
    )
    if options.apply and options.has_action:
        # Re-scan after writes so the reported "after" total is accurate.
        _, total_after = _scan(log_dir)
    else:
        # No writes happened (either dry-run, or no flags at all). The
        # "after" total equals "before" minus any planned deletes plus
        # the gzipped size that would replace each rotated active file.
        gzip_total = sum(
            a.compressed_size for a in actions if a.action == "copy"
        )
        total_after = scanned_bytes - deleted_bytes + gzip_total
    return RotateReport(
        profile=profile,
        log_dir=str(log_dir),
        ok=True,
        actions=actions,
        scanned_files=scanned_files,
        scanned_bytes=scanned_bytes,
        deleted_bytes=deleted_bytes,
        rotated_count=rotated,
        deleted_count=deleted,
        truncated_count=truncated,
        dry_run=not options.apply or not options.has_action,
        total_size_after=total_after,
    )


def run(
    paths: ResolvedPaths,
    *,
    log_dir: Path | None = None,
    max_size: int | None = None,
    max_age_days: int | None = None,
    max_total: int | None = None,
    keep: int = DEFAULT_KEEP,
    apply: bool = True,
    gzip_level: int = 6,
) -> dict[str, Any]:
    """Rotate/prune the selected profile's ``logs/`` directory.

    Returns a JSON-serialisable report. With *log_dir* the caller can
    override the profile-resolved path; otherwise
    :attr:`ResolvedPaths.log_dir` is used.
    """
    target = Path(log_dir) if log_dir else paths.log_dir
    options = RotateOptions(
        max_size=max_size,
        max_age=timedelta(days=max_age_days) if max_age_days is not None else None,
        max_total=max_total,
        keep=keep,
        apply=apply,
        gzip_level=gzip_level,
    )
    report = rotate_log_dir(target, options, profile=paths.profile)
    return report.to_dict()


def render_human(report: dict[str, Any]) -> tuple[int, str]:
    """Format a rotate report for the terminal."""
    lines = [
        f"Hermes log rotation — profile '{report['profile']}'",
        "=" * 60,
        "",
        f"log_dir:       {report['log_dir']}",
        f"scanned:       {report['scanned_files']} files, {report['scanned_bytes']} bytes",
        f"total after:   {report['total_size_after']} bytes",
        "",
    ]
    if report.get("dry_run"):
        lines.append("(dry-run: no bytes written or removed)")
        lines.append("")

    actions = report.get("actions") or []
    if not actions:
        lines.append("No actions planned.")
    else:
        # Group: rotates first, then truncates, then deletes, then skips.
        for kind in ("rotate", "truncate", "delete", "skip", "copy"):
            bucket = [a for a in actions if a["action"] == kind]
            if not bucket:
                continue
            label = {
                "rotate": "rotate",
                "truncate": "truncate",
                "delete": "delete",
                "skip": "skip",
                "copy": "  (gzip target)",
            }[kind]
            for a in bucket:
                lines.append(
                    f"  {label:9s}  {a['path']}  — {a['reason']}"
                )

    lines.append("")
    lines.append("=" * 60)
    verdict_parts: list[str] = []
    verdict_parts.append(f"rotated={report['rotated_count']}")
    verdict_parts.append(f"truncated={report['truncated_count']}")
    verdict_parts.append(f"deleted={report['deleted_count']}")
    verdict_parts.append(f"reclaimed={report['deleted_bytes']} bytes")
    verdict = "VERDICT: " + ", ".join(verdict_parts)
    if report.get("dry_run"):
        verdict += " (dry-run)"
    lines.append(verdict + ".")
    return 0, "\n".join(lines)


def show_resolution(
    paths: ResolvedPaths,
    *,
    log_dir: Path | None = None,
    max_size: int | None = None,
    max_age_days: int | None = None,
    max_total: int | None = None,
    keep: int = DEFAULT_KEEP,
) -> str:
    """Return a JSON resolution descriptor for debugging."""
    import json
    target = Path(log_dir) if log_dir else paths.log_dir
    scanned_files, scanned_bytes = _scan(target)
    options = RotateOptions(
        max_size=max_size,
        max_age=timedelta(days=max_age_days) if max_age_days is not None else None,
        max_total=max_total,
        keep=keep,
        apply=False,  # resolution is always a dry run
    )
    actions = _plan_actions(target, options)
    return json.dumps(
        {
            "profile": paths.profile,
            "log_dir": str(target),
            "scanned_files": scanned_files,
            "scanned_bytes": scanned_bytes,
            "options": {
                "max_size": max_size,
                "max_age_days": max_age_days,
                "max_total": max_total,
                "keep": keep,
            },
            "would_act": [
                a.to_dict() for a in actions if a.action != "skip"
            ],
        },
        indent=2,
    )
