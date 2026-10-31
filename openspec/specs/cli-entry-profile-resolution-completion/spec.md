# CLI entry point, profile resolution, and shell completion

**Primary surface:** none

## Purpose

Define the non-interactive `talaria` command surface, how one Hermes profile and
its state paths are selected, and how bash and zsh completion are derived from
the live parser. Inspection dispatchers use this profile/path pair; profile sync
has separate resolution with `main`/`base` aliases and literal config paths.
Write defaults differ per command and belong to each capability spec.

## Requirements

### Requirement: Non-interactive command tree

`talaria` SHALL expose a non-interactive console command of the form
`talaria [--version] <command> …` with five top-level commands: `paths`,
`completion`, `hermes`, `skills`, and `config`. `--version` SHALL print the
version from `talaria.__version__`. Commands MUST NOT prompt; reports are the
only stdout channel and error text MUST go to stderr.

#### Scenario: An operator asks for the version

- **WHEN** the operator runs `talaria --version`
- **THEN** the process prints `talaria <version>` from `talaria.__version__` and exits zero
- **AND** no profile state is read

#### Scenario: A command needs input it does not have

- **WHEN** a required argument or subcommand is missing or invalid
- **THEN** argparse writes usage text to stderr and the process exits 2
- **AND** the command does not fall back to an interactive prompt

### Requirement: General exit-code convention

Commands SHALL use `0` for a clean run, `1` for a fired signal or failed
verdict, and `2` for a handled tool, validation, or usage error. Uncaught
exceptions and command-specific dispatch paths MAY differ where their own
specification says so. The signal code `1` is used by `hermes doctor` for any
firing finding and by `hermes benchmark` for a smoke or vision failure.

#### Scenario: A handled error is reported

- **WHEN** a command rejects an unknown detector id, an unresolvable profile, or a mutually exclusive flag pair
- **THEN** it prints an error to stderr and exits 2

#### Scenario: An unexpected exception escapes

- **WHEN** a command raises an uncaught exception outside its handlers
- **THEN** Python's default traceback path determines the exit status, which is not part of the 0/1/2 contract

### Requirement: Profile and state path resolution

`talaria paths` SHALL resolve a profile by `--profile`, then `HERMES_PROFILE`,
then `~/.hermes/active_profile`, then `default`. `--state-db` and `--log-dir`
SHALL override artifact paths without changing the reported profile name.
Resolution SHALL use the canonical `~/.hermes` layout, ignore `HERMES_HOME`,
and not require artifact existence; consuming commands handle missing state.

#### Scenario: Canonical profile locations are resolved

- **WHEN** the default or a named profile is selected, even with `HERMES_HOME` set
- **THEN** the default lives directly under `~/.hermes/` and a named profile under `~/.hermes/profiles/<name>/`
- **AND** inherited `HERMES_HOME` does not affect resolution; only delegated `hermes skills …` subprocesses receive it for their selected profile

#### Scenario: Several profile inputs are present

- **WHEN** `--profile`, `HERMES_PROFILE`, and an active-profile file all name different profiles
- **THEN** the flag wins, then the environment variable, then the file content, and an absent or empty file falls through to `default`

#### Scenario: Artifact paths are overridden

- **WHEN** the operator passes `--state-db` or `--log-dir`
- **THEN** the corresponding reported path changes while the profile name still follows profile resolution

#### Scenario: A profile has never been used

- **WHEN** the resolved `state.db` or `logs/` directory does not exist
- **THEN** `paths` still prints the resolved locations and exits zero instead of failing

### Requirement: Path report shape

`talaria paths` SHALL print four `key: value` lines on every successful run:
`profile`, `hermes_root`, `state_db`, and `log_dir`. `--json` SHALL emit the
same data plus an `overrides` block recording which inputs were present:
profile flag, state-db flag, log-dir flag, `HERMES_PROFILE`, and the
active-profile file.

#### Scenario: Paths are reported as text

