"""Locate the Hermes Agent's per-profile state database and log directory.

Talaria commands always operate on a *resolved pair* of paths:

* a SQLite ``state.db`` (sessions, message counters, output_tokens)
* a ``logs/`` directory (agent.log, errors.log)

The Hermes runtime supports multiple **profiles** (``default`` lives at
``~/.hermes/``; named profiles at ``~/.hermes/profiles/<name>/``). The
active profile is normally set by ``hermes profile use``, but Talaria
must also honour CLI flags and environment overrides.

Resolution order (highest priority wins):

  1. CLI flags ``--state-db`` / ``--log-dir``           (explicit override)
  2. CLI flag ``--profile <name>``                      (resolved relative
     to ``$HERMES_ROOT/profiles/<name>/``)
  3. Environment ``HERMES_PROFILE=<name>``
  4. ``$HERMES_ROOT/active_profile`` file content       (created by
     ``hermes profile use``)
  5. Fallback: profile name ``"default"``

Functions in this module never raise on missing files — they return the
*resolved paths* so the caller can decide how to react. Path *existence*
checks are the caller's responsibility (state.db may legitimately be
absent in a clean install).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

HERMES_ROOT: Path = Path.home() / ".hermes"
"""Canonical Hermes install root. Always ``~/.hermes``.

Talaria does not honour ``HERMES_HOME`` for *resolution* — only for
referencing what an already-running Hermes session thinks is home.
Resolution is always relative to the root so that a script invoked
outside a running Hermes can still find its files.
"""

ACTIVE_PROFILE_FILE: Path = HERMES_ROOT / "active_profile"
"""File set by ``hermes profile use`` containing the active profile name."""

DEFAULT_PROFILE_NAME = "default"


@dataclass(frozen=True)
class ResolvedPaths:
    """The pair of paths a Talaria feature should operate on."""

    profile: str
    hermes_root: Path
    state_db: Path
    log_dir: Path

    def override(self, *, state_db: Path | None = None, log_dir: Path | None = None) -> "ResolvedPaths":
        """Return a copy with explicit path overrides applied."""
        return ResolvedPaths(
            profile=self.profile,
            hermes_root=self.hermes_root,
            state_db=state_db or self.state_db,
            log_dir=log_dir or self.log_dir,
        )


def resolve_profile_name(
    *,
    profile_flag: str | None = None,
    env_value: str | None = None,
    active_profile_file: Path | None = None,
) -> str:
    """Return the active profile name following the documented priority.

    Parameters mirror the resolution layers so tests can inject each one
    in isolation. Production code should call :func:`resolve_paths`.
    """
    if profile_flag:
        return profile_flag
    if env_value:
        return env_value
    path = active_profile_file or ACTIVE_PROFILE_FILE
    if path.exists():
        name = path.read_text().strip()
        if name:
            return name
    return DEFAULT_PROFILE_NAME


def profile_paths(
    profile_name: str,
    hermes_root: Path | None = None,
) -> tuple[Path, Path]:
    """Return ``(state_db, log_dir)`` for *profile_name*.

    The ``default`` profile stores its data directly under
    ``$HERMES_ROOT``. Named profiles use ``$HERMES_ROOT/profiles/<name>/``.
    """
    root = hermes_root or HERMES_ROOT
    if profile_name == DEFAULT_PROFILE_NAME:
        return (root / "state.db", root / "logs")
    return (root / "profiles" / profile_name / "state.db",
            root / "profiles" / profile_name / "logs")


def resolve_paths(
    *,
    profile_flag: str | None = None,
    state_db_flag: Path | None = None,
    log_dir_flag: Path | None = None,
    hermes_root: Path | None = None,
) -> ResolvedPaths:
    """Resolve the (profile, state_db, log_dir) tuple for the current call.

    Explicit path flags win over profile resolution; profile flag wins
    over environment; environment wins over the ``active_profile`` file;
    ``default`` is the final fallback.
    """
    root = hermes_root or HERMES_ROOT
    profile = resolve_profile_name(
        profile_flag=profile_flag,
        env_value=os.environ.get("HERMES_PROFILE"),
        active_profile_file=root / "active_profile",
    )
    resolved_state_db, resolved_log_dir = profile_paths(profile, root)
    return ResolvedPaths(
        profile=profile,
        hermes_root=root,
        state_db=state_db_flag or resolved_state_db,
        log_dir=log_dir_flag or resolved_log_dir,
    )