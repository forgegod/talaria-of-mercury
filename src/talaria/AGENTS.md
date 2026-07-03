# src/talaria

## Purpose

The Talaria Python package — `talaria` CLI entry point and library code.

## Ownership

- Package code lives in this subtree; tests live in `/tests`.
- The package's public surface is `talaria.cli:main` (the console script) plus
  the importable modules `talaria.paths`, `talaria.hermos.*`, and the version
  constant on `talaria.__version__`.
- Backwards-incompatible changes to the CLI surface require a major version bump.

## Local Contracts

- Public CLI surface: `talaria [--version] <command> [<subcommand>] [...flags]`.
- Exit codes: `0` clean, `1` signal fired, `2` tool error.
- All filesystem and SQLite access is read-only against the Hermes runtime;
  Talaria never modifies `state.db` or rotates logs.
- Every CLI subcommand must accept `--json` and a human-readable default renderer.

## Work Guidance

- New feature groups add a new subpackage under `talaria/<group>/` and a
  matching subparser in `talaria.cli.build_parser`. Do not add top-level
  commands without a feature group.
- Feature modules expose `run(paths, **opts) -> dict` and
  `render_human(report) -> tuple[int, str]`. JSON output is produced by
  `json.dumps(report, default=str)` at the CLI layer.
- Path resolution flows through `talaria.paths.resolve_paths` — never reach
  directly into `~/.hermes/` from feature code.
- Module-level constants for thresholds, regexes, and look-back windows go at
  the top of the feature module, not buried inside functions.

## Verification

- `pytest` from the repo root must pass before any change is considered done.
- New feature modules must add a sibling `tests/test_<feature>.py` using the
  `fake_hermes_root` fixture from `tests/conftest.py` plus
  `tests/_helpers.make_sessions_db`.

## Child DOX Index

- `cli/` — argparse parser, subcommand dispatch, console-script entry point.
- `hermos/` — Hermes-specific features (path resolution + per-feature checks).
- `paths.py` — profile + path resolution shared by every feature.