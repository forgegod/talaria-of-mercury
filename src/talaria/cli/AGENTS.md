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
- `talaria completion <shell>` is a top-level command (sibling to
  `paths`, `hermes`, `config`) that prints a self-contained bash or
  zsh completion script to stdout. It takes a single positional
  `shell` argument (choices: `bash`, `zsh`). The generated script is
  pure shell with no per-keystroke Python subprocess; users activate
  it via `eval "$(talaria completion bash)"` or `eval "$(talaria
  completion zsh)"`. The zsh variant must use the `#compdef _funcname
  <cmd>` directive (NOT the bare `#compdef <cmd>` autoload form)
  and must NOT end with a `_talaria "$@"` call — both are required
  so `eval "$(talaria completion zsh)"` in ~/.zshrc wires up
  completion cleanly. A trailing call invokes `_arguments` outside
  completion context when the script is sourced, raising
  `_arguments:comparguments:… can only be called from completion
  function`.

## Work Guidance

- New feature groups: add a module under `talaria/<group>/`, then a subparser
  in `build_parser` that delegates to a `cmd_<group>_<feature>` function.
- Subcommand names use kebab-case (`moa-truncation`); Python functions and
  module names use snake_case (`cmd_hermes_moa_truncation`,
  `talaria.hermos.moa_truncation`).
- `--json` flag is always present on data-producing subcommands and produces
  a JSON dump via `json.dumps(payload, indent=2, default=str)`.
- **Silent-by-default contract.** Every `cmd_*` function that produces a
  human-readable report must gate the `print(text)` on `args.verbose` and
  add `-v/--verbose` to the matching subparser. The default run is exit
  code only — operators pipe through scripts and don't want chatter.
  `--json` and `--show-resolution` always print (explicit data channels).
  Errors always go to stderr.
  When a feature module already has an internal `_say(verbose)` helper
  (e.g. `skill_install`, `skill_uninstall`), the CLI-level gate is in
  addition to it: `--verbose` enables both the per-step progress stream
  and the final report print.

  Carve-outs (do NOT add `-v/--verbose` to these — they print by default
  because their only job is to print):

  * `talaria paths` (`cmd_paths`) — its output is the resolved
    profile + paths. The CLI prints the four `key: value` lines on
    every successful run; `--json` switches the channel to a JSON
    envelope. (Previous behaviour was a `--verbose` gate; that was a
    regression for a debug helper whose job is to print.)
  * `talaria hermes log-rotate` (`cmd_hermes_log_rotate`) — the
    tool is explicit-only: with no `--max-size` / `--max-age` /
    `--max-total` flag the filesystem is never touched and the
    report's `dry_run` is true. In both no-action and action runs
    the renderer always prints; gating it behind `--verbose` would
    hide the "no actions planned" verdict that tells the operator
    nothing was done.
  * `talaria completion` (`cmd_completion`) — its sole output is
    the shell script. (Pre-existing exception.)
  * `talaria config sync --list` (the `--list` branch inside
    `cmd_sync`) — the operator asked for the dot-path list; that's
    the answer. (Pre-existing behaviour, intentionally not gated.)
- Profile-agnostic features (e.g. `talaria hermes refresh-catalog`) still
  call `resolve_paths()` for dispatcher shape symmetry, but the resolved
  profile is reported — not consumed — by the feature itself. `refresh-catalog --gateway` selects the provider catalog/source/cache; `--profile` never does.
- Profile-scoped write features (e.g. `talaria hermes fix-context-cache`)
  must expose `--dry-run`, `--no-backup`, `--json`, and `--show-resolution`.
- `talaria skills` is a top-level command group (sibling to `paths`,
  `hermes`, `config`) with `install` and `uninstall` subcommands. Both
  expand a skill identifier (recursive when it ends in `/*`), delegate
  each install/uninstall to the matching `hermes skills` subcommand, and
  update the profile's `skills.disabled` policy. A non-wildcard
  identifier installs or uninstalls a single skill. The recursive
  behaviour is implicit — there is no separate recursive subcommand.
- `talaria hermes serve-stop` detects and stops the dashboard/serve backend
  by its listening port (profile-agnostic, Linux-only). It takes `--port`,
  `--dry-run`, `--json`, and `--show-resolution`. `--profile` is recorded in
  the report only and does not affect detection.
- `talaria hermes log-rotate` rotates and prunes the active profile's
  `logs/` (or every profile's `logs/` with `--all-profiles`). It takes
  `--max-size BYTES` (per-file cap on the gzipped payload, applied via
  `copy → gzip → truncate`), `--max-age DAYS` (delete rotated copies
  and `logs/curator/<ts>/` directories older than the threshold),
  `--max-total BYTES` (bound the aggregate directory size by
  deleting the oldest rotated copies first), `--keep N` (per-base-name
  floor that protects the newest N rotated copies), `--dry-run`,
  `--json`, `--show-resolution`, and `--profile`. The tool is
  explicit-only: with no prune/rotate flags the file system is never
  touched regardless of `--dry-run`. `--all-profiles` sweeps the
  root `~/.hermes/logs/` plus every `~/.hermes/profiles/*/logs/`.
- `talaria completion` delegates to `talaria.cli.completion`, which walks the
  live `build_parser()` tree at invocation time and emits a static bash/zsh
  script. Architectural note: completion is coupled to the CLI parameter
  surface — any change to subcommand names, option flags, or argument
  arity (e.g. adding `--json` to a new feature, changing `nargs`, or adding
  a new subparser group) also changes the completion output. The coupling is
  satisfied by construction because `collect()` introspects the parser tree
  rather than maintaining a separate spec, but the contract must hold: if
  `build_parser()` exposes a flag, `talaria completion` must surface it.

## Verification

- CLI tests live in `tests/test_<feature>.py` and invoke `python -m talaria.cli`
  via `subprocess.run` with a temporary `state.db` and `logs/`.
- `talaria --help` and `talaria <group> --help` must render without warnings.

## Child DOX Index

This package is the leaf — no nested subpackages.