# src/talaria

## Purpose

The Talaria Python package — `talaria` CLI entry point and library code.

## Ownership

- Package code lives in this subtree; tests live in `/tests`.
- The package's public surface is `talaria.cli:main` (the console script) plus
  the importable modules `talaria.paths`, `talaria.hermos.*`,
  `talaria.sync.*`, and the version constant on `talaria.__version__`.
- Backwards-incompatible changes to the CLI surface require a major version bump.

## Local Contracts

- Public CLI surface: `talaria [--version] <command> [<subcommand>] [...flags]`.
- Exit codes: `0` clean, `1` signal fired, `2` tool error.
- **Silent by default.** Every CLI subcommand that produces a
  human-readable report is exit-code-only when run without flags;
  `-v/--verbose` is the opt-in to actually print the report on stdout.
  `--json` and `--show-resolution` always print (explicit data
  channels). Errors always go to stderr. The contract is enforced
  in the dispatcher layer (`src/talaria/cli/AGENTS.md`) and applies
  uniformly to every command group: `hermes`, `config`, `skills`.

  Four carve-outs print by default with no `--verbose` needed, because
  their only job is to print:

  * `talaria paths` — its output *is* the resolved profile + paths.
  * `talaria hermes log-rotate` — explicit-only: with no action flags
    it reports scanned size/age and exits 0 without writing. The report
    is the answer.
  * `talaria completion` — its output *is* the completion script the
    operator asked for.
  * `talaria config sync --list` — its output *is* the dot-path list
    the operator asked for.
- All filesystem and SQLite access is **read-only against the Hermes runtime**
  for inspection features (`hermes moa-truncation`, `paths`).
  Write-bearing carve-outs are explicit: `talaria config sync` copies profile
  artefacts between profiles, `talaria config apply-auxiliary` derives
  `model.aliases` from a profile's `auxiliary` block,
  `talaria config sync-env` refreshes a profile's `.env` values from the
  live environment and can optionally extend the variable scope
  (`--add-key`, `--skip-key`, `--disable-key`, `--enable-key`),
  `talaria hermes fix-context-cache` repairs `context_length_cache.yaml`
  in one profile, `talaria hermes log-rotate` rotates and prunes the
  active profile's `logs/` (and every profile's `logs/` with
  `--all-profiles`) per age and per-file/total size caps, and
  `talaria skills install` / `talaria skills uninstall` install or
  remove third-party skills then update that profile's
  `skills.disabled` policy. None of the inspection features touches
  `state.db`.
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
- `hermos/` — Hermes features: inspections, catalog refresh, explicit
  context-cache repair, skill install/uninstall orchestration, and
  single-profile auxiliary-alias derivation (`talaria config apply-auxiliary`).
- `sync/` — Hermes sync feature group (the write-bearing group behind
  `talaria config sync`; copies profile artefacts between profiles).
- `paths.py` — profile + path resolution shared by every inspection feature.