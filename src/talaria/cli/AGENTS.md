# src/talaria/cli

## Purpose

Console-script entry point and argparse dispatch for the `talaria` command.

## Ownership

- Owns the CLI surface contract: subcommand names, flag spellings, exit codes,
  JSON-vs-human output behavior.
- Owns the `python -m talaria.cli` invocation path via `__main__.py`.

## Local Contracts

- `build_parser()` returns the root `argparse.ArgumentParser`. Adding a feature
  group means adding a subparser here.
- `main(argv=None)` is the only public entry; `argv=None` means
  `argparse.parse_args()` reads `sys.argv`.
- `cmd_<name>(args)` functions are private dispatcher targets — they are
  registered on subparsers via `set_defaults(func=...)` and never called by
  external code.
- `talaria sync` is a flat subcommand (no nested subsubparser): it takes
  `<source>` and optional `<target>`, plus flags that select/configure the
  five sync phases and the `--add-mcp-serve` injection. Defaults to
  apply-by-default; `--dry-run` opts out.

## Work Guidance

- New feature groups: add a module under `talaria/<group>/`, then a subparser
  in `build_parser` that delegates to a `cmd_<group>_<feature>` function.
- Subcommand names use kebab-case (`moa-truncation`); Python functions and
  module names use snake_case (`cmd_hermes_moa_truncation`,
  `talaria.hermos.moa_truncation`).
- `--json` flag is always present on data-producing subcommands and produces
  a JSON dump via `json.dumps(payload, indent=2, default=str)`.
- Profile-agnostic features (e.g. `talaria hermes refresh-catalog`) still
  call `resolve_paths()` for dispatcher shape symmetry, but the resolved
  profile is reported — not consumed — by the feature itself. `refresh-catalog --gateway` selects the provider catalog/source/cache; `--profile` never does.
- Profile-scoped write features (e.g. `talaria hermes fix-context-cache`)
  must expose `--dry-run`, `--no-backup`, `--json`, and `--show-resolution`.

## Verification

- CLI tests live in `tests/test_<feature>.py` and invoke `python -m talaria.cli`
  via `subprocess.run` with a temporary `state.db` and `logs/`.
- `talaria --help` and `talaria <group> --help` must render without warnings.

## Child DOX Index

This package is the leaf — no nested subpackages.