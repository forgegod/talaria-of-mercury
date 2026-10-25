# CAP-007 — Single-profile configuration helpers

**Status:** implemented
**Primary surface:** none

## Behaviour

- `talaria config apply-auxiliary` reads one profile's `config.yaml` and derives
  `model.aliases._<usecase>` entries from each concrete
  `auxiliary.<usecase>.model` pin in the same file. It is single-profile: there
  is no source/target split.
- Usecases whose `model` is missing, empty, or one of `auto`, `inherit`,
  `default`, `use_default`, `use_model`, or `use_provider` are skipped. Existing
  operator-defined `model.aliases` keys outside the derived set are preserved.
  Re-running with unchanged pins is a no-op.
- The report distinguishes `added`, `updated`, `kept`, and `preserved` aliases.
  Writes go through the atomic backup writer; `--dry-run` writes nothing and
  `--no-backup` suppresses the `.bak` sidecar.
- `talaria config sync-env` refreshes a single profile's `.env` values from the
  live process environment. For each active `KEY=…` line already in the file,
  the value is replaced with the environment value. Keys absent from the file
  are never added by default.
- Four repeatable, orthogonal key operations extend that baseline:
  `--add-key KEY` appends a named key that is absent from the file and present in
  the environment with a non-empty value; `--skip-key KEY` keeps a key's file
  value unchanged; `--disable-key KEY` turns `KEY=value` into `#KEY=value` and
  drops an `export` prefix; `--enable-key KEY` restores `#KEY=value` verbatim
  and excludes that key from the env refresh on the same run.
- Empty environment values never overwrite the file value. Comments, blank
  lines, and unrelated lines are preserved. Missing files are a no-op unless
  `--add-key` supplies an addable key, in which case the file is created.
- Both helpers apply by default; `--dry-run` previews. Both write through the
  atomic backup writer with a default `.bak`. They are silent unless `-v`,
  `--json`, or `--show-resolution` is used. Successful runs exit 0; the dispatcher
  maps reported `ok: false` to 2, but uncaught I/O/YAML exceptions can exit 1.

## Implementation

- `src/talaria/hermes/auxiliary.py` — sentinel set, alias derivation, merge,
  atomic write, renderer, `show_resolution`.
- `src/talaria/hermes/sync_env.py` — line scanner, refresh, add/skip/disable/
  enable resolution, payload builder, atomic write, renderer,
  `show_resolution`.
- `src/talaria/sync/writer.py` — atomic write plus optional backup.
- `src/talaria/cli/__init__.py` — `cmd_config_apply_auxiliary`,
  `cmd_config_sync_env`.

## Rules and boundaries

- Scope is one profile. `apply-auxiliary` never reads another profile's config;
  `sync-env` never copies values from another profile.
- `sync-env` treats the environment as authoritative for values but the file as
  authoritative for scope: no key is added without `--add-key`.
- `sync-env --json` reports `updated[].old` and `updated[].new` values, so that
  channel can expose secrets. The human renderer prints key names only, and
  `--show-resolution` lists key names without values. Do not treat every output
  as redacted.
- `apply-auxiliary` does not validate that the pinned model exists; it copies
  the operator's string. A non-dict `model.aliases` is left untouched rather than
  clobbered.
- Neither helper touches `state.db` or `logs/`.

## Verification

- `tests/test_auxiliary.py` — `TestApplyAuxiliary` (injection, sentinel skipping,
  alias preservation, no-op, idempotency, dry-run, profile and explicit-path
  resolution, resolution output) and `TestAuxiliaryCLI` (apply/JSON, dry-run,
  `--show-resolution`).
- `tests/test_sync_env.py` — `TestSyncEnv` (refresh, no-add default, export
  prefix, comments, empty env, absent keys, dry-run, backup),
  `TestSyncEnvAddKeys`, `TestSyncEnvSkipKeys`, `TestSyncEnvDisableKeys`,
  `TestSyncEnvEnableKeys`, `TestSyncEnvCombinedOperations`,
  `TestSyncEnvShowResolutionNewOps`, `TestSyncEnvCLI`, `TestSyncEnvAddKeysCLI`,
  and `TestSyncEnvNewOpsCLI`.

## Related contracts

- [Architecture](../../architecture.md)
- [Repository contract](../../../AGENTS.md)
