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
- `talaria config sync` is a flat subcommand under the `config` command
  group (no nested subsubparser): it takes `<source>` and optional
  `<target>`, plus flags that select/configure the five sync phases and
  the `--add-mcp-serve` injection. Defaults to apply-by-default;
  `--dry-run` opts out.
- `talaria config apply-auxiliary` is the sibling command under
  `config`: single-profile alias derivation. It takes `--profile` /
  `--config-path` and the standard write-feature flags
  (`--dry-run`, `--no-backup`, `--json`, `--show-resolution`).
- `talaria config sync-env` is the third sibling under `config`:
  single-profile `.env` value refresh from the live process
  environment. It takes `--profile` / `--env-path` and the standard
  write-feature flags. Existing target keys get their values
  overwritten from `os.environ`; four optional repeatable flags
  extend the variable scope: `--add-key` appends a new key from the
  environment, `--skip-key` excludes a key from the refresh,
  `--disable-key` comments a key out, and `--enable-key` uncomments
  a previously disabled key.

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
- `talaria hermes install-skills-recursive` expands wildcard skill identifiers
  and delegates actual installation to the Hermes CLI; Talaria only owns the
  recursive expansion and `skills.disabled` policy update.
- `talaria hermes serve-stop` detects and stops the dashboard/serve backend
  by its listening port (profile-agnostic, Linux-only). It takes `--port`,
  `--dry-run`, `--json`, and `--show-resolution`. `--profile` is recorded in
  the report only and does not affect detection.

## Verification

- CLI tests live in `tests/test_<feature>.py` and invoke `python -m talaria.cli`
  via `subprocess.run` with a temporary `state.db` and `logs/`.
- `talaria --help` and `talaria <group> --help` must render without warnings.

## Child DOX Index

This package is the leaf — no nested subpackages.