- **WHEN** the operator runs `talaria paths` without `--json`
- **THEN** exactly those four labelled lines print to stdout and the exit code is zero

#### Scenario: Paths are reported as JSON

- **WHEN** the operator runs `talaria paths --json`
- **THEN** the object carries the four path fields and an `overrides` object whose booleans reflect the supplied flags, environment, and file presence

### Requirement: Verbosity and default printing

Report-producing commands SHALL stay silent by default. `-v/--verbose` opts in
to the human report, while `--json` and `--show-resolution` MUST always print.
Seven surfaces print their report without `-v`: `paths`, `completion`,
`hermes log-rotate`, `hermes doctor`, `hermes benchmark`, `skills prune`, and
`config sync --list`.

#### Scenario: A quiet command succeeds

- **WHEN** `config sync` or `skills install` completes successfully without `-v` or `--json`
- **THEN** stdout stays empty and the exit code conveys the result

#### Scenario: A default-printing command runs

- **WHEN** `hermes doctor`, `hermes benchmark`, `hermes log-rotate`, `skills prune`, `paths`, or `completion` runs without `-v`
- **THEN** the human report or script still prints

### Requirement: Parser-derived shell completion

`talaria completion bash|zsh` SHALL walk the live `build_parser()` tree and
print a static completion script, so adding or renaming a subcommand, flag, or
`nargs` changes the generated script. There MUST be no second, hand-maintained
completion specification. The zsh form SHALL use `#compdef _talaria talaria`
and MUST omit a trailing `_talaria "$@"` call so
`eval "$(talaria completion zsh)"` is safe in `~/.zshrc`.

#### Scenario: The zsh script is evaluated in a shell startup file

- **WHEN** the operator evaluates `talaria completion zsh` in `~/.zshrc`
- **THEN** the completion function registers without `_arguments` context errors and without printing on load

#### Scenario: The parser grows a command

- **WHEN** a subcommand or option is added to `build_parser()`
- **THEN** both generated scripts gain the corresponding candidate without a separate spec edit

## Verification

- `src/talaria/cli/__init__.py` — `build_parser()`, `main()`, every `cmd_*` dispatcher, and JSON serialisation.
- `src/talaria/cli/__main__.py` — `python -m talaria.cli` entry point.
- `src/talaria/cli/completion.py` — parser-tree introspection plus `render_bash` and `render_zsh`.
- `src/talaria/paths.py` — `resolve_profile_name`, `profile_paths`, `resolve_paths`, and `ResolvedPaths`.
- `src/talaria/__init__.py` — `__version__`.
- `tests/test_paths.py` — `TestResolveProfileName` (flag, environment, active-profile file, empty file, fallback), `TestProfilePaths`, `TestResolvePaths::test_path_flags_win_over_profile_resolution`, `TestCanonicalRoot` (root anchored at `~/.hermes`), and `TestCli::test_default_run_prints_paths`.
- `tests/test_completion.py` — `TestCollect` (five top-level commands, nested groups, flag and `nargs` collection), `TestBash::test_bash_syntax_valid`, `TestBash::test_bash_completion_offers_top_level_subs`, `TestZsh::test_has_compdef_directive`, `TestZsh::test_zsh_syntax_valid`, `TestZsh::test_zsh_function_loads`, and `TestCli::test_zsh_output_evals_silently_in_zshrc`.
- `tests/test_doctor.py` — `TestCli::test_version_flag` proves the version flag on the installed entry point.
- [Architecture](../../../docs/architecture.md) — runtime boundary, path resolution, and per-command side effects.
- [Repository contract](../../../AGENTS.md) — record authority and write boundaries.
- Related current specs: [Doctor diagnostics and remediation](../doctor-diagnostics-remediation/spec.md), [Model benchmark](../model-benchmark/spec.md), [Profile sync](../profile-sync/spec.md), [Single-profile configuration helpers](../single-profile-config-helpers/spec.md).
