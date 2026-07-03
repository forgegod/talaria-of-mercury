"""CLI subcommand package.

The top-level ``talaria`` entry point (:func:`main`) dispatches to a
*feature group* (currently just ``hermes``) and then to the named
feature (e.g. ``moa-truncation``). New feature groups add a new module
in this package without touching the dispatcher.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import talaria
from talaria.hermos import refresh_catalog as refresh_catalog_module
from talaria.paths import resolve_paths
from talaria.sync import (
    SyncOptions,
    list_profiles as sync_list_profiles,
    resolve_profile as sync_resolve_profile,
)
from talaria.sync.render import print_error, render_human, render_json
from talaria.sync.run import run_sync

__all__ = ["main", "build_parser"]


# ---------- Renderer helpers ----------
def _print_json(payload: object) -> None:
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


# ---------- Subcommand: talaria paths ----------
def cmd_paths(args: argparse.Namespace) -> int:
    """Print the resolved (profile, state_db, log_dir) tuple."""
    paths = resolve_paths(
        profile_flag=args.profile,
        state_db_flag=args.state_db,
        log_dir_flag=args.log_dir,
    )
    if args.json:
        _print_json({
            "profile": paths.profile,
            "hermes_root": str(paths.hermes_root),
            "state_db": str(paths.state_db),
            "log_dir": str(paths.log_dir),
            "overrides": {
                "profile_flag": bool(args.profile),
                "state_db_flag": bool(args.state_db),
                "log_dir_flag": bool(args.log_dir),
                "HERMES_PROFILE_env": bool(__import__("os").environ.get("HERMES_PROFILE")),
                "active_profile_file": (paths.hermes_root / "active_profile").exists(),
            },
        })
        return 0
    print(f"profile:     {paths.profile}")
    print(f"hermes_root: {paths.hermes_root}")
    print(f"state_db:    {paths.state_db}")
    print(f"log_dir:     {paths.log_dir}")
    return 0


# ---------- Subcommand: talaria hermes moa-truncation ----------
def cmd_hermes_moa_truncation(args: argparse.Namespace) -> int:
    from talaria.hermos import moa_truncation
    paths = resolve_paths(
        profile_flag=args.profile,
        state_db_flag=args.state_db,
        log_dir_flag=args.log_dir,
    )
    if args.show_resolution:
        print(moa_truncation.show_resolution(paths))
        return 0
    report = moa_truncation.run(paths, days=args.days, since=args.since)
    if args.json:
        _print_json(report)
        return 1 if report["fired"] else 0
    exit_code, text = moa_truncation.render_human(report)
    print(text)
    return exit_code


# ---------- Subcommand: talaria hermes refresh-catalog ----------
def _default_catalog_dst(gateway: str = refresh_catalog_module.DEFAULT_GATEWAY) -> Path:
    """Resolve the default catalog cache path from XDG_CACHE_HOME.

    Computed at call time (not module import) so tests can monkeypatch
    the environment before invoking the parser.
    """
    return refresh_catalog_module.default_cache_path(gateway)


def cmd_hermes_refresh_catalog(args: argparse.Namespace) -> int:
    from talaria.hermos import refresh_catalog
    # Profile-agnostic by design, but resolve_paths() is cheap and keeps
    # the dispatch shape identical to the other hermes subcommands.
    paths = resolve_paths(profile_flag=args.profile)
    dst = Path(args.dst) if args.dst else _default_catalog_dst(args.gateway)
    src_url = args.src_url or refresh_catalog.gateway_config(args.gateway).source_url
    if args.show_resolution:
        print(refresh_catalog.show_resolution(paths, dst=dst, gateway=args.gateway, src_url=src_url))
        return 0
    report = refresh_catalog.run(
        paths,
        dst=dst,
        src_url=src_url,
        max_age_seconds=args.max_age_seconds,
        force=args.force,
        gateway=args.gateway,
    )
    if args.json:
        _print_json(report)
        return 0 if report["ok"] else 2
    exit_code, text = refresh_catalog.render_human(report)
    print(text)
    return exit_code

# ---------- Subcommand: talaria hermes fix-context-cache ----------
def cmd_hermes_fix_context_cache(args: argparse.Namespace) -> int:
    from talaria.hermos import context_cache_fix

    paths = resolve_paths(profile_flag=args.profile)
    cache_path = Path(args.cache_path) if args.cache_path else None
    if args.show_resolution:
        print(context_cache_fix.show_resolution(paths, cache_path=cache_path))
        return 0
    report = context_cache_fix.run(
        paths,
        cache_path=cache_path,
        apply=not args.dry_run,
        no_backup=args.no_backup,
        create_missing=not args.only_existing,
    )
    if args.json:
        _print_json(report)
        return 0 if report["ok"] else 2
    exit_code, text = context_cache_fix.render_human(report)
    print(text)
    return exit_code


# ---------- Subcommand: talaria sync ----------
def _build_sync_options(args: argparse.Namespace) -> SyncOptions:
    """Translate argparse ``Namespace`` into a :class:`SyncOptions`.

    Single point of truth so adding a CLI flag does not require
    touching :func:`run_sync`.
    """
    return SyncOptions(
        apply=not args.dry_run,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        excludes=list(args.exclude or []),
        only_paths=list(args.only or []),
        add_mcp_serve=args.add_mcp_serve,
        mcp_serve_name=args.mcp_serve_name,
        mcp_serve_port=args.mcp_serve_port,
        mcp_serve_host=args.mcp_serve_host,
        skill_filters=list(args.sync_skills or []),
        skip_config=args.skip_config,
        skip_soul=args.skip_soul,
        skip_skills=args.skip_skills,
        skip_env=args.skip_env,
        skip_cache=args.skip_cache,
    )


def cmd_sync(args: argparse.Namespace) -> int:
    """Entry point for ``talaria sync <source> <target>``.

    Resolves both profile specs, runs the configured phases via
    :func:`talaria.sync.run.run_sync`, and renders the report.
    Errors before orchestration (bad profile names, mutually
    exclusive flags) print to stderr and return 2.
    """
    # ``--list`` is special: no target needed, prints paths in
    # the source config and exits.
    if args.list:
        try:
            source = sync_resolve_profile(args.source)
        except FileNotFoundError as e:
            print_error(str(e))
            return 2
        paths = sync_list_config_paths(source, max_depth=args.list_depth)
        if not paths:
            print(f"  (no config.yaml at {source.config_yaml})")
            return 0
        print(f"Paths in {source.config_yaml}:\n")
        for path in paths:
            print(f"  {path}")
        return 0

    if not args.target:
        print_error("target profile is required (or use --list)")
        return 2

    try:
        source = sync_resolve_profile(args.source)
        target = sync_resolve_profile(args.target)
    except FileNotFoundError as e:
        print_error(str(e))
        return 2

    try:
        options = _build_sync_options(args)
        report = run_sync(source, target, options)
    except ValueError as e:
        print_error(str(e))
        return 2

    if args.json:
        sys.stdout.write(render_json(report))
        return 0 if report.ok else 2

    exit_code, text = render_human(report, verbose=args.verbose)
    sys.stdout.write(text)
    return exit_code


def sync_list_config_paths(profile, *, max_depth: int) -> list[str]:
    """Thin wrapper so ``cmd_sync`` does not import sync internals."""
    from talaria.sync.config import list_config_paths
    return list_config_paths(profile, max_depth=max_depth)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="talaria",
        description=(
            "Winged sandals for the Hermes Agent — maintenance utilities "
            "for NousResearch/hermes-agent."
        ),
    )
    p.add_argument("--version", action="version", version=f"talaria {talaria.__version__}")

    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # talaria paths
    p_paths = sub.add_parser(
        "paths", help="Print the resolved Hermes state.db and logs/ paths.",
        description=(
            "Resolve which Hermes profile Talaria would inspect, honouring "
            "CLI flags, $HERMES_PROFILE, and ~/.hermes/active_profile."
        ),
    )
    p_paths.add_argument("--profile", help="Override profile name.")
    p_paths.add_argument("--state-db", type=Path, help="Explicit state.db path override.")
    p_paths.add_argument("--log-dir", type=Path, help="Explicit logs/ directory override.")
    p_paths.add_argument("--json", action="store_true", help="Emit JSON.")
    p_paths.set_defaults(func=cmd_paths)

    # talaria hermes ...
    hermes = sub.add_parser(
        "hermes", help="Maintenance commands targeting Hermes Agent state.",
        description="Subcommands that inspect state.db and/or logs/.",
    )
    hermes_sub = hermes.add_subparsers(dest="hermes_command", required=True, metavar="COMMAND")

    # talaria hermes moa-truncation
    p_moa = hermes_sub.add_parser(
        "moa-truncation",
        help="Verify the MoA truncation mitigation (signal A + signal B).",
        description=(
            "Inspect state.db for high-output MoA sessions and scan "
            "agent.log/errors.log for length-class truncation markers."
        ),
    )
    p_moa.add_argument(
        "--days", type=int, default=2,
        help="Look-back window in days (default: 2).",
    )
    p_moa.add_argument(
        "--since", type=str, default=None,
        help="ISO date YYYY-MM-DD overriding --days.",
    )
    p_moa.add_argument(
        "--profile", help="Hermes profile name to inspect.",
    )
    p_moa.add_argument(
        "--state-db", type=Path,
        help="Explicit path to state.db (overrides --profile resolution).",
    )
    p_moa.add_argument(
        "--log-dir", type=Path,
        help="Explicit path to logs/ (overrides --profile resolution).",
    )
    p_moa.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable output.",
    )
    p_moa.add_argument(
        "--show-resolution", action="store_true",
        help="Print which profile and paths were resolved, then exit.",
    )
    p_moa.set_defaults(func=cmd_hermes_moa_truncation)

    # talaria hermes refresh-catalog
    p_catalog = hermes_sub.add_parser(
        "refresh-catalog",
        help="Refresh a gateway model catalog into the Hermes manifest cache.",
        description=(
            "Fetch the selected gateway catalog and reshape it into the "
            "Hermes manifest schema. Writes to the gateway-specific "
            "$XDG_CACHE_HOME cache file by default. Skips the fetch when "
            "the cache is younger than --max-age-seconds."
        ),
    )
    p_catalog.add_argument(
        "--gateway",
        choices=sorted(refresh_catalog_module.GATEWAYS),
        default=refresh_catalog_module.DEFAULT_GATEWAY,
        help="Gateway/provider catalog to refresh (currently only: kilocode).",
    )
    p_catalog.add_argument(
        "--dst", type=Path, default=None,
        help="Destination manifest path (default: gateway-specific file in $XDG_CACHE_HOME).",
    )
    p_catalog.add_argument(
        "--src-url", default=None,
        help="Catalog endpoint URL (advanced; defaults to the selected gateway).",
    )
    p_catalog.add_argument(
        "--max-age-seconds", type=int, default=refresh_catalog_module.MAX_AGE_SECONDS,
        help="Skip fetch when the cache is younger than this many seconds (default: 6h).",
    )
    p_catalog.add_argument(
        "--force", action="store_true",
        help="Refetch even when the cache is fresh.",
    )
    p_catalog.add_argument(
        "--profile", help="Recorded in the report for debugging; does not affect the cache path.",
    )
    p_catalog.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable output.",
    )
    p_catalog.add_argument(
        "--show-resolution", action="store_true",
        help="Print the resolved cache path and source URL, then exit.",
    )
    p_catalog.set_defaults(func=cmd_hermes_refresh_catalog)

    # talaria hermes fix-context-cache
    p_context = hermes_sub.add_parser(
        "fix-context-cache",
        help="Repair known-bad Hermes context_length_cache.yaml entries.",
        description=(
            "Update the selected profile's context_length_cache.yaml with "
            "Talaria's curated fixes for model context windows that Hermes "
            "has been known to cache incorrectly."
        ),
    )
    p_context.add_argument(
        "--profile", help="Hermes profile whose context cache should be repaired.",
    )
    p_context.add_argument(
        "--cache-path", type=Path, default=None,
        help="Explicit context_length_cache.yaml path (overrides --profile resolution).",
    )
    p_context.add_argument(
        "--only-existing", action="store_true",
        help="Only update existing bad entries; do not insert missing known-fix keys.",
    )
    p_context.add_argument(
        "--dry-run", action="store_true",
        help="Preview repairs without writing.",
    )
    p_context.add_argument(
        "--no-backup", action="store_true",
        help="Skip .bak backup before overwriting.",
    )
    p_context.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable output.",
    )
    p_context.add_argument(
        "--show-resolution", action="store_true",
        help="Print the resolved cache path and known-fix table, then exit.",
    )
    p_context.set_defaults(func=cmd_hermes_fix_context_cache)

    # talaria sync
    p_sync = sub.add_parser(
        "sync",
        help="Copy Hermes profile artefacts from source to target.",
        description=(
            "Sync config.yaml, SOUL.md, skills/, .env, and "
            "context_length_cache.yaml between two Hermes profiles. "
            "By default every phase runs and writes to the target; "
            "use --dry-run to preview, --skip-* to omit a phase, "
            "--exclude/--only to filter config.yaml paths, and "
            "--add-mcp-serve to inject an SSE endpoint entry."
        ),
    )
    p_sync.add_argument(
        "source",
        help="Source profile name (e.g. 'default') or path to a config.yaml.",
    )
    p_sync.add_argument(
        "target", nargs="?",
        help="Target profile name or path. Required unless --list is used.",
    )
    # Phase selection
    p_sync.add_argument("--skip-config", action="store_true",
                        help="Skip the config.yaml phase.")
    p_sync.add_argument("--skip-soul", action="store_true",
                        help="Skip the SOUL.md phase.")
    p_sync.add_argument("--skip-skills", action="store_true",
                        help="Skip the skills/ phase.")
    p_sync.add_argument("--skip-env", action="store_true",
                        help="Skip the .env phase.")
    p_sync.add_argument("--skip-cache", action="store_true",
                        help="Skip the context_length_cache.yaml phase.")
    # config.yaml filtering
    p_sync.add_argument("-e", "--exclude", nargs="+", default=[], metavar="PATH",
                        help="Dot-notation paths to exclude from source. "
                             "Target keeps its own values for excluded paths.")
    p_sync.add_argument("-o", "--only", nargs="+", default=[], metavar="PATH",
                        help="Copy ONLY these paths from source. "
                             "Mutually exclusive with --exclude.")
    # skills filter
    p_sync.add_argument("--sync-skills", nargs="*", default=None, metavar="FILTER",
                        help="Limit skills sync to specific categories or "
                             "category/skill-name paths. With no args, syncs "
                             "all skills. e.g. --sync-skills github or "
                             "--sync-skills github/dev-git-commit-message.")
    # mcp_serve injection
    p_sync.add_argument("--add-mcp-serve", action="store_true",
                        help="Add an mcp_servers entry to target connecting "
                             "to a running Hermes SSE endpoint.")
    p_sync.add_argument("--mcp-serve-name", default="hermes",
                        help="Name for the mcp_servers entry (default: hermes).")
    p_sync.add_argument("--mcp-serve-port", type=int, default=9119,
                        help="Port for the Hermes SSE endpoint (default: 9119).")
    p_sync.add_argument("--mcp-serve-host", default="localhost",
                        help="Host for the Hermes SSE endpoint (default: localhost).")
    # safety
    p_sync.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing.")
    p_sync.add_argument("--no-backup", action="store_true",
                        help="Skip .bak backup before overwriting.")
    # introspection
    p_sync.add_argument("--list", action="store_true",
                        help="List dot-notation paths in source config.yaml and exit.")
    p_sync.add_argument("--list-depth", type=int, default=2,
                        help="Depth for --list (default: 2).")
    # output
    p_sync.add_argument("--json", action="store_true",
                        help="Emit JSON report instead of human-readable output.")
    p_sync.add_argument("-v", "--verbose", action="store_true",
                        help="Show diffs, per-skill detail, and source/target banners.")
    p_sync.set_defaults(func=cmd_sync)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())