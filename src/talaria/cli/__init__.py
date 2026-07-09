"""CLI subcommand package.

The top-level ``talaria`` entry point (:func:`main`) dispatches to a
*feature group* (currently just ``hermes``) and then to the named
feature (e.g. ``doctor``). New feature groups add a new module
in this package without touching the dispatcher.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import talaria
from talaria.hermos import doctor as doctor_module
from talaria.hermos import refresh_catalog as refresh_catalog_module
from talaria.hermos import benchmark as benchmark_module
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


# ---------- Subcommand: talaria hermes doctor ----------
def _parse_csv(value: str) -> tuple[str, ...]:
    """Parse a comma-separated string into a tuple of trimmed, non-empty ids."""
    return tuple(p.strip() for p in value.split(",") if p.strip())


def cmd_hermes_doctor(args: argparse.Namespace) -> int:
    from talaria.hermos import doctor
    paths = resolve_paths(
        profile_flag=args.profile,
        state_db_flag=args.state_db,
        log_dir_flag=args.log_dir,
    )
    if args.show_resolution:
        print(doctor.show_resolution(paths))
        return 0
    only = _parse_csv(args.only) if args.only else ()
    skip = _parse_csv(args.skip) if args.skip else ()
    try:
        report = doctor.run(
            paths,
            days=args.days,
            since=args.since,
            include_curator=args.include_curator,
            only=only,
            skip=skip,
            free_flight=not args.no_free_flight,
            apply_curator_suggestions=args.apply_curator_suggestions,
            apply_dry_run=args.dry_run,
        )
    except ValueError as exc:
        print(f"talaria hermes doctor: {exc}", file=sys.stderr)
        return 2
    # Tactical actions run after detection so the report's per_detector
    # verdict still reflects the scan state (post-tactical-action re-scan
    # is not the contract). The three preview/apply flags are
    # independent of each other and of --apply-curator-suggestions.
    tactical_flags = (
        args.prune_stale_locks
        or args.close_zombies
        or args.prune_ghost_sessions
    )
    if tactical_flags:
        report["tactical_actions"] = doctor.apply_tactical_actions(
            paths,
            prune_stale_locks=args.prune_stale_locks,
            close_zombies=args.close_zombies,
            prune_ghost_sessions=args.prune_ghost_sessions,
            apply=args.apply,
            days=args.days,
            since=args.since,
        )
    if args.json:
        _print_json(report)
        return 1 if report["fired"] else 0
    if not args.quiet:
        exit_code, text = doctor.render_human(report)
        print(text)
        return exit_code
    return 1 if report["fired"] else 0


# ---------- Subcommand: talaria hermes benchmark ----------
def cmd_hermes_benchmark(args: argparse.Namespace) -> int:
    from talaria.hermos import benchmark
    paths = resolve_paths(
        profile_flag=args.profile,
        state_db_flag=args.state_db,
    )
    if args.show_resolution:
        print(benchmark.show_resolution(
            paths,
            days=args.days,
            ttl=args.ttl,
            jobs=args.jobs,
            config_path=Path(args.config) if args.config else None,
            vision_fixtures_dir=Path(args.vision_fixtures_dir) if args.vision_fixtures_dir else None,
        ))
        return 0
    report = benchmark.run(
        paths,
        days=args.days,
        ttl=args.ttl,
        smoke=not args.no_smoke,
        vision=not args.no_vision,
        jobs=args.jobs,
        config_path=Path(args.config) if args.config else None,
        cache_path=Path(args.cache) if args.cache else None,
        vision_fixtures_dir=Path(args.vision_fixtures_dir) if args.vision_fixtures_dir else None,
    )
    if args.json:
        _print_json(report)
        # Smoke or vision fail → exit 1; otherwise 0.
        any_fail = any(
            (m.get("smoke") or {}).get("ok") is False
            or any(
                (vf.get("ok") is False and not vf.get("skipped"))
                for vf in (m.get("vision") or [])
            )
            for m in report.get("per_model", [])
        )
        return 1 if any_fail else 0
    if not args.quiet:
        exit_code, text = benchmark.render_human(report)
        print(text)
        return exit_code
    # --quiet: exit-code only. Smoke or vision fail → 1.
    any_fail = any(
        (m.get("smoke") or {}).get("ok") is False
        or any(
            (vf.get("ok") is False and not vf.get("skipped"))
            for vf in (m.get("vision") or [])
        )
        for m in report.get("per_model", [])
    )
    return 1 if any_fail else 0


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
    if args.verbose:
        exit_code, text = refresh_catalog.render_human(report)
        print(text)
        return exit_code
    return 0 if report["ok"] else 2

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
    if args.verbose:
        exit_code, text = context_cache_fix.render_human(report)
        print(text)
        return exit_code
    return 0 if report["ok"] else 2


# ---------- Subcommand: talaria hermes skills install ----------
def cmd_hermes_skills_install(args: argparse.Namespace) -> int:
    from talaria.hermos import skill_install

    paths = resolve_paths(profile_flag=args.profile)
    if args.show_resolution:
        print(skill_install.show_resolution(paths, identifier=args.identifier, category=args.category))
        return 0
    report = skill_install.run(
        paths,
        identifier=args.identifier,
        force=args.force,
        force_enable=args.force_enable,
        enable=list(args.enable or []),
        category=args.category,
        replace_similar=args.replace_similar_skill,
        apply=not args.dry_run,
        no_backup=args.no_backup,
        verbose=args.verbose,
    )
    if args.json:
        _print_json(report)
        return 0 if report["ok"] else 2
    if args.verbose:
        exit_code, text = skill_install.render_human(report)
        print(text)
        return exit_code
    return 0 if report["ok"] else 2


# ---------- Subcommand: talaria skills uninstall ----------
def cmd_hermes_skills_uninstall(args: argparse.Namespace) -> int:
    from talaria.hermos import skill_uninstall

    paths = resolve_paths(profile_flag=args.profile)
    if args.show_resolution:
        print(skill_uninstall.show_resolution(paths, identifier=args.identifier))
        return 0
    report = skill_uninstall.run(
        paths,
        identifier=args.identifier,
        apply=not args.dry_run,
        no_backup=args.no_backup,
        verbose=args.verbose,
    )
    if args.json:
        _print_json(report)
        return 0 if report["ok"] else 2
    if args.verbose:
        exit_code, text = skill_uninstall.render_human(report)
        print(text)
        return exit_code
    return 0 if report["ok"] else 2


# ---------- Subcommand: talaria skills create-category ----------
def cmd_hermes_skills_create_category(args: argparse.Namespace) -> int:
    from talaria.hermos import skill_category

    paths = resolve_paths(profile_flag=args.profile)
    if args.show_resolution:
        print(skill_category.show_resolution(
            paths, category=args.category, description=args.description or "",
        ))
        return 0
    report = skill_category.create_category(
        paths,
        args.category,
        description=args.description or "",
        apply=not args.dry_run,
        no_backup=args.no_backup,
    )
    if args.json:
        _print_json(report)
        return 0 if report["ok"] else 2
    if args.verbose:
        exit_code, text = skill_category.render_human(report)
        print(text)
        return exit_code
    return 0 if report["ok"] else 2


# ---------- Subcommand: talaria skills prune ----------
def cmd_hermes_skills_prune(args: argparse.Namespace) -> int:
    from talaria.hermos import skill_prune

    paths = resolve_paths(profile_flag=args.profile)
    report = skill_prune.run(
        paths,
        prune_filesystem_only=args.prune_filesystem_only,
        prune_lock_only=args.prune_lock_only,
        prune_disabled_orphans=args.prune_disabled_orphans,
        apply=args.apply,
        no_backup=args.no_backup,
    )
    if args.json:
        _print_json(skill_prune.report_to_dict(report))
        return 0
    exit_code, text = skill_prune.render_human(report)
    print(text)
    return exit_code


# ---------- Subcommand: talaria config sync ----------
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
        force_config=args.force_config,
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

    if args.verbose:
        exit_code, text = render_human(report, verbose=True)
        sys.stdout.write(text)
        return exit_code
    return 0 if report.ok else 2


def sync_list_config_paths(profile, *, max_depth: int) -> list[str]:
    """Thin wrapper so ``cmd_sync`` does not import sync internals."""
    from talaria.sync.config import list_config_paths
    return list_config_paths(profile, max_depth=max_depth)


# ---------- Subcommand: talaria hermes serve-stop ----------
def cmd_hermes_serve_stop(args: argparse.Namespace) -> int:
    from talaria.hermos import serve_stop

    paths = resolve_paths(profile_flag=args.profile)
    if args.show_resolution:
        print(serve_stop.show_resolution(paths, port=args.port))
        return 0
    report = serve_stop.run(
        paths,
        port=args.port,
        apply=not args.dry_run,
    )
    if args.json:
        _print_json(report)
        return 0 if report["ok"] else 2
    if args.verbose:
        exit_code, text = serve_stop.render_human(report)
        print(text)
        return exit_code
    return 0 if report["ok"] else 2


# ---------- Subcommand: talaria hermes log-rotate ----------
def _log_rotate_target_paths(args: argparse.Namespace) -> list[tuple[str, Path]]:
    """Return ``[(profile, log_dir)]`` pairs to process.

    With ``--all-profiles`` every ``$HERMES_ROOT/profiles/*/logs/``
    plus the root ``$HERMES_ROOT/logs/`` is in scope; otherwise just
    the active profile's logs/ (the same path that ``talaria paths``
    reports).
    """
    paths = resolve_paths(profile_flag=args.profile)
    if not args.all_profiles:
        return [(paths.profile, paths.log_dir)]
    from talaria.paths import DEFAULT_PROFILE_NAME, HERMES_ROOT

    targets: list[tuple[str, Path]] = []
    root = paths.hermes_root
    root_logs = root / "logs"
    if root_logs.is_dir():
        targets.append((DEFAULT_PROFILE_NAME, root_logs))
    profiles_dir = root / "profiles"
    if profiles_dir.is_dir():
        for child in sorted(profiles_dir.iterdir()):
            if not child.is_dir():
                continue
            logs = child / "logs"
            if logs.is_dir():
                targets.append((child.name, logs))
    return targets


def cmd_hermes_log_rotate(args: argparse.Namespace) -> int:
    from talaria.hermos import log_rotate as log_rotate_module

    targets = _log_rotate_target_paths(args)
    if args.show_resolution:
        from talaria.paths import ResolvedPaths

        all_actions: list[dict] = []
        for profile, log_dir in targets:
            paths = ResolvedPaths(
                profile=profile,
                hermes_root=log_dir.parent.parent,
                state_db=log_dir.parent / "state.db",
                log_dir=log_dir,
            )
            print(log_rotate_module.show_resolution(
                paths,
                log_dir=log_dir,
                max_size=args.max_size,
                max_age_days=args.max_age_days,
                max_total=args.max_total,
                keep=args.keep,
            ))
            all_actions.append({"profile": profile, "log_dir": str(log_dir)})
        return 0

    reports: list[dict] = []
    for profile, log_dir in targets:
        from talaria.paths import ResolvedPaths

        paths = ResolvedPaths(
            profile=profile,
            hermes_root=log_dir.parent.parent,
            state_db=log_dir.parent / "state.db",
            log_dir=log_dir,
        )
        reports.append(log_rotate_module.run(
            paths,
            log_dir=log_dir,
            max_size=args.max_size,
            max_age_days=args.max_age_days,
            max_total=args.max_total,
            keep=args.keep,
            apply=not args.dry_run,
        ))

    if args.json:
        _print_json({"reports": reports})
        return 0

    for r in reports:
        exit_code, text = log_rotate_module.render_human(r)
        print(text)
    return 0


def cmd_config_apply_auxiliary(args: argparse.Namespace) -> int:
    from talaria.hermos import auxiliary

    paths = resolve_paths(profile_flag=args.profile)
    config_path = Path(args.config_path) if args.config_path else None
    if args.show_resolution:
        print(auxiliary.show_resolution(paths, config_path=config_path))
        return 0
    report = auxiliary.run(
        paths,
        config_path=config_path,
        apply=not args.dry_run,
        no_backup=args.no_backup,
    )
    if args.json:
        _print_json(report)
        return 0 if report["ok"] else 2
    if args.verbose:
        exit_code, text = auxiliary.render_human(report)
        print(text)
        return exit_code
    return 0 if report["ok"] else 2


def cmd_config_sync_env(args: argparse.Namespace) -> int:
    from talaria.hermos import sync_env

    paths = resolve_paths(profile_flag=args.profile)
    env_file = Path(args.env_path) if args.env_path else None
    add_keys = args.add_key or None
    skip_keys = args.skip_key or None
    disable_keys = args.disable_key or None
    enable_keys = args.enable_key or None
    if args.show_resolution:
        print(sync_env.show_resolution(
            paths, env_file=env_file, add_keys=add_keys, skip_keys=skip_keys,
            disable_keys=disable_keys, enable_keys=enable_keys,
        ))
        return 0
    report = sync_env.run(
        paths,
        env_file=env_file,
        apply=not args.dry_run,
        no_backup=args.no_backup,
        add_keys=add_keys,
        skip_keys=skip_keys,
        disable_keys=disable_keys,
        enable_keys=enable_keys,
    )
    if args.json:
        _print_json(report)
        return 0 if report["ok"] else 2
    if args.verbose:
        exit_code, text = sync_env.render_human(report)
        print(text)
        return exit_code
    return 0 if report["ok"] else 2


# ---------- Subcommand: talaria completion ----------
def cmd_completion(args: argparse.Namespace) -> int:
    """Emit a shell completion script (bash or zsh).

    Usage::

        eval "$(talaria completion zsh)"
        eval "$(talaria completion bash)"
    """
    from talaria.cli import completion

    try:
        script = completion.render(build_parser(), args.shell)
    except completion.CompletionError as e:
        print_error(str(e))
        return 2
    sys.stdout.write(script)
    return 0


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

    # talaria completion
    from talaria.cli import completion as _completion_module
    p_completion = sub.add_parser(
        "completion",
        help="Print a shell completion script (bash or zsh).",
        description=(
            "Emit a self-contained shell completion script for the talaria "
            "CLI. Source it with `eval \"$(talaria completion bash)\" (bash) "
            "or `eval \"$(talaria completion zsh)\"` (zsh)."
        ),
    )
    p_completion.add_argument(
        "shell",
        choices=list(_completion_module.SHELLS),
        help="Target shell: bash or zsh.",
    )
    p_completion.set_defaults(func=cmd_completion)

    # talaria hermes ...
    hermes = sub.add_parser(
        "hermes", help="Maintenance commands targeting Hermes Agent state.",
        description=(
            "Subcommands that inspect the profile's state.db, logs/, and "
            "skill registry, plus narrowly scoped maintenance tasks."
        ),
    )
    hermes_sub = hermes.add_subparsers(dest="hermes_command", required=True, metavar="COMMAND")

    # talaria hermes doctor
    p_diag = hermes_sub.add_parser(
        "doctor",
        help=(
            "Inspect the profile for anomalies (state.db + logs + skills registry) "
            "and optionally apply curator-driven config fixes."
        ),
        description=(
            "Run every selected detector against the profile's state.db, "
            "logs/, and skill registry "
            "(<skills_root>/.hub/lock.json plus skills.disabled in "
            "config.yaml). The free-flight curator pass (the operator's "
            "configured _curator model analysing the assembled evidence) "
            "runs by default — it is the only way the structured 12-detector "
            "pass catches unknown-unknown anomalies and config improvements. "
            "Pass --no-free-flight for pure deterministic results. "
            "Findings come in two kinds: anomaly (reported in the verdict; "
            "no tactical action is available) and config_suggestion "
            "(curator-model recommendations; reported but never applied "
            "unless --apply-curator-suggestions is passed)."
        ),
    )
    p_diag.add_argument(
        "--days", type=int, default=doctor_module.DEFAULT_LOOKBACK_DAYS,
        help="Look-back window in days (default: 2).",
    )
    p_diag.add_argument(
        "--since", type=str, default=None,
        help="ISO date YYYY-MM-DD overriding --days.",
    )
    p_diag.add_argument(
        "--include-curator", action="store_true",
        help="Also walk logs/curator/<ts>/ snapshot trees.",
    )
    p_diag.add_argument(
        "--only", type=str, default="",
        help=(
            "Comma-separated list of detector ids to run exclusively "
            f"(e.g. {doctor_module.TRUNCATION_OUTPUT},{doctor_module.ZOMBIE_SESSIONS})."
        ),
    )
    p_diag.add_argument(
        "--skip", type=str, default="",
        help="Comma-separated list of detector ids to skip.",
    )
    p_diag.add_argument(
        "--no-free-flight", action="store_true",
        help=(
            "Skip the free-flight curator pass; the report uses only "
            "the deterministic 12-detector verdicts. Off by default; "
            "the operator passes this for pure-deterministic runs or "
            "to keep the command hermes-free (no model call)."
        ),
    )
    p_diag.add_argument(
        "--apply-curator-suggestions", action="store_true",
        help=(
            "Apply config_suggestion findings from the free-flight "
            "curator pass to the active profile's config.yaml. Atomic "
            "backup (config.yaml.bak) is written first; the change is "
            "reported in the apply.* fields of the JSON report. Off by "
            "default; the operator reviews suggestions on a dry-run first. "
            "Note: only findings of kind config_suggestion are applied; "
            "anomaly findings are diagnostic and have no tactical action."
        ),
    )
    p_diag.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Preview the apply path without writing bytes. Implies "
            "--apply-curator-suggestions (so the diff is computed) but "
            "the write step is suppressed. The proposed diff is reported "
            "in apply.dry_run_diff."
        ),
    )
    p_diag.add_argument(
        "--prune-stale-locks", action="store_true",
        help=(
            "Stale-lock tactical action: drop every row in "
            "compression_locks whose expires_at is in the past. "
            "Dry-run preview by default — pass --apply to actually "
            "write to state.db via SQLite WAL."
        ),
    )
    p_diag.add_argument(
        "--close-zombies", action="store_true",
        help=(
            "Zombie-closure tactical action: set ended_at on every "
            "session whose writer crashed without closing. Session "
            "rows are preserved (only ended_at is written). "
            "Dry-run preview by default — pass --apply to actually write."
        ),
    )
    p_diag.add_argument(
        "--prune-ghost-sessions", action="store_true",
        help=(
            "Ghost-session tactical action: delete every session "
            "within the look-back window that has zero rows in "
            "messages. Out-of-window ghosts are diagnostic-only and "
            "never auto-deleted. Dry-run preview by default — pass "
            "--apply to actually write."
        ),
    )
    p_diag.add_argument(
        "--apply", action="store_true",
        help=(
            "Gate the tactical write paths. When set, the three "
            "--prune-* / --close-* flags become writes instead of "
            "dry-run previews. A no-op when none of those flags is "
            "set. Independent from --apply-curator-suggestions, which "
            "writes curator config_suggestion findings."
        ),
    )
    p_diag.add_argument(
        "--profile", help="Hermes profile name to inspect.",
    )
    p_diag.add_argument(
        "--state-db", type=Path,
        help="Explicit path to state.db (overrides --profile resolution).",
    )
    p_diag.add_argument(
        "--log-dir", type=Path,
        help="Explicit path to logs/ (overrides --profile resolution).",
    )
    p_diag.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable output.",
    )
    p_diag.add_argument(
        "--show-resolution", action="store_true",
        help="Print which profile and paths were resolved, then exit.",
    )
    p_diag.add_argument(
        "-q", "--quiet", action="store_true",
        help="Suppress the human-readable report; exit code only (default: report printed).",
    )
    p_diag.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print the human-readable report (default behaviour; kept for convenience).",
    )
    p_diag.set_defaults(func=cmd_hermes_doctor, quiet=False)

    # talaria hermes benchmark
    p_bench = hermes_sub.add_parser(
        "benchmark",
        help="Report per-model health, cost, latency, and capabilities.",
        description=(
            "Aggregate per-model stats from state.db (call counts, token "
            "usage, cost, first-response latency, reasoning level) and "
            "enrich with capability data from models.dev. When the smoke "
            "cache is stale (default TTL 30 min), makes one fresh JSON "
            "smoke call per discovered model. The report is read-only."
        ),
    )
    p_bench.add_argument(
        "--days", type=int, default=benchmark_module.DEFAULT_LOOKBACK_DAYS,
        help=f"Look-back window in days for state.db aggregation (default: {benchmark_module.DEFAULT_LOOKBACK_DAYS}).",
    )
    p_bench.add_argument(
        "--ttl", type=int, default=benchmark_module.DEFAULT_TTL_SECONDS,
        help=(
            f"Cache TTL in seconds for smoke results (default: "
            f"{benchmark_module.DEFAULT_TTL_SECONDS // 60} min). Smoke "
            "calls within the TTL window reuse the cached result."
        ),
    )
    p_bench.add_argument(
        "--no-smoke", action="store_true",
        help="Skip all JSON smoke calls; report only state.db data.",
    )
    p_bench.add_argument(
        "--no-vision", action="store_true",
        help=(
            "Skip all vision-capability checks. By default, every "
            "discovered model whose capabilities include vision (per "
            "models.dev) is tested against the vision fixture images."
        ),
    )
    p_bench.add_argument(
        "--jobs", "-j", type=int, default=benchmark_module.DEFAULT_JOBS,
        help=(
            f"Max parallel subprocess calls for smoke and vision checks "
            f"(default: {benchmark_module.DEFAULT_JOBS}). Each call is an "
            f"I/O-bound model API wait, so parallelism gives near-linear "
            f"speedup on the cold path. Use --jobs 1 for the old sequential "
            f"behaviour."
        ),
    )
    p_bench.add_argument(
        "--vision-fixtures-dir", type=Path, default=None,
        help=(
            "Override the vision fixture-image directory (default: "
            "assets/benchmark/vision/ relative to the repository root)."
        ),
    )
    p_bench.add_argument(
        "--profile", help="Hermes profile name to inspect.",
    )
    p_bench.add_argument(
        "--state-db", type=Path,
        help="Explicit path to state.db (overrides --profile resolution).",
    )
    p_bench.add_argument(
        "--config", type=Path,
        help="Explicit path to config.yaml (overrides --profile resolution).",
    )
    p_bench.add_argument(
        "--cache", type=Path,
        help="Explicit path to the benchmark cache file.",
    )
    p_bench.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable output.",
    )
    p_bench.add_argument(
        "--show-resolution", action="store_true",
        help="Print resolved paths + discovered models and exit.",
    )
    p_bench.add_argument(
        "-q", "--quiet", action="store_true",
        help="Suppress the human-readable report; exit code only.",
    )
    p_bench.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print the human-readable report (default behaviour; kept for convenience).",
    )
    p_bench.set_defaults(func=cmd_hermes_benchmark, quiet=False)

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
    p_catalog.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print the human-readable report on stdout (default: silent, exit code only).",
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
    p_context.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print the human-readable report on stdout (default: silent, exit code only).",
    )
    p_context.set_defaults(func=cmd_hermes_fix_context_cache)

    # talaria hermes serve-stop
    p_serve_stop = hermes_sub.add_parser(
        "serve-stop",
        help="Stop a running Hermes dashboard/serve backend by its port.",
        description=(
            "Detect and gracefully stop the Hermes dashboard/serve backend "
            "by the TCP port it is listening on (default 9119). Useful when "
            "the desktop app launched the backend in a way that "
            "hermes serve --stop cannot pattern-match (e.g. the desktop "
            "app's `-p default dashboard` launch)."
        ),
    )
    p_serve_stop.add_argument(
        "--port", type=int, default=9119,
        help="Port the Hermes backend is listening on (default: 9119).",
    )
    p_serve_stop.add_argument(
        "--profile", help="Recorded in the report for debugging; does not affect detection.",
    )
    p_serve_stop.add_argument(
        "--dry-run", action="store_true",
        help="Detect and report the backend PID(s) without sending any signal.",
    )
    p_serve_stop.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable output.",
    )
    p_serve_stop.add_argument(
        "--show-resolution", action="store_true",
        help="Print the port and detected PID(s), then exit.",
    )
    p_serve_stop.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print the human-readable report on stdout (default: silent, exit code only).",
    )
    p_serve_stop.set_defaults(func=cmd_hermes_serve_stop)

    # talaria hermes log-rotate
    p_log_rotate = hermes_sub.add_parser(
        "log-rotate",
        help="Rotate and prune Hermes log directories (age + size caps, gzip, dry-run).",
        description=(
            "Rotate and prune the active profile's logs/ directory. "
            "Explicit-only: with no flags the tool reports sizes/ages and "
            "exits without writing. --max-size rotates any active file "
            "whose gzipped payload exceeds the cap (copy -> <name>.1.gz, "
            "truncate source to 0). --max-age deletes rotated copies and "
            "curator snapshots older than the threshold. --max-total bounds "
            "the aggregate size of the directory by deleting the oldest "
            "rotated copies first. --keep N protects the most recent N "
            "rotated copies per base name. --all-profiles sweeps every "
            "$HERMES_ROOT/profiles/*/logs/ plus the root logs/ in one run."
        ),
    )
    p_log_rotate.add_argument(
        "--profile", help="Hermes profile whose logs/ to operate on (default: active).",
    )
    p_log_rotate.add_argument(
        "--all-profiles", action="store_true",
        help="Sweep every profile's logs/ and the root logs/ in one run.",
    )
    p_log_rotate.add_argument(
        "--max-size", type=int, default=None, metavar="BYTES",
        help="Per-file size cap, applied to the gzipped payload. Active files "
             "exceeding this are rotated (copy -> <name>.1.gz, truncate to 0).",
    )
    p_log_rotate.add_argument(
        "--max-age", type=int, default=None, dest="max_age_days", metavar="DAYS",
        help="Delete rotated copies and curator snapshots older than DAYS days.",
    )
    p_log_rotate.add_argument(
        "--max-total", type=int, default=None, metavar="BYTES",
        help="Cap the aggregate on-disk size of the directory; oldest "
             "rotated copies are deleted first until the total drops below.",
    )
    p_log_rotate.add_argument(
        "--keep", type=int, default=1, metavar="N",
        help="Minimum number of rotated copies to preserve per base name (default: 1).",
    )
    p_log_rotate.add_argument(
        "--dry-run", action="store_true",
        help="Preview actions without copying, gzipping, truncating, or deleting.",
    )
    p_log_rotate.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable output.",
    )
    p_log_rotate.add_argument(
        "--show-resolution", action="store_true",
        help="Print resolved log dir, scanned size, and planned actions, then exit.",
    )
    p_log_rotate.set_defaults(func=cmd_hermes_log_rotate)

    # talaria skills ...
    p_skills = sub.add_parser(
        "skills",
        help="Install or uninstall skills (recursive expansion implicit).",
        description=(
            "Skill maintenance. install and uninstall expand a skill "
            "identifier (recursive when it ends in /*), delegate each "
            "install/uninstall to the matching hermes skills subcommand, "
            "then update the profile's skills.disabled policy. A "
            "non-wildcard identifier installs or uninstalls a single skill."
        ),
    )
    skills_sub = p_skills.add_subparsers(
        dest="skills_command", required=True, metavar="COMMAND",
    )

    # talaria skills install
    p_skill_install = skills_sub.add_parser(
        "install",
        help="Install skill(s) under an identifier (recursive if it ends in /*).",
        description=(
            "Expand a skill identifier (recursive when it ends in /*), run "
            "hermes skills install for each child skill, and update "
            "skills.disabled so third-party recursive installs are disabled "
            "by default."
        ),
    )
    p_skill_install.add_argument(
        "identifier",
        help="Skill identifier; a trailing /* installs every child skill (e.g. skills-sh/addyosmani/agent-skills/*).",
    )
    p_skill_install.add_argument(
        "--profile", help="Hermes profile to install into and whose config.yaml to update.",
    )
    p_skill_install.add_argument(
        "--force", action="store_true",
        help="Pass --force to each hermes skills install invocation.",
    )
    p_skill_install.add_argument(
        "--force-enable", dest="force_enable", action="store_true",
        help="Enable every installed skill instead of disabling recursive installs by default.",
    )
    p_skill_install.add_argument(
        "--enable", nargs="*", default=[], metavar="SKILL",
        help="Enable only these installed skill names or identifiers; all other installed skills are disabled.",
    )
    p_skill_install.add_argument(
        "--category", default="",
        help="Category directory name forwarded to `hermes skills install --category`. "
             "Installs each skill into skills/<category>/<name>/ instead of the flat root. "
             "Must match Hermes' category regex: lowercase letters, digits, hyphens, "
             "underscores, and slashes (e.g. software-development, mlops/training). "
             "No display-name mapping — the value is the literal directory name.",
    )
    p_skill_install.add_argument(
        "--replace-similar-skill", dest="replace_similar_skill", action="store_true",
        help="When a skill name already exists and the frontmatter (name + description) "
             "is >=65%% similar (difflib.SequenceMatcher), uninstall the existing skill "
             "before installing the new one. Without this flag, similar skills are "
             "reported as hints only.",
    )
    p_skill_install.add_argument(
        "--dry-run", action="store_true",
        help="Preview expansion and config policy without installing or writing config.yaml.",
    )
    p_skill_install.add_argument(
        "--no-backup", action="store_true",
        help="Skip .bak backup before updating config.yaml.",
    )
    p_skill_install.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable output.",
    )
    p_skill_install.add_argument(
        "--show-resolution", action="store_true",
        help="Print expanded skill identifiers and target config path, then exit.",
    )
    p_skill_install.add_argument(
        "-v", "--verbose", action="store_true",
        help="Stream per-skill progress to stderr AND print the human-readable report "
             "on stdout (default: silent, exit code only).",
    )
    p_skill_install.set_defaults(func=cmd_hermes_skills_install)

    # talaria skills uninstall
    p_skill_uninstall = skills_sub.add_parser(
        "uninstall",
        help="Uninstall skill(s) under an identifier (recursive if it ends in /*).",
        description=(
            "Expand a skill identifier (recursive when it ends in /*), run "
            "hermes skills uninstall for each child skill name, and remove "
            "the uninstalled skills from skills.disabled so the disabled "
            "list does not reference skills that are no longer present."
        ),
    )
    p_skill_uninstall.add_argument(
        "identifier",
        help="Skill identifier; a trailing /* uninstalls every child skill (e.g. skills-sh/addyosmani/agent-skills/*).",
    )
    p_skill_uninstall.add_argument(
        "--profile", help="Hermes profile to uninstall from and whose config.yaml is updated.",
    )
    p_skill_uninstall.add_argument(
        "--dry-run", action="store_true",
        help="Preview expansion and config policy without uninstalling or writing config.yaml.",
    )
    p_skill_uninstall.add_argument(
        "--no-backup", action="store_true",
        help="Skip .bak backup before updating config.yaml.",
    )
    p_skill_uninstall.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable output.",
    )
    p_skill_uninstall.add_argument(
        "--show-resolution", action="store_true",
        help="Print expanded skill identifiers and target config path, then exit.",
    )
    p_skill_uninstall.add_argument(
        "-v", "--verbose", action="store_true",
        help="Stream per-skill progress to stderr AND print the human-readable report "
             "on stdout (default: silent, exit code only).",
    )
    p_skill_uninstall.set_defaults(func=cmd_hermes_skills_uninstall)

    # talaria skills create-category
    p_skill_create_cat = skills_sub.add_parser(
        "create-category",
        help="Create a skill category directory with an optional description.",
        description=(
            "Create a category directory under the profile's skills/ tree so "
            "skills can be installed into it with `talaria skills install "
            "--category <name>`. Optionally writes a DESCRIPTION.md whose "
            "frontmatter description is shown in the Hermes system prompt. "
            "The category name is the literal directory name (e.g. "
            "software-development, mlops/training) — lowercase letters, "
            "digits, hyphens, underscores, and slashes."
        ),
    )
    p_skill_create_cat.add_argument(
        "category",
        help="Category directory name (e.g. software-development, mlops/training).",
    )
    p_skill_create_cat.add_argument(
        "--description", default="",
        help="Human-readable description written to DESCRIPTION.md frontmatter. "
             "Shown after the category name in the Hermes system prompt.",
    )
    p_skill_create_cat.add_argument(
        "--profile", help="Hermes profile whose skills/ tree to create the category in.",
    )
    p_skill_create_cat.add_argument(
        "--dry-run", action="store_true",
        help="Preview the resolved paths without creating anything.",
    )
    p_skill_create_cat.add_argument(
        "--no-backup", action="store_true",
        help="Skip .bak backup when overwriting an existing DESCRIPTION.md.",
    )
    p_skill_create_cat.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable output.",
    )
    p_skill_create_cat.add_argument(
        "--show-resolution", action="store_true",
        help="Print the resolved category directory and validation result, then exit.",
    )
    p_skill_create_cat.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print the human-readable report on stdout (default: silent, exit code only).",
    )
    p_skill_create_cat.set_defaults(func=cmd_hermes_skills_create_category)

    # talaria skills prune
    p_skill_prune = skills_sub.add_parser(
        "prune",
        help="Reconcile on-disk skills, lock.json, and skills.disabled for a profile.",
        description=(
            "Detect and (with --apply) resolve drift between three sources "
            "of skill state in the active profile: the on-disk "
            "<skills_root>/**/SKILL.md walk, the <skills_root>/.hub/lock.json "
            "registry, and skills.disabled in the profile's config.yaml. "
            "Pair with `talaria hermes doctor` (skill_index_drift detector) "
            "for the read-only view. --dry-run is the default — pass --apply "
            "to execute. None of --prune-filesystem-only, --prune-lock-only, "
            "or --prune-disabled-orphans set defaults to ON, so the bare "
            "command is a no-op."
        ),
    )
    p_skill_prune.add_argument(
        "--prune-filesystem-only", action="store_true",
        help="Delete on-disk skill directories that are missing from lock.json "
             "(filesystem install that bypassed `hermes skills install`).",
    )
    p_skill_prune.add_argument(
        "--prune-lock-only", action="store_true",
        help="Drop lock.json entries whose on-disk directory is gone "
             "(orphan lock entry from a manual rm -rf).",
    )
    p_skill_prune.add_argument(
        "--prune-disabled-orphans", action="store_true",
        help="Remove skills.disabled names that are not on disk and not in lock "
             "(stale policy entry; harmless but a clean policy should not reference nothing).",
    )
    p_skill_prune.add_argument(
        "--profile", help="Hermes profile to prune (default: active profile).",
    )
    p_skill_prune.add_argument(
        "--apply", action="store_true",
        help="Execute the planned prune (default: dry-run preview).",
    )
    p_skill_prune.add_argument(
        "--no-backup", action="store_true",
        help="Skip .bak backup before overwriting lock.json or config.yaml.",
    )
    p_skill_prune.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable output.",
    )
    p_skill_prune.set_defaults(func=cmd_hermes_skills_prune)

    # talaria config ...
    config_grp = sub.add_parser(
        "config", help="Configuration maintenance: sync profiles, derive aliases, refresh .env values.",
        description=(
            "Configuration maintenance commands. sync copies profile "
            "artefacts between two profiles; apply-auxiliary derives "
            "model.aliases from a single profile's auxiliary block; "
            "sync-env refreshes a single profile's .env values from the "
            "current process environment, and can optionally extend the "
            "file's variable scope with --add-key (no new variables "
            "added by default)."
        ),
    )
    config_sub = config_grp.add_subparsers(dest="config_command", required=True, metavar="COMMAND")

    # talaria config sync
    p_sync = config_sub.add_parser(
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
    p_sync.add_argument("--force-config", action="store_true",
                        help="Overwrite target config.yaml even when the source config.yaml is not newer.")
    # introspection
    p_sync.add_argument("--list", action="store_true",
                        help="List dot-notation paths in source config.yaml and exit.")
    p_sync.add_argument("--list-depth", type=int, default=2,
                        help="Depth for --list (default: 2).")
    # output
    p_sync.add_argument("--json", action="store_true",
                        help="Emit JSON report instead of human-readable output.")
    p_sync.add_argument("-v", "--verbose", action="store_true",
                        help="Print the human-readable report on stdout with diffs, "
                             "per-skill detail, and source/target banners "
                             "(default: silent, exit code only).")
    p_sync.set_defaults(func=cmd_sync)

    # talaria config apply-auxiliary
    p_auxiliary = config_sub.add_parser(
        "apply-auxiliary",
        help="Derive model.aliases from a profile's auxiliary block.",
        description=(
            "Read the selected profile's auxiliary.<usecase>.model pins and "
            "surface them as model.aliases._<usecase> entries in the same "
            "profile's config.yaml. Usecases set to a sentinel (auto, "
            "inherit, default, ...) are skipped; existing operator-defined "
            "aliases are preserved."
        ),
    )
    p_auxiliary.add_argument(
        "--profile", help="Hermes profile whose config.yaml should be updated.",
    )
    p_auxiliary.add_argument(
        "--config-path", type=Path, default=None,
        help="Explicit config.yaml path (overrides --profile resolution).",
    )
    p_auxiliary.add_argument(
        "--dry-run", action="store_true",
        help="Preview the derived aliases without writing.",
    )
    p_auxiliary.add_argument(
        "--no-backup", action="store_true",
        help="Skip .bak backup before overwriting config.yaml.",
    )
    p_auxiliary.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable output.",
    )
    p_auxiliary.add_argument(
        "--show-resolution", action="store_true",
        help="Print the resolved config path and the aliases that would be derived, then exit.",
    )
    p_auxiliary.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print the human-readable report on stdout (default: silent, exit code only).",
    )
    p_auxiliary.set_defaults(func=cmd_config_apply_auxiliary)

    # talaria config sync-env
    p_sync_env = config_sub.add_parser(
        "sync-env",
        help="Refresh a profile's .env values from the current environment.",
        description=(
            "Update the selected profile's .env by overwriting the value "
            "of every variable that already exists in the file with its "
            "current value from the process environment. Variables absent "
            "from the .env are never added — the file keeps the exact "
            "variable set the operator defined, only values are refreshed."
        ),
    )
    p_sync_env.add_argument(
        "--profile", help="Hermes profile whose .env should be refreshed.",
    )
    p_sync_env.add_argument(
        "--env-path", type=Path, default=None,
        help="Explicit .env path (overrides --profile resolution).",
    )
    p_sync_env.add_argument(
        "--add-key", action="append", default=None, metavar="KEY", dest="add_key",
        help="Append KEY to the .env (with its value from the current "
             "environment) if it is absent. Repeatable. Keys already in "
             "the file are left to the normal refresh path. Pass to extend "
             "the profile's variable scope; without it, only existing "
             "values are refreshed.",
    )
    p_sync_env.add_argument(
        "--skip-key", action="append", default=None, metavar="KEY", dest="skip_key",
        help="Keep KEY out of the env-value refresh on this run: its file "
             "value is preserved as-is even when the environment has a "
             "different value. Repeatable.",
    )
    p_sync_env.add_argument(
        "--disable-key", action="append", default=None, metavar="KEY", dest="disable_key",
        help="Comment out KEY (KEY=value becomes #KEY=value). Repeatable. "
             "Disabled keys are hidden from the refresh scan and keep "
             "their value while inactive. Reversible with --enable-key.",
    )
    p_sync_env.add_argument(
        "--enable-key", action="append", default=None, metavar="KEY", dest="enable_key",
        help="Uncomment a previously disabled KEY (#KEY=value becomes "
             "KEY=value). Repeatable. The restored value is kept verbatim; "
             "KEY is not refreshed from the environment on the same run.",
    )
    p_sync_env.add_argument(
        "--dry-run", action="store_true",
        help="Preview which variables would change without writing.",
    )
    p_sync_env.add_argument(
        "--no-backup", action="store_true",
        help="Skip .bak backup before overwriting .env.",
    )
    p_sync_env.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable output.",
    )
    p_sync_env.add_argument(
        "--show-resolution", action="store_true",
        help="Print the resolved .env path and which keys would be updated, then exit.",
    )
    p_sync_env.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print the human-readable report on stdout (default: silent, exit code only).",
    )
    p_sync_env.set_defaults(func=cmd_config_sync_env)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())