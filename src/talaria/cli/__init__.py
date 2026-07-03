"""CLI subcommand package.

The top-level ``talaria`` entry point (:func:`main`) dispatches to a
*feature group* (currently just ``hermes``) and then to the named
feature (e.g. ``moa-truncation``). New feature groups add a new module
in this package without touching the dispatcher.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import talaria
from talaria.paths import resolve_paths

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


# ---------- Parser ----------
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

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())