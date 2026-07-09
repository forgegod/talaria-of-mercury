"""Sync feature group — copy Hermes profile artefacts between profiles.

Talaria's read-only features inspect the running agent; the **sync**
group is the deliberate carve-out for the operator side of the
same workflow: copying configuration, personality, skills,
environment variables, and the model-context cache from a
source profile to a target profile.

Public entry points:

* :func:`talaria.sync.run.run_sync` — execute a sync between two
  resolved :class:`SyncProfile` objects.
* :func:`talaria.sync.paths.resolve_profile` — turn a profile name
  or ``config.yaml`` path into a :class:`SyncProfile`.
* :class:`talaria.sync.run.SyncOptions` — per-call options the CLI
  constructs from argparse flags.

The :class:`~talaria.sync.result.SyncReport` is the structured
output for both the human renderer and ``--json`` consumers.
"""

from __future__ import annotations

from talaria.sync.paths import (
    DEFAULT_MCP_SERVE_PORT,
    HERMES_ROOT,
    SyncProfile,
    list_profiles,
    mcp_serve_entry,
    resolve_profile,
)
from talaria.sync.result import (
    AuthTokensPhaseResult,
    ConfigPhaseResult,
    FilePhaseResult,
    PhaseResult,
    SkillsPhaseResult,
    SyncReport,
)
from talaria.sync.run import SyncOptions, run_mcp_serve, run_sync

__all__ = [
    "DEFAULT_MCP_SERVE_PORT",
    "HERMES_ROOT",
    "SyncProfile",
    "SyncOptions",
    "SyncReport",
    "PhaseResult",
    "ConfigPhaseResult",
    "FilePhaseResult",
    "AuthTokensPhaseResult",
    "SkillsPhaseResult",
    "list_profiles",
    "mcp_serve_entry",
    "resolve_profile",
    "run_mcp_serve",
    "run_sync",
]