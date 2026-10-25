# CAP-006 — Profile sync

**Status:** implemented
**Primary surface:** none

## Behaviour

- `talaria config sync <source> <target>` copies profile artefacts from a source
  to a target. A profile spec may be `default`/`main`/`base`, a named profile
  under `~/.hermes/profiles/<name>/`, or a literal `config.yaml` path. The target
  is required; a missing profile reports the available profiles and exits 2.
- `--list` prints the dot-notation paths of the source `config.yaml`
  (`--list-depth`, default 2) and exits without needing a target.
- Six phases run by default: `config.yaml`, `SOUL.md`, `skills/`, `.env`,
  `context_length_cache.yaml`, and `auth.json`. Each can be skipped with
  `--skip-config`, `--skip-soul`, `--skip-skills`, `--skip-env`,
  `--skip-cache`, or `--skip-auth`.
- Plain config sync replaces target configuration with source. `--exclude` keeps target values for
  listed paths; `--only` copies only listed paths; the two are mutually
  exclusive. Filtered or MCP-injection runs are guarded by a source-newer-than-target
  timestamp check unless `--force-config` is passed; plain replacement is not.
  `--add-mcp-serve` injects or updates an
  `mcp_servers.<name>` SSE entry (`--mcp-serve-name`, `--mcp-serve-port`,
  `--mcp-serve-host`).
- The skills phase copies skill directories and supports
  `--sync-skills [FILTER …]` by category or `category/skill-name`.
- The `.env` phase is additive: missing source variables are appended to the
  target and existing target values win on conflict.
- The context-cache phase appends missing keys and lets the source win on
  conflict.
- The auth phase scans every profile's `auth.json`, selects the newest token per
  provider (`last_refresh`, then `obtained_at`), and writes it into the target.
  Only the `providers` block is merged; other target fields are preserved.
- Sync applies by default; `--dry-run` previews every phase. Backups are written
  before overwriting unless `--no-backup`. A same-profile source/target pair and
  the `--exclude`/`--only` combination are rejected before any phase runs.
- Exit code is 0 on success and 2 for resolution, validation, or phase errors.
  The default run is silent; `-v/--verbose` prints the human report and
  `--json` emits the structured report.

## Implementation

- `src/talaria/cli/__init__.py` — `cmd_sync`, `_build_sync_options`,
  `sync_list_config_paths`.
- `src/talaria/sync/run.py` — `SyncOptions`, `run_sync`, phase selection.
- `src/talaria/sync/paths.py` — `SyncProfile`, `resolve_profile`,
  `list_profiles`, `same_profile`.
- `src/talaria/sync/config.py` — replacement, filtered merge, MCP injection.
- `src/talaria/sync/soul.py` — soul copy.
- `src/talaria/sync/skills.py` — skills tree copy.
- `src/talaria/sync/env.py` — additive environment merge.
- `src/talaria/sync/context_cache.py` — source-wins cache merge.
- `src/talaria/sync/auth_tokens.py` — newest provider-token selection.
- `src/talaria/sync/mcp_serve.py` — target-only MCP configuration helper.
- `src/talaria/sync/writer.py` — atomic file replacement and backup.
- `src/talaria/sync/yaml_io.py` — YAML loading, dumping, validation.
- `src/talaria/sync/render.py` — human and JSON rendering.
- `src/talaria/sync/result.py` — structured report types.

## Rules and boundaries

- This is the write-bearing profile-to-profile command. It writes by default;
  `--dry-run` is the opt-out, not `--apply`.
- Provider OAuth tokens in `auth.json` are synced by default; the target's
  credential pool and other non-provider fields remain unchanged. Use
  `--skip-auth` to omit this phase. It reads every
  profile's `auth.json` to find the newest token, not just source and target.
- Existing target `.env` values win; sync never overwrites them from source. The
  reverse direction (live environment into one profile's `.env`) is CAP-007.
- For filtered/MCP-injection config sync, a source not newer than the target
  is skipped unless `--force-config`; plain replacement ignores timestamps.
  Content-equivalent files are skipped where supported by each phase.
- `--no-backup` removes the recovery copy for overwritten files.
- Sync never reads or writes `state.db` or `logs/`.

## Verification

- `tests/test_sync.py` — `TestResolveProfile` (default aliases, named profile,
  file path, missing-profile message, `list_profiles`), `TestDotPath`,
  `TestYamlIO`, `TestConfigPhase` (exclude/only, timestamp guard, force,
  mcp-serve), `TestSoulPhase`, `TestSkillsPhase`, `TestEnvPhase`,
  `TestContextCachePhase`, `TestAuthTokensPhase`, `TestRunSync`, and
  `TestSyncCLI` (dry-run, default apply write, silent default, verbose report,
  `--list`, exclude).

## Related contracts

- [Architecture](../../architecture.md)
- [Repository contract](../../../AGENTS.md)
