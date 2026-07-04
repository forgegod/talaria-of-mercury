# Talaria — Winged Sandals for the Hermes Agent

<p align="center"><img src="assets/logo.svg" alt="Talaria"></p>

> *"With these sandals I shall bear the words of Olympus across wind and wave, swift as thought, returning before the laurel of my errand has time to wither."*

**Talaria** is a maintenance CLI for [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). It verifies mitigations, surfaces regressions, manages skill lifecycles, and keeps profiles in sync — giving the operator a single command for everything the agent itself doesn't cover.

The name is deliberate. In the [Greek mythos](https://en.wikipedia.org/wiki/Talaria), the *talaria* were the winged sandals of Hermes: golden, swift, granted by Zeus, and worn by the messenger god to travel between mortal and divine realms. Hermes Agent carries his namesake's errands — tool calls, model swaps, subagent dispatch. Talaria carries its operator's errands: profile resolution, signal verification, and the verdict that tells you whether the agent is still flying cleanly or has begun to drag a wing.

## Highlights

Talaria gives Hermes operators a single installable CLI for everything the agent itself doesn't cover — profile-aware inspection, model catalog refreshes, skill lifecycle management, and controlled configuration sync across profiles:

- 🩺 **Verify agent health** — check for MoA truncation regressions and repair stale context-length caches.
- 🧩 **Manage skills at scale** — recursively install, categorize, and uninstall third-party skill collections from GitHub or skills.sh, with collision detection and fuzzy similarity matching to prevent silent overwrites.
- 🔄 **Keep profiles in sync** — copy config, SOUL.md, skills, `.env`, and context cache between profiles; refresh `.env` values from the live environment; derive model aliases from auxiliary pins.
- 🗂️ **Refresh model catalogs** — fetch and reshape gateway-backed model manifests into Hermes' provider cache.
- 🛑 **Stop runaway backends** — detect and gracefully terminate Hermes dashboard/serve processes by port (not cmdline pattern).

Every command follows the same conventions: profile-aware path resolution, structured JSON output for cron and dashboards, `--dry-run` for safe previews, and `--show-resolution` for path debugging.

## Features at a glance

| Command | Group | Purpose |
|---------|-------|---------|
| `talaria paths` | — | Print the resolved profile + paths Talaria would inspect. |
| `talaria completion <shell>` | — | Print a bash or zsh shell completion script. |
| `talaria hermes moa-truncation` | inspection | Verify the MoA truncation mitigation (Signal A + Signal B). |
| `talaria hermes refresh-catalog` | maintenance | Refresh and reshape a gateway-backed model manifest. |
| `talaria hermes fix-context-cache` | maintenance | Repair curated known-bad entries in a profile's context cache. |
| `talaria hermes serve-stop` | maintenance | Detect and stop the Hermes dashboard/serve backend by port. |
| `talaria skills install` | skills | Install skill(s) under an identifier (recursive if `/*`) with category and enable policy. |
| `talaria skills uninstall` | skills | Uninstall skill(s) under an identifier and clean up `skills.disabled`. |
| `talaria skills create-category` | skills | Create a skill category directory with an optional description. |
| `talaria config sync` | config | Copy config.yaml, SOUL.md, skills/, .env, context cache between profiles. |
| `talaria config apply-auxiliary` | config | Derive `model.aliases._<usecase>` from a profile's `auxiliary` block. |
| `talaria config sync-env` | config | Refresh a profile's `.env` values from the live environment. |

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

| Code | Meaning |
|------|---------|
| `0`  | Clean — no signals fired, refresh succeeded, or feature is N/A. |
| `1`  | At least one signal fired; printed guidance follows. (Alert-emitting features.) |
| `2`  | Tool error — bad flag, missing/unreadable input, network failure, write failure. |

### Output

Every data-producing subcommand accepts `--json` and emits a stable JSON document to stdout suitable for cron, dashboards, and other agents. Human-readable output is the default for terminals.

### Debug helpers

Every feature with path resolution accepts `--show-resolution`: it prints what Talaria would inspect and exits 0 without running the feature.

## Usage

```bash
# Inspect which profile + paths Talaria would use
talaria paths

# Verify the MoA truncation mitigation against the active profile
talaria hermes moa-truncation

# Refresh the Kilo Code gateway model catalog
talaria hermes refresh-catalog --gateway kilocode

# Install every child skill below a skills.sh repo path into a category
talaria skills create-category software-development \
  --description "Software engineering workflows and tools."
talaria skills install 'skills-sh/addyosmani/agent-skills/*' \
  --category software-development \
  --enable api-and-interface-design context-engineering

# Uninstall every child skill below a skills.sh repo path
talaria skills uninstall 'skills-sh/addyosmani/agent-skills/*'

# Sync config from default to a working profile
talaria config sync default hermes-vc

# Refresh .env values from the live environment
talaria config sync-env --profile hermes-vc

# Stop a runaway Hermes dashboard
talaria hermes serve-stop

# Shell completion
eval "$(talaria completion zsh)"
```

## Feature: `talaria hermes moa-truncation`

Verifies the MoA truncation mitigation by running two signals against the resolved profile's `state.db` and `logs/`. Both signals must pass for the command to exit 0.

### Usage

```bash
talaria hermes moa-truncation
talaria hermes moa-truncation --profile hermes-vc
talaria hermes moa-truncation --state-db /var/lib/hermes/state.db --log-dir /var/log/hermes --json --days 7
```

### Flags

| Flag | Default | Effect |
|------|---------|--------|
| `--days N` | `2` | Sliding look-back window in days (UTC). |
| `--since YYYY-MM-DD` | — | Absolute start date; overrides `--days`. |
| `--profile NAME` | from env/file | Profile to inspect. |
| `--state-db PATH` | resolved | Override the `state.db` path. |
| `--log-dir PATH` | resolved | Override the `logs/` directory. |
| `--json` | off | Emit JSON instead of human-readable output. |
| `--show-resolution` | off | Print resolved paths and exit 0 without running the signals. |

### Signals

**Signal A** — queries `state.db` for sessions exceeding the output-token alert threshold (default `64_000`). A regression here usually means a MoA preset's `max_tokens` is still too high.

**Signal B** — scans `agent.log` and `errors.log` for length-truncation markers (`finish_reason='length'`, `Response truncated`, `hit max output tokens`). Only counts `WARNING`/`ERROR`/`CRITICAL` lines to avoid false positives from INFO echoes.

`fired: true` in JSON output is the consumer's signal to branch on the non-zero exit code.

## Feature: `talaria hermes refresh-catalog`

Refreshes the selected gateway model catalog into that provider's Hermes manifest cache. Profile-agnostic — every Hermes profile reads the same provider cache.

### Usage

```bash
# idempotent: skip the fetch if the selected provider cache is younger than 6h
talaria hermes refresh-catalog --gateway kilocode

# force a refresh
talaria hermes refresh-catalog --gateway kilocode --force

# custom destination + JSON report
talaria hermes refresh-catalog --dst /tmp/kilocode.json --json
```

### Flags

| Flag | Default | Effect |
|------|---------|--------|
| `--gateway NAME` | `kilocode` | Gateway catalog to fetch (currently only `kilocode`). |
| `--dst PATH` | gateway-specific XDG cache path | Destination manifest path. |
| `--src-url URL` | selected gateway endpoint | Catalog endpoint (advanced). |
| `--max-age-seconds N` | `21600` (6h) | Skip fetch when the cache is younger than this. |
| `--force` | off | Refetch even when the cache is fresh. |
| `--json` | off | Emit JSON instead of human-readable output. |
| `--show-resolution` | off | Print the resolved cache path + source URL and exit 0. |

Three steps: fetch the live catalog, reshape into the Hermes manifest schema (normalising pricing to per-million-token values), and atomic-write to the destination via a sibling temp file + `os.replace`.

Exit code `2` with `reason: "auth" | "network" | "parse" | "write"` disambiguates failure modes. No `1` exit — there is no alert condition, only success vs. tool error.

## Feature: `talaria hermes fix-context-cache`

Repairs curated known-bad entries in a profile's `context_length_cache.yaml` — model context windows that Hermes has been known to cache incorrectly. Uses Talaria's curated fix table and preserves unrelated cache entries.

### Usage

```bash
talaria hermes fix-context-cache
talaria hermes fix-context-cache --profile hermes-vc
talaria hermes fix-context-cache --dry-run
```

### Flags

| Flag | Default | Effect |
|------|---------|--------|
| `--profile NAME` | active | Profile whose context cache should be repaired. |
| `--cache-path PATH` | resolved | Explicit cache path (overrides `--profile`). |
| `--only-existing` | off | Only update existing bad entries; do not insert missing known-fix keys. |
| `--dry-run` | off | Preview repairs without writing. |
| `--no-backup` | off | Skip `.bak` backup before overwriting. |
| `--json` | off | Emit JSON instead of human-readable output. |
| `--show-resolution` | off | Print the resolved cache path and known-fix table, then exit. |

## Feature: `talaria hermes serve-stop`

Detects and gracefully stops the Hermes dashboard/serve backend listening on a TCP port (default 9119). Detection is port-based via `psutil.net_connections` → PID, then SIGTERM/poll/SIGKILL — it finds backends that `hermes serve --stop` misses when launched with a global flag between module and subcommand (e.g. the Hermes Desktop app's `-p default dashboard` launch).

### Usage

```bash
talaria hermes serve-stop
talaria hermes serve-stop --port 9119
talaria hermes serve-stop --dry-run   # detect and report PIDs without sending any signal
```

### Flags

| Flag | Default | Effect |
|------|---------|--------|
| `--port N` | `9119` | Port the Hermes backend is listening on. |
| `--profile NAME` | active | Recorded in the report for debugging; does not affect detection. |
| `--dry-run` | off | Detect and report the backend PID(s) without sending any signal. |
| `--json` | off | Emit JSON instead of human-readable output. |
| `--show-resolution` | off | Print the port and detected PID(s), then exit. |

Report `reason`: `stopped` | `none` | `detected` (dry-run) | `partial` (some PIDs survived SIGKILL).

## Feature: `talaria skills install`

Installs Hermes skills below a skill identifier. When the identifier ends in `/*`, Talaria expands it into its child skills (scanning the GitHub repository tree for `SKILL.md` files), invokes `hermes skills install` once per child skill, then updates the selected profile's `config.yaml` so third-party recursive installs are disabled by default.

This is a Talaria wrapper around the Hermes CLI: Talaria owns the expansion, category routing, similarity checking, and `skills.disabled` policy; Hermes owns the actual install.

### Usage

```bash
# Install all child skills, disabled by default
talaria skills install 'skills-sh/addyosmani/agent-skills/*'

# Install into a category directory
talaria skills install 'skills-sh/addyosmani/agent-skills/*' \
  --category software-development

# Enable only selected skills
talaria skills install 'skills-sh/addyosmani/agent-skills/*' \
  --enable api-and-interface-design context-engineering

# Replace a similar already-installed skill (>=65% frontmatter match)
talaria skills install 'new-owner/arxiv' --replace-similar-skill

# Preview without installing
talaria skills install 'skills-sh/addyosmani/agent-skills/*' --dry-run --json
```

### Flags

| Flag | Default | Effect |
|------|---------|--------|
| `identifier` | — | Skill identifier; trailing `/*` installs every child skill. |
| `--profile` | active | Hermes profile to install into and whose `config.yaml` is updated. |
| `--force` | off | Pass `--force` to each `hermes skills install` invocation. |
| `--force-enable` | off | Enable every successfully installed child skill. |
| `--enable SKILL...` | none | Enable only matching skill names or identifiers; disable the rest. |
| `--category NAME` | none | Category directory forwarded to `hermes skills install --category`. Installs into `skills/<category>/<name>/`. Lowercase letters, digits, hyphens, underscores, slashes (e.g. `software-development`, `mlops/training`). |
| `--replace-similar-skill` | off | When a skill name already exists and frontmatter (name + description) is >=65% similar (`difflib.SequenceMatcher`), uninstall the existing skill before installing the new one. Without this flag, similar skills are reported as hints only. |
| `--dry-run` | off | Expand and report policy without invoking Hermes or writing config. |
| `--no-backup` | off | Skip `.bak` backup before updating `config.yaml`. |
| `--json` | off | Emit the structured report. |
| `--show-resolution` | off | Print expanded identifiers and target config path, then exit. |
| `-v`, `--verbose` | off | Stream per-skill progress to stderr. |

### Enable policy

After installation, Talaria writes `skills.disabled` in the selected profile config:

- default: every successfully installed child skill is disabled.
- `--force-enable`: every successfully installed child skill is enabled.
- `--enable A B`: only selected children are enabled; every other installed child is disabled.

### Name collision detection

When a skill name already exists in the profile's `lock.json`, Talaria:

1. Reads the installed skill's `SKILL.md` frontmatter from disk.
2. Fetches the incoming skill's frontmatter from GitHub.
3. Compares `name + description` via `difflib.SequenceMatcher` (stdlib).
4. If the ratio >= 65%: reports `SIMILAR` and suggests `--replace-similar-skill`.
5. With `--replace-similar-skill`: uninstalls the old skill, then installs the new one.

Network calls for similarity fetches only happen when the skill name already exists — non-colliding installs are zero-overhead.

The report includes `name_collisions`, `similarity_assessments`, and `replaced_skills` fields in JSON output.

## Feature: `talaria skills uninstall`

Removes Hermes skills installed below a skill identifier. Mirrors `skills install`: expands the identifier (recursive when it ends in `/*`), invokes `hermes skills uninstall` for each child skill *name* (unlike install, uninstall takes a name, not an identifier), then removes the uninstalled names from `skills.disabled`.

### Usage

```bash
talaria skills uninstall 'skills-sh/addyosmani/agent-skills/*'
talaria skills uninstall 'skills-sh/addyosmani/agent-skills/*' --dry-run --json
```

### Flags

| Flag | Default | Effect |
|------|---------|--------|
| `identifier` | — | Skill identifier; trailing `/*` uninstalls every child skill. |
| `--profile` | active | Hermes profile to uninstall from and whose `config.yaml` is updated. |
| `--dry-run` | off | Expand and report cleanup without invoking Hermes or writing config. |
| `--no-backup` | off | Skip `.bak` backup before updating `config.yaml`. |
| `--json` | off | Emit the structured report. |
| `--show-resolution` | off | Print expanded identifiers and target config path, then exit. |
| `-v`, `--verbose` | off | Stream per-skill progress to stderr. |

Partial failures: successfully uninstalled skills are still cleaned up from `skills.disabled`, but the command exits `2` if any uninstall fails.

## Feature: `talaria skills create-category`

Creates a skill category directory under the profile's `skills/` tree so skills can be installed into it with `talaria skills install --category <name>`. Optionally writes a `DESCRIPTION.md` whose frontmatter `description:` is shown in the Hermes system prompt after the category name.

### Usage

```bash
# Create a category with a description
talaria skills create-category software-development \
  --description "Software engineering workflows and tools."

# Create a nested category
talaria skills create-category mlops/training --description "Model training tools."

# Preview the resolved paths
talaria skills create-category preview --dry-run
```

### Flags

| Flag | Default | Effect |
|------|---------|--------|
| `category` | — | Category directory name (e.g. `software-development`, `mlops/training`). |
| `--description TEXT` | none | Human-readable description written to `DESCRIPTION.md` frontmatter. Shown in the Hermes system prompt. |
| `--profile` | active | Hermes profile whose `skills/` tree to create the category in. |
| `--dry-run` | off | Preview the resolved paths without creating anything. |
| `--no-backup` | off | Skip `.bak` backup when overwriting an existing `DESCRIPTION.md`. |
| `--json` | off | Emit JSON instead of human-readable output. |
| `--show-resolution` | off | Print the resolved category directory and validation result, then exit. |

Category names must match Hermes' regex: `^[a-z][a-z0-9_/-]*$` (lowercase letters, digits, hyphens, underscores, slashes). Creating an existing category is a no-op on the directory; re-writing its `DESCRIPTION.md` goes through the atomic backup writer.

## Feature: `talaria config sync`

Copy Hermes profile artefacts (config.yaml, SOUL.md, skills/, .env, context_length_cache.yaml) from one profile to another. **sync is the write-bearing command** — every other Talaria command is read-only against the Hermes runtime. sync never touches `state.db` or rotates logs.

### Usage

```bash
# Sync every phase from default to hermes-vc
talaria config sync default hermes-vc

# Preview without writing
talaria config sync default hermes-vc --dry-run

# Copy only specific config.yaml paths
talaria config sync default hermes-vc --only moa.max_tokens memory.provider

# Copy everything except mcp_servers and model
talaria config sync default hermes-vc -e mcp_servers model

# Sync a subset of skills
talaria config sync default hermes-vc --sync-skills github/dev-git-commit-message

# Inject a Hermes SSE endpoint into the target's mcp_servers
talaria config sync default hermes-vc --add-mcp-serve

# List the dot-notation paths in the source config
talaria config sync default --list
```

### Flags

| Flag | Default | Effect |
|------|---------|--------|
| `source` | — | Source profile name or path to `config.yaml`. |
| `target` | — | Target profile name or path. Required unless `--list`. |
| `--skip-config` | off | Skip the `config.yaml` phase. |
| `--skip-soul` | off | Skip the `SOUL.md` phase. |
| `--skip-skills` | off | Skip the `skills/` phase. |
| `--skip-env` | off | Skip the `.env` phase. |
| `--skip-cache` | off | Skip the `context_length_cache.yaml` phase. |
| `-e`, `--exclude` | none | Dot-notation paths to exclude from source. |
| `-o`, `--only` | none | Copy only these paths. Mutually exclusive with `-e`. |
| `--sync-skills` | all | Limit skills sync to categories or `category/skill-name` paths. |
| `--add-mcp-serve` | off | Add an `mcp_servers` entry to target connecting to a Hermes SSE endpoint. |
| `--dry-run` | off | Preview changes without writing. **Apply by default.** |
| `--no-backup` | off | Skip `.bak` backup before overwriting. |
| `--force-config` | off | Overwrite target `config.yaml` even when source is not newer. |
| `--list` | off | List dot-notation paths in source `config.yaml` and exit. |
| `--json` | off | Emit JSON report instead of human-readable output. |
| `-v`, `--verbose` | off | Show diffs, per-skill detail, and source/target banners. |

### Sync phases

Each phase is independent and any subset runs together:

1. **config.yaml** — element-level merge with `--exclude`/`--only` filtering. No-op when none of `--exclude`, `--only`, or `--add-mcp-serve` is set.
2. **SOUL.md** — straight copy with `.bak` when it differs.
3. **skills/** — byte-level tree comparison. New and differing skills are copied; `--sync-skills` filters by category or `category/skill-name`.
4. **.env** — additive merge. New variables appended; existing target values never overwritten.
5. **context_length_cache.yaml** — source-wins merge (factual measurements presumed more recent). Target-only entries preserved.

## Feature: `talaria config apply-auxiliary`

Derive `model.aliases._<usecase>` entries from a profile's own `auxiliary.<usecase>.model` block. Single-profile — no source/target split. Usecases set to a "no override" sentinel (`auto`, `inherit`, `default`, ...) are skipped; existing operator-defined aliases are always preserved.

### Usage

```bash
talaria config apply-auxiliary
talaria config apply-auxiliary --profile hermes-vc
talaria config apply-auxiliary --dry-run
```

### Flags

| Flag | Default | Effect |
|------|---------|--------|
| `--profile` | active | Hermes profile whose `config.yaml` should be updated. |
| `--config-path` | — | Explicit `config.yaml` path (overrides `--profile`). |
| `--dry-run` | off | Preview the derived aliases without writing. **Apply by default.** |
| `--no-backup` | off | Skip `.bak` backup before overwriting. |
| `--json` | off | Emit JSON report instead of human-readable output. |
| `--show-resolution` | off | Print the resolved config path and derived aliases, then exit. |

## Feature: `talaria config sync-env`

Refresh a single profile's `.env` values from the live process environment (`os.environ`). For every `KEY=...` line already present in the target file, the value is overwritten with the matching environment value. Keys absent from the file are **never added by default** — the file's variable set is the operator-defined scope. Four opt-in, repeatable key operations change this:

| Flag | Effect |
|------|--------|
| `--add-key KEY` | Append KEY with its value from the environment if absent. |
| `--skip-key KEY` | Preserve KEY's file value as-is on this run. |
| `--disable-key KEY` | Comment out KEY (`KEY=value` → `#KEY=value`). Reversible with `--enable-key`. |
| `--enable-key KEY` | Uncomment a previously disabled KEY. Value restored verbatim; not refreshed from env on the same run. |

### Usage

```bash
# Refresh existing .env values from the environment
talaria config sync-env --profile hermes-vc

# Add a new key from the environment
talaria config sync-env --profile hermes-vc --add-key NEW_API_KEY

# Disable and enable keys
talaria config sync-env --profile hermes-vc --disable-key OLD_KEY --enable-key RECOVERED_KEY

# Preview without writing
talaria config sync-env --profile hermes-vc --dry-run --json
```

### Flags

| Flag | Default | Effect |
|------|---------|--------|
| `--profile` | active | Hermes profile whose `.env` should be refreshed. |
| `--env-path` | resolved | Explicit `.env` path (overrides `--profile`). |
| `--add-key KEY` | none | Append KEY from the environment if absent. Repeatable. |
| `--skip-key KEY` | none | Exclude KEY from the refresh. Repeatable. |
| `--disable-key KEY` | none | Comment out KEY. Repeatable. |
| `--enable-key KEY` | none | Uncomment KEY. Repeatable. |
| `--dry-run` | off | Preview which variables would change without writing. |
| `--no-backup` | off | Skip `.bak` backup before overwriting `.env`. |
| `--json` | off | Emit JSON instead of human-readable output. |
| `--show-resolution` | off | Print the resolved `.env` path and which keys would be updated, then exit. |

The `export` prefix on matching lines is preserved on refresh and dropped on disable; comments and blank lines are preserved verbatim. Values are never echoed in `--show-resolution` output.

## Feature: `talaria completion`

Prints a self-contained shell completion script for the `talaria` CLI. Source it in your shell:

```bash
eval "$(talaria completion bash)"   # bash
eval "$(talaria completion zsh)"    # zsh
```

## Configuration

Talaria reads no configuration files itself. Every input is a CLI flag or environment variable.

| Var | Effect |
|-----|--------|
| `HERMES_PROFILE` | Profile name to inspect when `--profile` is omitted. |
| `XDG_CACHE_HOME` | Parent directory for the default catalog cache path. |
| `KILOCODE_API_KEY` | Kilo Code gateway API key used by `refresh-catalog --gateway kilocode` (also read from `~/.hermes/.env`). |
| `GITHUB_TOKEN` / `GH_TOKEN` | Used by `skills install` for GitHub tree expansion and by similarity fetches for raw content access. |

## Adding a new feature

Talaria has two feature groups plus a configuration command group. Inspection features live under `talaria/hermos/` (read-only against `state.db` and `logs/`). Sync phases live under `talaria/sync/` (the write-bearing carve-out; copies profile artefacts between profiles). Single-profile configuration features also live under `talaria/hermos/` when they operate on one profile's own files.

1. Add `talaria/hermos/<feature>.py` exposing `run(paths, **opts)` and `render_human(report)`.
2. Wire its argparse subparser into `talaria.cli.build_parser`.
3. Add tests under `tests/test_<feature>.py` using the shared `fake_hermes_root` fixture.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The test suite uses an in-memory SQLite `sessions` table and tmpdir logs — no live Hermes install is required. Network-bound tests stub `urllib.request.urlopen`; no real Kilo Code or GitHub calls happen during `pytest`.

## License

MIT — see `LICENSE`.

## References

- [Talaria — Wikipedia](https://en.wikipedia.org/wiki/Talaria) — mythology behind the name.
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — the agent this CLI maintains.
- [hermes-agent.nousresearch.com](https://hermes-agent.nousresearch.com) — the agent documentation and visual reference.
