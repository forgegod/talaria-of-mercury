# CAP-001 — CLI entry point, profile resolution, and shell completion

**Status:** implemented
**Primary surface:** none

## Behaviour

- `talaria` is a non-interactive console command: `talaria [--version] <command> …`
  with five top-level commands — `paths`, `completion`, `hermes`, `skills`, and
  `config`. `--version` prints the version from `talaria.__version__`.
- `talaria paths` resolves one profile and its two state paths. Priority is
  `--profile` → `HERMES_PROFILE` → the content of `~/.hermes/active_profile` →
  `default`. `--state-db` and `--log-dir` override the resolved paths without
  changing the reported profile name.
- The `default` profile lives directly under `~/.hermes/`; a named profile lives
  under `~/.hermes/profiles/<name>/`. Resolution never checks that the files
  exist; a missing `state.db` is the consuming command's problem.
- `talaria paths` prints four `key: value` lines on every successful run.
  `--json` emits the same data plus an `overrides` block describing which inputs
  were present.
- `talaria completion bash|zsh` walks the live `build_parser()` tree and prints a
  static completion script. The zsh form uses `#compdef _talaria talaria` and
  deliberately omits a trailing `_talaria "$@"` call, so `eval "$(talaria
  completion zsh)"` is safe in `~/.zshrc`.
- Output is silent by default for report-producing commands; `-v/--verbose`
  opts in, while `--json` and `--show-resolution` always print and errors go to
  stderr. Seven surfaces print without `-v`: `paths`, `completion`,
  `hermes log-rotate`, `hermes doctor`, `hermes benchmark`, `skills prune`, and
  `config sync --list`.
- The general exit convention is `0` clean, `1` signal fired, `2` handled tool
  or usage error; uncaught exceptions and command-specific dispatch paths can
  differ (see the other CAPs). Signal
  codes are used by `doctor` (any finding fires) and `benchmark` (smoke or
  vision failure).

## Implementation

- `src/talaria/cli/__init__.py` — `build_parser()`, `main()`, every `cmd_*`
  dispatcher, and JSON serialisation.
- `src/talaria/cli/completion.py` — parser-tree introspection plus `render_bash`
  and `render_zsh`.
- `src/talaria/paths.py` — `resolve_profile_name`, `profile_paths`,
  `resolve_paths`, and `ResolvedPaths`.
- `src/talaria/cli/__main__.py` — `python -m talaria.cli` entry point.
- `src/talaria/__init__.py` — `__version__`.

## Rules and boundaries

- `HERMES_HOME` is not an input to `resolve_paths`; it is only set on delegated
  `hermes skills …` subprocesses. Resolution is anchored at `~/.hermes`.
- Completion output is coupled to the parser by construction: adding or renaming
  a subcommand, flag, or `nargs` changes the generated script. There is no
  second, hand-maintained completion spec.
- Commands never prompt. Reports are the only stdout channel; error text goes
  to stderr.
- Profile-name resolution for `config sync` is separate (`talaria.sync.paths`)
  and additionally accepts `main`/`base` aliases and a literal `config.yaml`
  path; see CAP-006.

## Verification

- `tests/test_paths.py` — `TestResolveProfileName` (flag, env, active-profile
  file, empty file, fallback), `TestProfilePaths`,
  `TestResolvePaths::test_path_flags_win_over_profile_resolution`,
  `TestCanonicalRoot`, and `TestCli::test_default_run_prints_paths`.
- `tests/test_completion.py` — `TestCollect` (five top-level commands, nested
  groups, flag collection), `TestBash::test_bash_syntax_valid`,
  `TestBash::test_bash_completion_offers_top_level_subs`,
  `TestZsh::test_has_compdef_directive`, `TestZsh::test_zsh_syntax_valid`,
  `TestZsh::test_zsh_function_loads`, and
  `TestCli::test_zsh_output_evals_silently_in_zshrc`.
- `tests/test_doctor.py` — `TestCli::test_version_flag`.

## Related contracts

- [Architecture](../../architecture.md)
- [Repository contract](../../../AGENTS.md)
