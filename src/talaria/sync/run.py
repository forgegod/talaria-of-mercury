"""Top-level orchestrator for the ``talaria sync`` command.

The orchestrator decides which phases to run based on the flags
passed by the CLI, calls each phase's :func:`sync_*` function, and
collects results into a :class:`~talaria.sync.result.SyncReport`.
The CLI layer only deals with argparse and exit codes — all sync
logic lives here so the same flow is callable from tests and
future entry points (cron scripts, MCP tools).

By default every phase runs. The operator can skip a phase with
``--skip-config``, ``--skip-soul``, ``--skip-skills``,
``--skip-env``, ``--skip-cache`` (and ``--skip-mcp-serve``). The
selection is per-call: there is no separate "all" subcommand —
``talaria sync <src> <tgt>`` with no skips is the all-phases case.
"""

from __future__ import annotations

from dataclasses import dataclass

from talaria.sync.config import sync_config
from talaria.sync.context_cache import sync_context_cache
from talaria.sync.env import sync_env
from talaria.sync.mcp_serve import sync_mcp_serve
from talaria.sync.paths import (
    DEFAULT_MCP_SERVE_PORT,
    SyncProfile,
    same_profile,
)
from talaria.sync.result import (
    ConfigPhaseResult,
    FilePhaseResult,
    PhaseResult,
    SkillsPhaseResult,
    SyncReport,
)
from talaria.sync.skills import sync_skills
from talaria.sync.soul import sync_soul


@dataclass
class SyncOptions:
    """Per-call options that the orchestrator forwards to each phase.

    The CLI constructs an instance of this from ``argparse`` flags
    and passes it to :func:`run_sync`. Fields default to the values
    the standalone tool used (``apply=True``, ``no_backup=False``,
    etc.) so a future programmatic caller can ignore most of them.
    """

    apply: bool = True
    no_backup: bool = False
    dry_run: bool = False
    excludes: list[str] | None = None
    only_paths: list[str] | None = None
    add_mcp_serve: bool = False
    mcp_serve_name: str = "hermes"
    mcp_serve_port: int = DEFAULT_MCP_SERVE_PORT
    mcp_serve_host: str = "localhost"
    skill_filters: list[str] | None = None
    skip_config: bool = False
    skip_soul: bool = False
    skip_skills: bool = False
    skip_env: bool = False
    skip_cache: bool = False


def run_sync(
    source: SyncProfile,
    target: SyncProfile,
    options: SyncOptions,
) -> SyncReport:
    """Execute the configured sync phases and return a :class:`SyncReport`.

    Parameters
    ----------
    source, target:
        Resolved profiles. The source/target ``name`` attributes are
        recorded into the report for display.
    options:
        Per-call options. ``apply=False`` makes every phase a dry
        run; the report's ``apply`` field reflects this so the
        renderer can show "(dry run)".

    Raises
    ------
    ValueError:
        Source and target resolve to the same directory; sync would
        silently no-op and is almost always a bug in the operator's
        invocation.
    """
    if same_profile(source, target):
        raise ValueError(
            f"source and target resolve to the same profile ({source.config_yaml})"
        )

    if options.excludes and options.only_paths:
        raise ValueError("--exclude and --only are mutually exclusive")

    apply = options.apply and not options.dry_run
    report = SyncReport(
        source=source.name,
        target=target.name,
        apply=apply,
    )

    if not options.skip_config:
        report.config = sync_config(
            source,
            target,
            excludes=options.excludes,
            only_paths=options.only_paths,
            add_mcp_serve=options.add_mcp_serve,
            mcp_serve_name=options.mcp_serve_name,
            mcp_serve_port=options.mcp_serve_port,
            mcp_serve_host=options.mcp_serve_host,
            apply=apply,
            no_backup=options.no_backup,
        )

    if not options.skip_soul:
        report.soul = sync_soul(
            source,
            target,
            apply=apply,
            no_backup=options.no_backup,
        )

    if not options.skip_skills:
        report.skills = sync_skills(
            source,
            target,
            filters=options.skill_filters,
            apply=apply,
            no_backup=options.no_backup,
        )

    if not options.skip_env:
        report.env = sync_env(
            source,
            target,
            apply=apply,
            no_backup=options.no_backup,
        )

    if not options.skip_cache:
        report.context_cache = sync_context_cache(
            source,
            target,
            apply=apply,
            no_backup=options.no_backup,
        )

    return report


def run_mcp_serve(
    target: SyncProfile,
    options: SyncOptions,
) -> ConfigPhaseResult:
    """Add or update an ``mcp_servers.<name>`` entry on *target*.

    Convenience entry point for the ``talaria sync --add-mcp-serve``
    flow when no source profile is needed (the phase reads the
    target's own config and writes back).
    """
    apply = options.apply and not options.dry_run
    return sync_mcp_serve(
        target,
        name=options.mcp_serve_name,
        port=options.mcp_serve_port,
        host=options.mcp_serve_host,
        apply=apply,
        no_backup=options.no_backup,
    )