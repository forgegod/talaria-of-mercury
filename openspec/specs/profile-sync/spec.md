# Profile sync

**Primary surface:** none

## Purpose

Copy profile artefacts from a source to a target. This is the write-bearing
profile-to-profile command: it applies by default, writes backups before
overwriting, and never reads or writes `state.db` or `logs/`. The reverse
direction, refreshing one profile's `.env` from the live process environment,
is a separate single-profile helper.

## Requirements

### Requirement: Profile specification and listing

`talaria config sync <source> <target>` SHALL accept a profile spec of
`default`, `main`, or `base`, a named profile under
`~/.hermes/profiles/<name>/`, or a literal `config.yaml` path. The target SHALL
be required; a missing profile MUST report the available profiles and exit 2.
`--list` SHALL print the dot-notation paths of the source `config.yaml` at
`--list-depth` (default 2) and exit without needing a target.

#### Scenario: A missing profile is named

- **WHEN** the source or target does not resolve to an existing profile
- **THEN** the error lists the available profiles and the exit code is 2

#### Scenario: Configuration paths are listed

- **WHEN** `--list` is supplied with a valid source and no target
- **THEN** the source config's dot paths print to stdout and the exit code is 0

### Requirement: Phase selection

Six phases SHALL run by default: `config.yaml`, `SOUL.md`, `skills/`, `.env`,
`context_length_cache.yaml`, and `auth.json`. Each SHALL be individually
skippable with `--skip-config`, `--skip-soul`, `--skip-skills`, `--skip-env`,
`--skip-cache`, or `--skip-auth`.

#### Scenario: The auth phase is skipped

- **WHEN** `--skip-auth` is supplied
- **THEN** no `auth.json` is read from any profile and no target token is written

#### Scenario: Only selected phases run

- **WHEN** several skip flags are combined
- **THEN** the report covers the remaining phases and the skipped artefacts are untouched

### Requirement: Config replacement, filtering, and timestamp guard

Plain sync SHALL replace target config with source. `--exclude`
SHALL keep listed target values; `--only` SHALL copy only listed
paths; the two MUST be mutually exclusive. Filtered or MCP-injection runs SHALL
be guarded by a source-newer-than-target timestamp check unless
`--force-config` is passed; plain replacement MUST NOT be guarded.
`--add-mcp-serve` SHALL inject or update an `mcp_servers.<name>` SSE entry using
`--mcp-serve-name`, `--mcp-serve-port`, and `--mcp-serve-host`.

#### Scenario: Excluded paths keep target values

- **WHEN** `--exclude` lists a path present in both configs
- **THEN** the target value survives and the remaining source values replace the target

#### Scenario: The source is older than the target

- **WHEN** a filtered or MCP-injection sync runs and the source `config.yaml` is not newer
- **THEN** the config phase is skipped unless `--force-config` is supplied

#### Scenario: Plain replacement runs

- **WHEN** no filter and no MCP injection is requested
- **THEN** the timestamp guard does not apply and source configuration replaces target configuration

#### Scenario: Both filters are requested

- **WHEN** `--exclude` and `--only` are supplied together
- **THEN** the combination is rejected before any phase runs and the exit code is 2

### Requirement: Skills phase filtering

The skills phase SHALL copy skill directories and SHALL support
`--sync-skills [FILTER …]` by category or `category/skill-name`.

#### Scenario: A category filter is supplied

- **WHEN** `--sync-skills software-development` is passed
- **THEN** only skills in that category are copied

#### Scenario: A skill path filter is supplied

- **WHEN** `--sync-skills category/skill-name` is passed
- **THEN** only that skill directory is copied

### Requirement: Additive environment and source-wins cache

The `.env` phase SHALL be additive: missing source variables are appended to
the target and existing target values MUST win on conflict. The context-cache
phase SHALL append missing keys and let the source win on conflict.

#### Scenario: Both files define a variable

- **WHEN** source and target `.env` files define the same key with different values
- **THEN** the target value remains

#### Scenario: The cache files disagree

- **WHEN** source and target `context_length_cache.yaml` define the same key
- **THEN** the source value is written to the target

### Requirement: Newest provider token merge

