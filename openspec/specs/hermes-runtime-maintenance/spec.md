# Hermes runtime maintenance: catalog, serve process, logs

**Primary surface:** none

## Purpose

Maintain three pieces of Hermes runtime state outside `state.db`: the gateway
model catalog cache, a listening serve backend, and on-disk logs. Each command
has its own network, signalling, and deletion boundary; none is a general
`--apply` tool. Catalog refresh fetches by default, serve-stop signals by
default, and log rotation writes only when an action flag is present.

## Requirements

### Requirement: Gateway catalog refresh

`refresh-catalog` SHALL fetch and reshape the selected gateway catalog into
Hermes' provider-manifest schema and write atomically. Only `kilocode` SHALL
be supported. A cache younger than `--max-age-seconds` (default 6 hours) SHALL
skip fetching unless `--force` is supplied. `--profile` SHALL be metadata only,
not select the shared cache. The command MUST NOT read or write `state.db`.

#### Scenario: Catalog connection defaults are used

- **WHEN** the selected gateway is `kilocode`
- **THEN** its default source is `https://api.kilo.ai/api/gateway/models` and its destination is `$XDG_CACHE_HOME/kilocode_catalog.json`
- **AND** `KILOCODE_API_KEY` comes from the environment first, then `~/.hermes/.env`; `--src-url` overrides the endpoint
- **AND** `--gateway` selects the catalog and provider key, independently of the reported profile

#### Scenario: A fresh cache already exists

- **WHEN** the cache file is younger than the max age and `--force` is absent
- **THEN** no network fetch occurs, the report reason records the fresh skip, and the exit code is 0

#### Scenario: The credential is absent

- **WHEN** `KILOCODE_API_KEY` is in neither the environment nor `~/.hermes/.env`
- **THEN** the report reason is an auth error, the hint names the credential, and the exit code is 2

#### Scenario: The catalog is refreshed

- **WHEN** the cache is stale or `--force` is supplied and the endpoint returns a catalog
- **THEN** the manifest is reshaped and written atomically, with the temporary file removed on failure

#### Scenario: A non-default gateway is requested

- **WHEN** `--gateway` names an unsupported gateway
- **THEN** the command reports a configuration error and exits 2

### Requirement: Catalog output and exit codes

Refresh exit code SHALL be 0 for refreshed, reshaped, or fresh-cache-skip runs
and 2 for auth, network, parse, write, or unknown-gateway errors, with the
report's `reason` distinguishing them. The command SHALL be silent unless `-v`,
`--json`, or `--show-resolution` is used.

#### Scenario: A refresh succeeds quietly

- **WHEN** the fetch and reshape succeed without verbosity flags
- **THEN** stdout is empty and the exit code is 0

### Requirement: Serve process stop

`serve-stop` SHALL find TCP listeners with `psutil.net_connections("inet")`
(default port 9119), exclude its PID, send `SIGTERM`, poll up to 5 seconds,
then `SIGKILL` survivors. `--dry-run` SHALL report PIDs without signalling.
`reason` SHALL be `stopped`, `none`, `detected`, or `partial`; exits SHALL be
0 for the first three and 2 for `partial`. `--profile` SHALL be metadata only;
no profile state is consumed. Output SHALL be silent unless `-v`, `--json`,
or `--show-resolution` is used.

#### Scenario: Nothing is listening

- **WHEN** no process listens on the selected port
- **THEN** the reason is `none`, no signal is sent, and the exit code is 0

#### Scenario: A backend ignores SIGTERM

- **WHEN** a detected process is still alive after the five-second grace period
- **THEN** it receives `SIGKILL` and the reason is `stopped`

#### Scenario: Signalling is not permitted

- **WHEN** the process exists but the operator lacks permission to signal it
- **THEN** the reason is `partial` and the exit code is 2

#### Scenario: The operator previews the stop

- **WHEN** `--dry-run` is supplied with a listener present
- **THEN** the report lists the detected PIDs and no signal is sent

### Requirement: Explicit-only log rotation

`talaria hermes log-rotate` SHALL be explicit-only: with no `--max-size`,
`--max-age`, or `--max-total` it MUST scan and report but never write, and the
report's `dry_run` SHALL be true. With at least one of those flags it SHALL
apply by default, and `--dry-run` SHALL preview instead. There is no `--apply`
flag for this command.

