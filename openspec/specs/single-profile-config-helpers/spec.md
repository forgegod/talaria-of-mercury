# Single-profile configuration helpers

**Primary surface:** none

## Purpose

Maintain one profile's `config.yaml` and `.env` without a source/target split.
`config apply-auxiliary` derives alias pins from the same file, and
`config sync-env` refreshes existing `.env` values from the live process
environment. Both apply by default, write through the atomic backup writer, and
never touch `state.db` or `logs/`. Profile-to-profile copying is a separate
capability.

## Requirements

### Requirement: Auxiliary alias derivation

`apply-auxiliary` SHALL derive `model.aliases._<usecase>` from concrete
`auxiliary.<usecase>.model` pins in the same profile's `config.yaml`.
Missing, empty or sentinel pins SHALL be skipped. Unrelated aliases and a
non-dict `model.aliases` SHALL remain untouched. Unchanged pins SHALL be a
no-op. Model strings SHALL be copied without validating model existence.

#### Scenario: Concrete pins are present

- **WHEN** `auxiliary.curator.model` names a concrete model
- **THEN** `model.aliases._curator` is created or updated to that value

#### Scenario: A sentinel pin is present

- **WHEN** an auxiliary use case pins `auto`, `inherit`, `default`, `use_default`, `use_model`, or `use_provider`
- **THEN** no alias is derived for that use case

#### Scenario: The operator already defined unrelated aliases

- **WHEN** `model.aliases` contains keys outside the derived set
- **THEN** those keys and values are preserved

#### Scenario: The helper runs twice

- **WHEN** the first run has written the derived aliases and no pin changed
- **THEN** the second run reports no added or updated aliases and leaves the file content-equivalent

### Requirement: Auxiliary report and write boundary

The auxiliary report SHALL distinguish `added`, `updated`, `kept`, and
`preserved` aliases. Writes SHALL go through the atomic backup writer;
`--dry-run` MUST write nothing and `--no-backup` SHALL suppress the `.bak`
sidecar. An explicit `--config-path` SHALL override profile resolution.

#### Scenario: A dry run is requested

- **WHEN** `--dry-run` is supplied
- **THEN** the would-be aliases are reported and the config file is byte-identical

#### Scenario: Backups are suppressed

- **WHEN** `--no-backup` is supplied with an actual write
- **THEN** the config is replaced atomically without a `.bak` file

### Requirement: Environment value refresh

`sync-env` SHALL refresh one profile's `.env` values from
the live process environment. For each active `KEY=…` line already in the file,
the value SHALL be replaced with the environment value. Keys absent from the
file MUST NOT be added by default. An empty environment value MUST NOT
overwrite the file value, and comments, blank lines, and unrelated lines SHALL
be preserved. A missing file SHALL be a no-op unless `--add-key` supplies an
addable key, in which case the file is created.

#### Scenario: The environment has a new value

- **WHEN** an active `KEY=old` line exists and the environment defines `KEY=new`
- **THEN** the line becomes `KEY=new`

#### Scenario: The environment value is empty

- **WHEN** the environment defines `KEY=` while the file holds a non-empty value
- **THEN** the file value is kept

#### Scenario: The file does not exist

- **WHEN** no `.env` exists and no addable key is requested
- **THEN** nothing is created and the run reports a no-op

### Requirement: Orthogonal key operations

Four repeatable key operations SHALL extend the refresh baseline. `--add-key
KEY` SHALL append a named key that is absent from the file and present in the
environment with a non-empty value. `--skip-key KEY` SHALL keep a key's file
value unchanged. `--disable-key KEY` SHALL turn `KEY=value` into `#KEY=value`
and drop an `export` prefix. `--enable-key KEY` SHALL restore `#KEY=value`
verbatim and exclude that key from the environment refresh on the same run.

#### Scenario: A key is added

- **WHEN** `--add-key KEY` names a key absent from the file and present in the environment
- **THEN** the key is appended with the environment value

#### Scenario: A key is skipped