The auth phase SHALL scan every profile's `auth.json`, select the newest token
per provider by `last_refresh`, then `obtained_at` (with the top-level
`updated_at` as a fallback), and write it into the target. Only the `providers`
block SHALL be merged; other target fields MUST be preserved. This phase reads
every profile's `auth.json`, not only source and target.

#### Scenario: Another profile holds a newer token

- **WHEN** a third profile's token is newer than both source and target tokens for a provider
- **THEN** the third profile's token is written into the target

#### Scenario: The target already holds the newest token

- **WHEN** the target's provider token is the most recent
- **THEN** the target value is preserved

#### Scenario: Non-provider fields exist in the target

- **WHEN** `auth.json` carries `active_provider`, credential-pool entries, or other non-provider fields
- **THEN** those fields survive the merge unchanged

### Requirement: Apply, preview, backups, and pre-flight guards

Sync SHALL apply by default and `--dry-run` SHALL preview every phase. Backups
SHALL be written before overwriting unless `--no-backup` is passed. A
same-profile source and target pair and the `--exclude`/`--only` combination
MUST be rejected before any phase runs. Sync MUST NOT read or write `state.db`
or `logs/`. Content-equivalent files SHALL be skipped where a phase supports
that check.

#### Scenario: Source and target resolve to one profile

- **WHEN** `default` and `main`, or two paths to the same config, are supplied as source and target
- **THEN** the run is rejected before any phase and the exit code is 2

#### Scenario: The operator previews a sync

- **WHEN** `--dry-run` is supplied
- **THEN** every selected phase reports its plan and no target file is modified

#### Scenario: Backups are disabled

- **WHEN** `--no-backup` is supplied and an existing file is overwritten
- **THEN** no recovery copy is produced for that file

### Requirement: Exit codes and output

Exit code SHALL be 0 on success and 2 for resolution, validation, or phase
errors. The default run SHALL be silent, `-v/--verbose` SHALL print the human
report, and `--json` SHALL emit the structured report.

#### Scenario: A successful default run

- **WHEN** sync completes without `-v` or `--json`
- **THEN** stdout is empty and the exit code is 0

#### Scenario: A phase fails

- **WHEN** any selected phase reports an error
- **THEN** the structured report marks the run unsuccessful and the exit code is 2

## Verification

- `src/talaria/cli/__init__.py` — `cmd_sync`, `_build_sync_options`, and `sync_list_config_paths`.
- `src/talaria/sync/run.py` — `SyncOptions`, `run_sync`, and phase selection.
- `src/talaria/sync/paths.py` — `SyncProfile`, `resolve_profile`, `list_profiles`, and `same_profile`.
- `src/talaria/sync/config.py` — replacement, filtered merge, and MCP injection.
- `src/talaria/sync/soul.py` — soul copy.
- `src/talaria/sync/skills.py` — skills tree copy and filters.
- `src/talaria/sync/env.py` — additive environment merge.
- `src/talaria/sync/context_cache.py` — source-wins cache merge.
- `src/talaria/sync/auth_tokens.py` — newest provider-token selection and providers-only merge.
- `src/talaria/sync/mcp_serve.py` — target-only MCP configuration helper.
- `src/talaria/sync/writer.py` — atomic file replacement and backup.
- `src/talaria/sync/yaml_io.py` — YAML loading, dumping, and validation.
- `src/talaria/sync/render.py` — human and JSON rendering.
- `src/talaria/sync/result.py` — structured report types.
- `tests/test_sync.py` — `TestResolveProfile` (default aliases, named profile, file path, missing-profile message, `list_profiles`), `TestDotPath`, `TestYamlIO`, `TestConfigPhase` (exclude and only, timestamp guard, force, mcp-serve), `TestSoulPhase`, `TestSkillsPhase`, `TestEnvPhase`, `TestContextCachePhase`, `TestAuthTokensPhase` (newer token, newest across three profiles, non-provider preservation, backups), `TestRunSync`, and `TestSyncCLI` (dry-run, default apply write, silent default, verbose report, `--list`, exclude).
- [Architecture](../../../docs/architecture.md) — sync phase and backup boundaries.
- [Repository contract](../../../AGENTS.md) — write boundaries.
- Related current specs: [Single-profile configuration helpers](../single-profile-config-helpers/spec.md), [CLI entry point, profile resolution, and shell completion](../cli-entry-profile-resolution-completion/spec.md).
