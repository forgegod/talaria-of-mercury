# Talaria — Winged Sandals for the Hermes Agent

<p align="center"><img src="assets/logo.svg" alt="Talaria"></p>

> *"With these sandals I shall bear the words of Olympus across wind and wave, swift as thought, returning before the laurel of my errand has time to wither."*

**Talaria** is a maintenance CLI for [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). It runs alongside the agent's existing `state.db` and `logs/`, verifying mitigations, surfacing regressions, and giving the operator a single foot-stool from which to oversee every Hermes profile running on the host.

The name is deliberate. In the [Greek mythos](https://en.wikipedia.org/wiki/Talaria), the *talaria* were the winged sandals of Hermes: golden, swift, granted by Zeus, and worn by the messenger god to travel between mortal and divine realms. Hermes Agent carries his namesake's errands — tool calls, model swaps, subagent dispatch. Talaria carries its operator's errands: profile resolution, signal verification, and the verdict that tells you whether the agent is still flying cleanly or has begun to drag a wing.

## Why

Hermes Agent stores session telemetry in a per-profile SQLite database and rotates `agent.log` / `errors.log` next to it. Talaria provides one installable CLI for profile-aware inspection, catalog refreshes, and controlled profile configuration sync, with:

- **Profile-aware path resolution** — explicit flags win, then `$HERMES_PROFILE`, then `~/.hermes/active_profile`, then `default`.
- **Structured JSON output** for cron, dashboards, and other agents.
- **Reusable feature layout** — adding a new maintenance check is a new module under `talaria.hermos` or a sync phase under `talaria.sync`, not a top-level script.
- **Zero network dependencies for inspection features.** Read-only features (e.g. `moa-truncation`) never talk to the network. Network-aware maintenance commands (`refresh-catalog`, `install-skills-recursive`) fetch external catalog or skill metadata before writing profile artefacts.
- **Write-bearing `talaria sync`.** The inspection features never modify `state.db` or logs; the `sync` feature group is the deliberate carve-out that copies profile *configuration* artefacts (config.yaml, SOUL.md, skills/, .env, context cache) between profiles.

## Features at a glance

| Command                          | Reads                                | Purpose                                                      |
|----------------------------------|--------------------------------------|--------------------------------------------------------------|
| `talaria paths`                  | —                                    | Print the resolved profile + paths Talaria would inspect.    |
| `talaria hermes moa-truncation`  | `state.db`, `agent.log`, `errors.log`| Verify the MoA truncation mitigation (Signal A + Signal B).   |
| `talaria hermes refresh-catalog` | selected gateway + XDG cache         | Refresh and reshape a gateway-backed model manifest.         |
| `talaria hermes install-skills-recursive` | GitHub tree + profile config | Install every child skill under `.../*` and set enable policy. |
| `talaria sync`                   | two profiles (or file paths)         | Copy config.yaml, SOUL.md, skills/, .env, context cache between profiles. |

Each feature has its own dedicated section below with usage, flags, output schema, and exit codes.

## Install

```bash
# from a clone of this repo
pip install -e ".[dev]"

# or, once published
pip install talaria
```

The `[dev]` extra pulls in `pytest` and `pytest-cov` for the test suite.

## Conventions

These rules apply to every `talaria` subcommand. Per-feature deviations are called out in the feature section.

### Path resolution

Talaria resolves the `(profile, state.db, logs)` triple in this priority order (highest wins):

1. `--state-db` / `--log-dir` on the command line (explicit override)
2. `--profile <name>` (resolved relative to `~/.hermes/profiles/<name>/`)
3. `$HERMES_PROFILE`
4. `~/.hermes/active_profile` (set by `hermes profile use`)
5. The fallback profile `default`

`state.db` may legitimately be absent in a clean install — Talaria reports that as a tool error, not a crash.

Talaria does **not** consume `HERMES_HOME` for resolution. That env var is set by a running Hermes session and would mislead a script invoked from cron or another shell. Resolution always starts from `~/.hermes/`.

### Exit codes

Every `talaria` subcommand follows this contract:

| Code | Meaning                                                                          |
|------|----------------------------------------------------------------------------------|
| `0`  | Clean — no signals fired, refresh succeeded, or feature is N/A.                  |
| `1`  | At least one signal fired; printed guidance follows. (Alert-emitting features.)  |
| `2`  | Tool error — bad flag, missing/unreadable input, network failure, write failure. |

Feature sections note any exceptions (e.g. `refresh-catalog` has no `1` exit because it has no alert condition).

### Output

Every data-producing subcommand accepts `--json` and emits a stable JSON document to stdout suitable for cron, dashboards, and other agents. Human-readable output is the default for terminals.

### Debug helpers

Every feature with path resolution accepts `--show-resolution`: it prints what Talaria would inspect and exits 0 without running the feature.

## Usage

```bash
# Inspect which profile + paths Talaria would use
talaria paths
talaria paths --json

# Verify the MoA truncation mitigation against the active profile
talaria hermes moa-truncation

# Refresh the selected gateway model catalog (kilocode by default; skip-if-fresh)
talaria hermes refresh-catalog --gateway kilocode

# Force a refresh and dump JSON
talaria hermes refresh-catalog --force --json

# Install every child skill below a skills.sh repo path; disabled by default
talaria hermes install-skills-recursive 'skills-sh/addyosmani/agent-skills/*'

# Enable only selected recursively installed skills
talaria hermes install-skills-recursive 'skills-sh/addyosmani/agent-skills/*' \
  --enable api-and-interface-design context-engineering

# Show what either feature would inspect without running it
talaria hermes moa-truncation --show-resolution
talaria hermes refresh-catalog --show-resolution
```

Per-feature usage patterns and flags are documented below.

## Feature: `talaria hermes moa-truncation`

Verifies the MoA truncation mitigation by running two signals against the resolved profile's `state.db` and `logs/`. Both signals must pass for the command to exit 0.

### Usage

```bash
# auto-detect the active Hermes profile and run both signals
talaria hermes moa-truncation

# inspect a specific profile
talaria hermes moa-truncation --profile hermes-vc

# cron-friendly: explicit paths, JSON output, longer look-back
talaria hermes moa-truncation \
  --state-db /var/lib/hermes/state.db \
  --log-dir  /var/log/hermes \
  --json --days 7

# debug: which profile and paths did Talaria resolve?
talaria hermes moa-truncation --show-resolution
```

### Flags

| Flag                | Default          | Effect                                                          |
|---------------------|------------------|-----------------------------------------------------------------|
| `--days N`          | `2`              | Sliding look-back window in days (UTC).                         |
| `--since YYYY-MM-DD`| —                | Absolute start date; overrides `--days`.                        |
| `--profile NAME`    | from env/file    | Profile to inspect.                                             |
| `--state-db PATH`   | resolved         | Override the `state.db` path.                                   |
| `--log-dir PATH`    | resolved         | Override the `logs/` directory.                                 |
| `--json`            | off              | Emit JSON instead of human-readable output.                     |
| `--show-resolution` | off              | Print resolved paths and exit 0 without running the signals.   |

### Output

Human mode prints the two-signal report followed by a `VERDICT:` line. JSON mode dumps the full report:

```json
{
  "profile": "default",
  "state_db": "/home/.../state.db",
  "log_dir":  "/home/.../logs",
  "window_start_utc": "2026-07-01T00:00:00+00:00",
  "signal_a_output_tokens":    { "ok": true, "flagged": [...], "sessions": [...] },
  "signal_b_log_truncations":  { "ok": true, "length_class_hits": 0, "matches": [...] },
  "fired": false
}
```

`fired: true` is the JSON consumer's signal to branch on the non-zero exit code.

### Signal A — session output_tokens trend

Queries `state.db` for the top 15 sessions in the look-back window by `output_tokens`. Flags any session whose `output_tokens` exceeds the alert threshold (default `64_000`, configurable in `talaria.hermos.moa_truncation.MOA_OUTPUT_TOKEN_ALERT`). A regression here usually means a MoA preset's `max_tokens` is still too high, or a long-running alias was left routed through MoA.

### Signal B — length-class truncation in logs

Scans `agent.log` and `errors.log` for the four length-truncation markers:

- `finish_reason='length'` and `finish_reason="length"` (Hermes runtime + providers)
- `Response truncated (finish_reason='length')`
- `hit max output tokens`

To avoid false positives from user-message INFO echoes, hits only count on lines whose severity prefix is `WARNING`/`ERROR`/`CRITICAL`. A separate `stream_drop_warnings` count surfaces mid-tool-call stream drops, which are not length events and are reported for visibility only.

### Look-back window

The default is `--days 2` (UTC, sliding). Override with `--days N` or pin an absolute start with `--since 2026-07-01`.

## Feature: `talaria hermes refresh-catalog`

Refreshes the selected gateway model catalog into that provider's Hermes manifest cache. Profile-agnostic — every Hermes profile reads the same provider cache (currently `$XDG_CACHE_HOME/kilocode_catalog.json` for `--gateway kilocode`).

### Usage

```bash
# refresh the Kilo Code gateway now (always fetches)
talaria hermes refresh-catalog --gateway kilocode --force

# idempotent: skip the fetch if the selected provider cache is younger than 6h
talaria hermes refresh-catalog --gateway kilocode

# custom destination + JSON report
talaria hermes refresh-catalog --dst /tmp/kilocode.json --json

# debug: which cache path and source URL would be used?
talaria hermes refresh-catalog --show-resolution
```

### Flags

| Flag                  | Default                                              | Effect                                                              |
|-----------------------|------------------------------------------------------|---------------------------------------------------------------------|
| `--gateway NAME`      | `kilocode`                                           | Gateway/provider catalog to fetch and write. Currently only `kilocode`. |
| `--dst PATH`          | gateway-specific XDG cache path                      | Destination manifest path.                                          |
| `--src-url URL`       | selected gateway endpoint                            | Catalog endpoint (advanced).                                        |
| `--max-age-seconds N` | `21600` (6h)                                         | Skip fetch when the cache is younger than this.                     |
| `--force`             | off                                                  | Refetch even when the cache is fresh.                               |
| `--profile NAME`      | from env/file                                        | Recorded in the report for debugging; does not affect the cache path.|
| `--json`              | off                                                  | Emit JSON instead of human-readable output.                         |
| `--show-resolution`   | off                                                  | Print the resolved cache path + source URL and exit 0.              |

### What it does

Three steps:

1. **Fetch** — GET the live catalog from the selected gateway into a temp file.
2. **Reshape** — convert the OpenAI-style `{data: [...]}` response into the Hermes manifest schema for that provider (`{providers: {kilocode: {models: [...]}}}` for `--gateway kilocode`), normalising per-token pricing to per-million-token values and marking zero-priced models as `is_free: true`.
3. **Atomic write** — write the manifest to the destination via a sibling temp file and `os.replace`, so a concurrent reader never sees a half-written JSON.

### Output

Human mode prints the cache path, source URL, HTTP status, model count, and a `VERDICT:` line. JSON mode dumps:

```json
{
  "ok": true,
  "skipped": false,
  "reason": "refreshed",
  "http_code": 200,
  "gateway": "kilocode",
  "provider_id": "kilocode",
  "credential_env": "KILOCODE_API_KEY",
  "cache_path": "/home/.../kilocode_catalog.json",
  "source_url": "https://api.kilo.ai/api/gateway/models",
  "model_count": 142,
  "manifest": { "version": 1, "providers": { "kilocode": { "models": [...] } } }
}
```

`reason` is `"refreshed"` on a full fetch, `"fresh"` when the cache was within the idempotency window and the fetch was skipped, or one of `"auth" | "network" | "parse" | "write"` on tool error.

### Exit codes

| Code | Meaning                                                                                |
|------|----------------------------------------------------------------------------------------|
| `0`  | Refreshed, or skipped because the cache is fresh.                                      |
| `2`  | Tool error — `reason` in the report disambiguates (`auth` / `network` / `parse` / `write`). |

The `fired` exit code (`1`) does not apply — there is no alert condition, only success vs. tool error.

### Idempotency and credentials

By default the feature skips the fetch when the cache is younger than `--max-age-seconds` (default `21600` / 6 hours). Use `--force` to refetch unconditionally.

The selected gateway's API key is read from its configured environment variable first, then from `~/.hermes/.env`. For `--gateway kilocode`, use `KILOCODE_API_KEY=...` or `export KILOCODE_API_KEY=...`. Missing credential is reported as `reason: "auth"` with exit code `2`.

## Feature: `talaria hermes install-skills-recursive`

Installs every Hermes skill below a wildcard GitHub-backed skill identifier. This is a Talaria wrapper around the Hermes CLI: Talaria expands `.../*`, invokes `hermes skills install` once per child skill, then updates the selected profile's `config.yaml` so recursive third-party installs are disabled by default.

### Usage

```bash
# Install all child skills and disable them by default
talaria hermes install-skills-recursive 'skills-sh/addyosmani/agent-skills/*'

# Enable all installed child skills immediately
talaria hermes install-skills-recursive 'skills-sh/addyosmani/agent-skills/*' --force-enable

# Enable only selected skills; all other installed children stay disabled
talaria hermes install-skills-recursive 'skills-sh/addyosmani/agent-skills/*' \
  --enable api-and-interface-design context-engineering

# Preview expansion and config policy without installing or writing config.yaml
talaria hermes install-skills-recursive 'skills-sh/addyosmani/agent-skills/*' --dry-run --json
```

### Flags

| Flag              | Default | Effect                                                                 |
|-------------------|---------|------------------------------------------------------------------------|
| `identifier`      | —       | Recursive identifier ending in `/*`, e.g. `skills-sh/owner/repo/path/*`. |
| `--profile`       | active  | Hermes profile to install into and whose `config.yaml` is updated.     |
| `--force`         | off     | Pass `--force` to each `hermes skills install` invocation.             |
| `--force-enable`  | off     | Enable every successfully installed child skill.                       |
| `--force-enalbe`  | off     | Typo-compatible alias for `--force-enable`.                            |
| `--enable SKILL...` | none  | Enable only matching skill names or identifiers; disable the rest.     |
| `--dry-run`       | off     | Expand and report policy without invoking Hermes or writing config.    |
| `--no-backup`     | off     | Skip `.bak` backup before updating `config.yaml`.                      |
| `--json`          | off     | Emit the structured report.                                            |
| `--show-resolution` | off   | Print expanded identifiers and target config path, then exit.          |

### Enable policy

After installation, Talaria writes `skills.disabled` in the selected profile config:

- default: every successfully installed child skill is disabled.
- `--force-enable`: every successfully installed child skill is enabled.
- `--enable A B`: only selected children are enabled; every other installed child is disabled.

### Exit codes

| Code | Meaning                                      |
|------|----------------------------------------------|
| `0`  | Expansion, installs, and config policy passed. |
| `2`  | Tool error — expansion, install, or write failed. |

## Feature: `talaria sync`

Copy Hermes profile artefacts (config.yaml, SOUL.md, skills/, .env,
context_length_cache.yaml) from one profile to another. **Sync is the
write-bearing feature group** — every other Talaria command is
read-only against the Hermes runtime. Sync never touches `state.db`
or rotates logs; it copies the *configuration* artefacts that
determine which profile a Hermes session runs under.

### Usage

```bash
# Sync every phase from default to hermes-vc (writes by default)
talaria sync default hermes-vc

# Preview without writing
talaria sync default hermes-vc --dry-run

# Copy only specific config.yaml paths
talaria sync default hermes-vc --only moa.max_tokens memory.provider

# Copy everything except mcp_servers and model (target keeps its own values)
talaria sync default hermes-vc -e mcp_servers model

# Sync only one phase
talaria sync default hermes-vc --skip-config --skip-env --skip-cache

# Sync a subset of skills
talaria sync default hermes-vc --sync-skills github/dev-git-commit-message

# Inject a Hermes SSE endpoint into the target's mcp_servers
talaria sync default hermes-vc --add-mcp-serve

# List the dot-notation paths in the source config
talaria sync default --list

# Use explicit file paths instead of profile names
talaria sync ~/.hermes/profiles/hermes-vc/config.yaml ~/.hermes/profiles/hermes-legal/config.yaml -e mcp_servers
```

### Flags

| Flag                  | Default | Effect                                                              |
|-----------------------|---------|---------------------------------------------------------------------|
| `source`              | —       | Source profile name (e.g. `default`) or path to `config.yaml`.      |
| `target`              | —       | Target profile name or path. Required unless `--list` is used.      |
| `--skip-config`       | off     | Skip the `config.yaml` phase.                                       |
| `--skip-soul`         | off     | Skip the `SOUL.md` phase.                                           |
| `--skip-skills`       | off     | Skip the `skills/` phase.                                           |
| `--skip-env`          | off     | Skip the `.env` phase.                                              |
| `--skip-cache`        | off     | Skip the `context_length_cache.yaml` phase.                         |
| `-e`, `--exclude`     | none    | Dot-notation paths to exclude from source. Target keeps its values. |
| `-o`, `--only`        | none    | Copy only these paths from source. Mutually exclusive with `-e`.    |
| `--sync-skills`       | all     | Limit skills sync to categories or `category/skill-name` paths.     |
| `--add-mcp-serve`     | off     | Add an `mcp_servers` entry to target connecting to a Hermes SSE endpoint. |
| `--mcp-serve-name`    | `hermes`| Name for the `mcp_servers` entry.                                   |
| `--mcp-serve-port`    | `9119`  | Port for the Hermes SSE endpoint.                                   |
| `--mcp-serve-host`    | `localhost` | Host for the Hermes SSE endpoint.                               |
| `--dry-run`           | off     | Preview changes without writing. **Apply by default.**              |
| `--no-backup`         | off     | Skip `.bak` backup before overwriting.                              |
| `--list`              | off     | List dot-notation paths in source `config.yaml` and exit.           |
| `--list-depth`        | `2`     | Depth for `--list`.                                                 |
| `--json`              | off     | Emit JSON report instead of human-readable output.                  |
| `-v`, `--verbose`     | off     | Show diffs, per-skill detail, and source/target banners.            |

### Sync phases

Each phase is independent and any subset runs together. The
default — no skip flags — runs every phase.

1. **`config.yaml`** — element-level merge with `--exclude` or `--only` filtering. The phase is a no-op when none of `--exclude`, `--only`, or `--add-mcp-serve` is set; sync is about *propagating deltas*, not replacing the target wholesale.
2. **`SOUL.md`** — straight copy with a `.bak` on the target when it differs.
3. **`skills/`** — byte-level tree comparison. New and differing skills are copied; matching skills are skipped. `--sync-skills` filters by category or `category/skill-name`.
4. **`.env`** — additive merge. New variables are appended to the target with a `# ── Synced from source profile ──` header. Existing target values are never overwritten (target is the running profile's environment; clobbering it would break a working setup).
5. **`context_length_cache.yaml`** — source-wins merge (factual model measurements presumed more recent). Target-only entries are preserved.

The `--add-mcp-serve` flag is independent of the config phase's
filter mode. It injects an `mcp_servers.<name>` entry pointing at
the running Hermes dashboard's SSE endpoint and is idempotent —
re-running with the same host/port reports "already up to date".

### Output

Human mode prints the source/target banner, one section per phase
with status (`ok` / `updated` / `new` / `skipped`), and a final
summary line listing what was written. `--verbose` adds YAML diffs,
per-skill detail, and the dry-run hint banner.

JSON mode dumps the full structured report:

```json
{
  "source": "default",
  "target": "hermes-vc",
  "apply": true,
  "ok": true,
  "any_writes": true,
  "config": {
    "phase": "config", "status": "in_sync", "write_confirmed": false,
    "target_path": null, "mode": "identity",
    "exclude_paths": [], "only_paths": [],
    "mcp_serve_name": null, "mcp_serve_url": null,
    "diff_lines": [], "backup_path": null
  },
  "soul": { "phase": "soul", "status": "new", "write_confirmed": true, "...": "..." },
  "skills": { "phase": "skills", "status": "updated", "copied": 0, "new_count": 3, "skipped": 0, "...": "..." },
  "env": { "phase": "env", "status": "in_sync", "new_vars": [], "preserved_vars": [], "...": "..." },
  "context_cache": { "phase": "context_cache", "status": "in_sync", "new_keys": [], "updated_keys": [], "...": "..." },
  "mcp_serve": null,
  "error": null
}
```

### Exit codes

| Code | Meaning                                                                          |
|------|----------------------------------------------------------------------------------|
| `0`  | Success — no errors. Phases may have been skipped or no-op; that is normal.      |
| `2`  | Tool error — bad flag, source/target not found, YAML parse failure, write failure, source == target. |

The `1` (alert fired) exit is unused. Sync emits no alert
conditions: every phase either succeeds, no-ops, or fails with a
tool error.

### Apply-by-default

Talaria's sync is meant for routine profile upkeep (propagating fixes
from a base profile to working profiles, syncing shared `.env` keys,
copying new skills into place). The operator runs the command,
inspects the on-screen summary, and trusts the write. `--dry-run` is
the explicit opt-out for the preview path.

### Sync vs `cp`

Sync is **not** a substitute for `cp` when the operator wants to
nuke a target's `config.yaml`. Sync's config phase is additive —
it copies paths the operator selects (`--exclude` keeps target
values, `--only` copies just the listed paths) and otherwise
leaves the target alone. To replace a file wholesale, use `cp`
or edit the file directly.

## Configuration

Talaria reads no configuration files itself. Every input is a CLI flag or environment variable.

| Var              | Effect                                                                  |
|------------------|-------------------------------------------------------------------------|
| `HERMES_PROFILE` | Profile name to inspect when `--profile` is omitted.                    |
| `XDG_CACHE_HOME` | Parent directory for the default catalog cache path.                    |
| `KILOCODE_API_KEY` | Kilo Code gateway API key used by `refresh-catalog --gateway kilocode` (also read from `~/.hermes/.env`). |

## Adding a new feature

Talaria has two feature groups. Inspection features live under
`talaria/hermos/` (read-only against `state.db` and `logs/`). Sync
phases live under `talaria/sync/` (the write-bearing carve-out;
copies profile artefacts between profiles).

Inspection feature (canonical shape — `moa_truncation`):

1. Add `talaria/hermos/<feature>.py` exposing `run(paths, **opts)` and `render_human(report)`.
2. Wire its argparse subparser into `talaria.cli.build_parser`.
3. Add tests under `tests/test_<feature>.py` using the shared `fake_hermes_root` fixture.

Sync phase (see `talaria/sync/config.py` for the canonical shape):

1. Add `talaria/sync/<phase>.py` exposing `sync_<phase>(source, target, *, apply, **opts)` returning a `PhaseResult` subclass from `talaria.sync.result`.
2. Wire it into `talaria.sync.run.run_sync` (or document why it needs a separate CLI flag).
3. Add an argparse flag to `talaria.cli.build_parser` if the phase needs CLI-level control.
4. Add tests under `tests/test_sync.py`.

## Development

```bash
# install + tests
pip install -e ".[dev]"
pytest

# install + tests against a real Hermes install
talaria paths   # confirm path resolution is sane
talaria hermes moa-truncation --show-resolution
talaria hermes refresh-catalog --show-resolution
talaria sync default hermes-vc --dry-run   # preview a sync without writing
```

The test suite uses an in-memory SQLite `sessions` table and tmpdir logs — no live Hermes install is required. Network-bound tests stub `urllib.request.urlopen`; no real Kilo Code calls happen during `pytest`.

## License

MIT — see `LICENSE`.

## References

- [Talaria — Wikipedia](https://en.wikipedia.org/wiki/Talaria) — mythology behind the name.
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — the agent this CLI maintains.
- [hermes-agent.nousresearch.com](https://hermes-agent.nousresearch.com) — the visual reference for the logo's puristic flat-mark style.
## Brand assets

- `assets/logo.svg` + `logo-256.png`, `logo-512.png`, `logo-1024.png` — primary lock-up.
- `assets/logo-mark.svg` + `logo-mark-128.png`, `logo-mark-256.png` — square mark only.
- `assets/logo-inverse.svg` — white-on-transparent for dark backgrounds.
- `assets/build_logo.py` — regenerates the SVG sources from the `ASCII_GLYPH` constant (single source of truth; edit the ASCII to redesign the silhouette).