#### Scenario: No action flags are supplied

- **WHEN** the command runs with no size, age, or total limit
- **THEN** it reports scanned files and exits 0 without rotating, deleting, gzipping, or truncating anything

#### Scenario: A limit is supplied with dry-run

- **WHEN** `--max-size` is combined with `--dry-run`
- **THEN** the plan is reported and the filesystem is unchanged

### Requirement: Rotation and pruning mechanics

Rotation SHALL copy, gzip at level 6 to `<name>.1.gz`, then truncate the active
file without deleting it. `--max-age` SHALL delete old rotated copies and whole
curator snapshots. `--max-total` SHALL prune oldest rotated copies until below
the cap. `--keep` (default 1) SHALL protect the newest N copies per base name.
`--all-profiles` SHALL include root `logs/` and every `profiles/*/logs/`.

#### Scenario: The same active log is rotated again

- **WHEN** another rotation writes the same base name's `.1.gz` slot
- **THEN** the previous rotated copy is overwritten and no further archival copy is produced

#### Scenario: An active log exceeds the size cap

- **WHEN** the gzipped footprint of an active file exceeds `--max-size`
- **THEN** the bytes are written to `<name>.1.gz`, the active file is truncated to zero, and the active file still exists

#### Scenario: A rotated copy is protected by the keep floor

- **WHEN** `--keep 2` is supplied and several rotated copies exist for one base name
- **THEN** the two newest copies survive size and age pruning

#### Scenario: A curator snapshot is old

- **WHEN** a `logs/curator/<timestamp>/` directory is older than `--max-age`
- **THEN** the directory is deleted as a unit

### Requirement: Log-rotate output and protected names

Log rotation SHALL always print its human report and exit 0, and `--json` SHALL
emit `{"reports": [...]}`. `README.md`, `AGENTS.md`, and `.gitkeep` MUST never
be rotated or deleted.

#### Scenario: The directory contains protected files

- **WHEN** `README.md`, `AGENTS.md`, or `.gitkeep` sits in the log directory
- **THEN** it is classified as protected and excluded from rotation and pruning

#### Scenario: Several profiles are swept

- **WHEN** `--all-profiles` is supplied
- **THEN** one report entry is produced for the root logs directory and for each profile log directory that exists

## Verification

- `src/talaria/hermes/refresh_catalog.py` — gateway table, credential discovery, fetch, reshape, cache freshness, run and renderer.
- `src/talaria/hermes/serve_stop.py` — port detection, signal escalation, run and renderer.
- `src/talaria/hermes/log_rotate.py` — classification, planning, apply, renderer, and resolution.
- `src/talaria/cli/__init__.py` — `cmd_hermes_refresh_catalog`, `cmd_hermes_serve_stop`, `cmd_hermes_log_rotate`, and `_log_rotate_target_paths`.
- `tests/test_refresh_catalog.py` — `TestReshape` (field normalisation, free detection, sort order, atomic write), `TestCredentialDiscovery`, `TestCacheFreshness`, `TestFetch` (stubbed urllib: auth, HTTP, network, timeout), `TestRun` (fresh skip, force, full refresh, error reasons, temp cleanup), `TestRenderer`, and `TestCli`.
- `tests/test_serve_stop.py` — `TestFindPids`, `TestRun` (none, dry-run, SIGTERM then SIGKILL, partial failure, process lookup), `TestPidAlive`, `TestRenderer`, and `TestCli`.
- `tests/test_log_rotate.py` — `TestParseRotated`, `TestClassify`, `TestRotateActive`, `TestAgePrune`, `TestTotalPrune`, `TestKeepFloor`, `TestDryRun`, `TestMultiProfile`, `TestRunReport`, and `TestCli` (default printing, no-flags scan).
- [Architecture](../../../docs/architecture.md) — network, signalling, and deletion boundaries.
- [Repository contract](../../../AGENTS.md) — write boundaries and evidence isolation.
- Related current spec: [CLI entry point, profile resolution, and shell completion](../cli-entry-profile-resolution-completion/spec.md).