- **WHEN** `--skip-key KEY` is supplied and the environment defines a different value
- **THEN** the file value is preserved and the report lists the preserved key

#### Scenario: A key is disabled

- **WHEN** `--disable-key KEY` targets `export KEY=value`
- **THEN** the line becomes `#KEY=value` without the `export` prefix

#### Scenario: A key is enabled

- **WHEN** `--enable-key KEY` targets `#KEY=value`
- **THEN** the line becomes `KEY=value` with the stored value, not the environment value, and the key is excluded from the refresh

### Requirement: Apply, backups, and exit codes

Both helpers SHALL apply by default and `--dry-run` SHALL preview. Both SHALL
write through the atomic backup writer with a default `.bak`. They SHALL be
silent unless `-v`, `--json`, or `--show-resolution` is used. Successful runs
SHALL exit 0; the dispatcher SHALL map a reported `ok: false` to 2, while
uncaught I/O or YAML exceptions can exit 1.

#### Scenario: A helper succeeds silently

- **WHEN** either helper runs without verbosity flags
- **THEN** stdout is empty and the exit code is 0

#### Scenario: A reported failure occurs

- **WHEN** a helper returns `ok: false`
- **THEN** the dispatcher exits 2

### Requirement: Secret exposure boundary

`sync-env --json` SHALL report `updated[].old` and `updated[].new` values, so
that channel can expose secrets. The human renderer SHALL print key names only,
and `--show-resolution` SHALL list key names without values. Output MUST NOT be
treated as uniformly redacted.

#### Scenario: JSON output is inspected

- **WHEN** `sync-env --json` refreshes a key
- **THEN** the report carries the previous and new values for that key

#### Scenario: The human report is inspected

- **WHEN** the same run prints the human report or `--show-resolution`
- **THEN** only key names appear, without values

### Requirement: Single-profile scope

`apply-auxiliary` MUST NOT read another profile's config and `sync-env` MUST
NOT copy values from another profile. `sync-env` SHALL treat the environment as
authoritative for values but the file as authoritative for scope, so no key is
added without `--add-key`. Neither helper touches `state.db` or `logs/`.

#### Scenario: The operator needs values from another profile

- **WHEN** a second profile holds the desired environment values
- **THEN** `sync-env` does not read that profile; the values must come from the live environment

## Verification

- `src/talaria/hermes/auxiliary.py` — sentinel set, alias derivation, merge, atomic write, renderer, `show_resolution`.
- `src/talaria/hermes/sync_env.py` — line scanner, refresh, add/skip/disable/enable resolution, payload builder, atomic write, renderer, `show_resolution`.
- `src/talaria/sync/writer.py` — atomic write plus optional backup.
- `src/talaria/cli/__init__.py` — `cmd_config_apply_auxiliary` and `cmd_config_sync_env`.
- `tests/test_auxiliary.py` — `TestApplyAuxiliary` (injection, sentinel skipping, alias preservation, no-op, idempotency, dry-run, profile and explicit-path resolution, resolution output) and `TestAuxiliaryCLI` (apply and JSON, dry-run, `--show-resolution`).
- `tests/test_sync_env.py` — `TestSyncEnv` (refresh, no-add default, export prefix, comments, empty environment, absent keys, dry-run, backup), `TestSyncEnvAddKeys`, `TestSyncEnvSkipKeys`, `TestSyncEnvDisableKeys`, `TestSyncEnvEnableKeys`, `TestSyncEnvCombinedOperations`, `TestSyncEnvShowResolutionNewOps`, `TestSyncEnvCLI`, `TestSyncEnvAddKeysCLI`, and `TestSyncEnvNewOpsCLI`.
- [Architecture](../../../docs/architecture.md) — single-profile helper write boundaries and secret-handling note.
- [Repository contract](../../../AGENTS.md) — write boundaries and evidence isolation.
- Related current specs: [Profile sync](../profile-sync/spec.md), [CLI entry point, profile resolution, and shell completion](../cli-entry-profile-resolution-completion/spec.md).
