# Talaria — Winged Sandals for the Hermes Agent

<p align="center"><img src="assets/logo.svg" alt="Talaria"></p>

> *"With these sandals I shall bear the words of Olympus across wind and wave, swift as thought, returning before the laurel of my errand has time to wither."*

**Talaria** is a maintenance CLI for [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). It verifies mitigations, surfaces regressions, manages skill lifecycles, and keeps profiles in sync — giving the operator a single command for everything the agent itself doesn't cover.

The name is deliberate. In the [Greek mythos](https://en.wikipedia.org/wiki/Talaria), the *talaria* were the winged sandals of Hermes: golden, swift, granted by Zeus, and worn by the messenger god to travel between mortal and divine realms. Hermes Agent carries his namesake's errands — tool calls, model swaps, subagent dispatch. Talaria carries its operator's errands: profile resolution, signal verification, and the verdict that tells you whether the agent is still flying cleanly or has begun to drag a wing.

## Highlights

Talaria gives Hermes operators a single installable CLI for everything the agent itself doesn't cover — multi-detector anomaly diagnosis, per-model benchmarking, skill lifecycle management, and controlled configuration sync across profiles:

- 🩺 **Diagnose agent anomalies** — 11 structured detectors scan `state.db` + `logs/` for truncation, compression stalls, zombie sessions, cost spikes, and more, plus a default-on free-flight pass that hands the assembled evidence to the operator's curator model for open-ended anomaly detection and config suggestions.
- 📊 **Benchmark every model** — per-model health, cost, latency, reasoning level, and capabilities (vision, tool-call, structured-output, context/output limits, per-token cost) for every `(model, provider)` pair the profile routes through. Combines `state.db` session aggregation, `models.dev` capability data, and cached JSON smoke calls — one deduplicated call per unique pair, not per config reference.
- 👁️ **Verify vision capability** — the benchmark automatically sends real images to every vision-capable model (per models.dev) and asserts the model reads them correctly: counting, OCR, spatial reasoning, and brand-logo recognition. `--no-vision` disables the checks.
- 🧩 **Manage skills at scale** — recursively install, categorize, and uninstall third-party skill collections from GitHub or skills.sh, with collision detection and fuzzy similarity matching to prevent silent overwrites.
- 🔄 **Keep profiles in sync** — copy config, SOUL.md, skills, `.env`, and context cache between profiles; refresh `.env` values from the live environment; derive model aliases from auxiliary pins.
- 🗂️ **Refresh model catalogs** — fetch and reshape gateway-backed model manifests into Hermes' provider cache.
- 🛑 **Stop runaway backends** — detect and gracefully terminate Hermes dashboard/serve processes by port (not cmdline pattern).
- 🌀 **Bound log directories** — rotate active logs (copy → gzip → truncate), prune rotated copies and curator snapshots by age and aggregate size, and sweep every profile with `--all-profiles`.

Every command follows the same conventions: profile-aware path resolution, structured JSON output for cron and dashboards, `--dry-run` for safe previews, and `--show-resolution` for path debugging.

## Features at a glance

| Command | Group | Purpose |
|---------|-------|---------|
| `talaria paths` | — | Print the resolved profile + paths Talaria would inspect. |
| `talaria completion <shell>` | — | Print a bash or zsh shell completion script. |
| `talaria hermes doctor` | inspection | Multi-detector profile anomaly scan (state.db + logs + optional curator free-flight pass). |
| `talaria hermes benchmark` | inspection | Per-model health, cost, latency, capabilities, vision verification from state.db + models.dev + cached smoke + vision calls. Parallel subprocess execution (`--jobs`). |
| `talaria hermes refresh-catalog` | maintenance | Refresh and reshape a gateway-backed model manifest. |
| `talaria hermes fix-context-cache` | maintenance | Repair curated known-bad entries in a profile's context cache. |
| `talaria hermes serve-stop` | maintenance | Detect and stop the Hermes dashboard/serve backend by port. |
| `talaria hermes log-rotate` | maintenance | Rotate active logs (copy→gzip→truncate) and prune rotated copies + curator snapshots by age / aggregate size. |
| `talaria skills install` | skills | Install skill(s) under an identifier (recursive if `/*`) with category and enable policy. |
| `talaria skills uninstall` | skills | Uninstall skill(s) under an identifier and clean up `skills.disabled`. |
| `talaria skills create-category` | skills | Create a skill category directory with an optional description. |
| `talaria config sync` | config | Copy config.yaml, SOUL.md, skills/, .env, context cache between profiles. |
| `talaria config apply-auxiliary` | config | Derive `model.aliases._<usecase>` from a profile's `auxiliary` block. |
| `talaria config sync-env` | config | Refresh a profile's `.env` values from the live environment. |

## Install

```bash
# from a clone of this repo (uv is the recommended tool — the repo
# ships a uv.lock so the dependency resolution is reproducible)
uv sync

# editable install with the dev extra (pytest + pytest-cov)
uv pip install -e ".[dev]"

# or with plain pip
pip install -e ".[dev]"

# or, once published
pip install talaria
```

The `[dev]` extra pulls in `pytest` and `pytest-cov` for the test suite. The `[dependency-groups] dev` group additionally pulls in `pillow` (used only by `assets/benchmark/vision/generate_vision_fixtures.py` to regenerate the fixture images).

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

Talaria follows a **two-tier output contract** so it scripts cleanly while
remaining useful interactively.

**Silent by default, opt-in to print** — most commands are exit-code-only
when run without flags. `--verbose` (`-v`) prints the human-readable report.
This is the pattern for: `hermes refresh-catalog`, `hermes fix-context-cache`,
`hermes serve-stop`, `skills install`, `skills uninstall`, `skills create-category`,
`config sync`, `config apply-auxiliary`, `config sync-env`.

**Print by default, opt-out to suppress** — inspection commands whose report
*is* the answer print on stdout automatically. Pass `--quiet` (`-q`) to suppress
and get the exit code only. This is the pattern for:

- `talaria paths` — its output *is* the resolved profile + paths.
- `talaria hermes doctor` — the 11-detector anomaly report.
- `talaria hermes benchmark` — the per-model health/cost/capability report.
- `talaria hermes log-rotate` — explicit-only: with no action flags it reports
  scanned size/age and exits 0 without writing.
- `talaria completion` — its output *is* the completion script the operator
  asked for.
- `talaria config sync --list` — its output *is* the dot-path list the
  operator asked for.

On the print-by-default commands `-v`/`--verbose` is a kept as a no-op
(convenience / muscle memory), not an alternative output channel — `-q` is
the real suppressor.

Both tiers share the explicit data channels, which always print regardless of tier:

- `--json` — emit a stable JSON document to stdout (suitable for cron, dashboards,
  and other agents).
- `--show-resolution` — print the resolved paths / sources and exit 0 without
  running the feature (useful for debugging).

Errors always go to stderr.

### Debug helpers

Every feature with path resolution accepts `--show-resolution`: it prints what Talaria would inspect and exits 0 without running the feature.

## Usage

```bash
# Inspect which profile + paths Talaria would use
talaria paths

# Run the multi-detector profile anomaly scan against the active profile
talaria hermes doctor

# Preview config suggestions from the free-flight curator pass without writing
talaria hermes doctor --apply-suggestions --dry-run

# Benchmark every model: health + cost + latency + capabilities + vision
talaria hermes benchmark

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

## Feature: `talaria hermes doctor`

Runs 11 structured detectors against the resolved profile's `state.db` and `logs/`, plus a default-on free-flight curator pass that hands the assembled evidence to the operator's configured `_curator` model for open-ended anomaly detection and config suggestions. The free-flight pass is the only way the structured detectors catch unknown-unknown anomalies — pass `--no-free-flight` for pure deterministic results. Use `--apply-suggestions` to write config-suggestion findings into the profile's `config.yaml` (atomic backup first; `--dry-run` previews the diff).

### Usage

```bash
talaria hermes doctor
talaria hermes doctor --profile hermes-vc
talaria hermes doctor --no-free-flight --json --days 7
talaria hermes doctor --only truncation_output,zombie_sessions
```

### Flags

| Flag | Default | Effect |
|------|---------|--------|
| `--days N` | `2` | Sliding look-back window in days (UTC). |
| `--since YYYY-MM-DD` | — | Absolute start date; overrides `--days`. |
| `--only ID,ID,...` | all | Comma-separated detector id whitelist. |
| `--skip ID,ID,...` | none | Comma-separated detector id blacklist. |
| `--no-free-flight` | off | Skip the curator model pass; pure deterministic. |
| `--apply-suggestions` | off | Write `config_suggestion` findings to `config.yaml` (atomic backup first). |
| `--dry-run` | off | Preview the apply diff without writing. |
| `--include-curator` | off | Walk `logs/curator/<ts>/` snapshot trees. |
| `--profile NAME` | from env/file | Profile to inspect. |
| `--state-db PATH` | resolved | Override the `state.db` path. |
| `--log-dir PATH` | resolved | Override the `logs/` directory. |
| `--json` | off | Emit JSON instead of human-readable output. |
| `--show-resolution` | off | Print resolved paths + detector catalog and exit 0. |
| `-q`, `--quiet` | off | Suppress the human-readable report; exit code only. |
| `-v`, `--verbose` | off | No-op alias (default already prints the report); kept for convenience. |

### Detectors

The 11 deterministic detectors cover: output-token truncation (SQL + log markers + finish_reason), stream drops, compression stale locks/failures, rewind storms, handoff errors, cost anomalies, zombie sessions, and ghost sessions. Exit code is 1 if any detector fires; 0 if clean. The free-flight curator pass is default-on and finds issues the deterministic rules don't know to look for.

## Feature: `talaria hermes benchmark`

Reports per-model health, cost, latency, reasoning level, capabilities, and (for vision-capable models) image-reading verification for every model the profile routes through. Combines state.db session aggregation, capability data from `models.dev`, cached JSON smoke calls, and cached vision-capability checks.

### Usage

```bash
# full report: state.db stats + capabilities + smoke + vision (cached for 30 min)
talaria hermes benchmark

# state.db only, no model calls at all
talaria hermes benchmark --no-smoke --no-vision

# JSON for scripting
talaria hermes benchmark --json --no-smoke

# inspect a specific profile
talaria hermes benchmark --profile hermes-vc --days 1

# skip vision checks only (keep smoke)
talaria hermes benchmark --no-vision
```

### Flags

| Flag | Default | Effect |
|------|---------|--------|
| `--days N` | `7` | Look-back window for state.db session aggregation. |
| `--ttl SECONDS` | `1800` | Cache TTL for smoke + vision results (30 min). Within the window, cached results are reused. |
| `--no-smoke` | off | Skip all JSON smoke calls; report only state.db data. |
| `--no-vision` | off | Skip all vision-capability checks. By default, every discovered model whose capabilities include vision (per models.dev) is tested against the vision fixture images. |
| `--jobs N`, `-j N` | `8` | Max parallel subprocess calls for smoke and vision checks. Each call is an I/O-bound model API wait, so parallelism gives near-linear speedup on the cold path. Use `--jobs 1` for sequential execution. |
| `--vision-fixtures-dir PATH` | `assets/benchmark/vision/` | Override the vision fixture-image directory. |
| `--profile NAME` | from env/file | Profile to inspect. |
| `--state-db PATH` | resolved | Override the `state.db` path. |
| `--config PATH` | resolved | Override the `config.yaml` path. |
| `--cache PATH` | resolved | Override the cache file path. |
| `--json` | off | Emit JSON instead of human-readable output. |
| `--show-resolution` | off | Print resolved paths + discovered models and exit. |
| `-q`, `--quiet` | off | Suppress the human-readable report; exit code only. |
| `-v`, `--verbose` | off | No-op alias (default already prints the report); kept for convenience. |

### What it reports per model

For each unique `(model, provider)` pair discovered in `config.yaml`:

- **Reasoning level** — from `sessions.model_config.reasoning_config.effort` (e.g. `low`, `medium`, `high`).
- **Capabilities** — from `models.dev`: reasoning, tool-call, vision, structured-output, context/output limits, per-token cost. Matched by model slug so provider-prefix differences (`zai-coding/glm-5.2` vs `z-ai/glm-5.2`) resolve.
- **Session stats** — call count, avg input/output/reasoning/cache tokens, total and per-session cost.
- **First-response latency** — avg time from first user message to first assistant reply (time-to-first-token proxy).
- **Smoke result** — fresh or cached: did the model return parseable JSON within the timeout?
- **Vision results** — for vision-capable models only: per-fixture results showing whether the model correctly read and reasoned about each test image.

Exit code is 1 if any model fails the smoke test OR any vision fixture fails; 0 if all pass.

### Vision-capability benchmark

Vision is integrated into the benchmark itself, not a separate command. Every discovered model whose capabilities include vision (per `models.dev`) is automatically tested against 4 fixture images, each with a deterministic ground-truth answer:

| Fixture | What it tests |
|---------|---------------|
| `count_grid.png` | Counting + colour discrimination (10 circles, 4 red). |
| `error_card.png` | OCR of structured error text (code ERR_4042, module agent.compression). |
| `spatial_arrow.png` | Spatial reasoning + arrow direction (points to box B). |
| `logo/logo-512.png` | Brand-logo recognition — reads the TALARIA wordmark, the winged-sandal glyph, and the gold palette. |

Vision results are cached alongside smoke results in the same cache file (`$XDG_CACHE_HOME/talaria/benchmark-cache-<profile>.json`), keyed by `<model_id>::vision::<fixture_label>`. The fixture images live in `assets/benchmark/vision/` and can be overridden with `--vision-fixtures-dir`.

Ground-truth entries support `|`-separated alternatives for visually-ambiguous fixtures — e.g. the stylised wing glyph may read as "wings", "winged", "sandal", or "butterfly" depending on the model, all of which are valid. Pass `--no-vision` to skip all vision checks.

Smoke and vision calls run in parallel (default 8 workers, `--jobs N` to tune). Each call is an I/O-bound model API wait, so a cold run of 10 vision-capable models × 4 fixtures (40 calls) finishes in ~3 min instead of ~21 min sequential.

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
| `-v`, `--verbose` | off | Print the human-readable report (default: silent, exit code only). |

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
| `-v`, `--verbose` | off | Print the human-readable report (default: silent, exit code only). |

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
| `-v`, `--verbose` | off | Print the human-readable report (default: silent, exit code only). |

Report `reason`: `stopped` | `none` | `detected` (dry-run) | `partial` (some PIDs survived SIGKILL).

## Feature: `talaria hermes log-rotate`

Rotates and prunes the active profile's `logs/` directory (or every profile's `logs/` with `--all-profiles`). Three orthogonal rules bound the directory size, all explicit and all opt-in:

- **`--max-size BYTES`** rotates any active file whose gzipped payload would exceed the cap. The pattern is **copy → gzip → truncate**: the current bytes are copied to `<name>.<ext>.1.gz` (gzip level 6), then the source is truncated to zero. A second rotation overwrites the previous `.1.gz` (single-slot policy). Hermes writers append concurrently, so a `.N` shift would race them — the copy-then-truncate order is the same pattern newsyslog and logrotate use.
- **`--max-age DAYS`** deletes rotated copies (`*.N` / `*.N.gz`) and `logs/curator/<ts>/` snapshot directories whose mtime is older than the threshold. Curator directories are removed as a single unit, never partially.
- **`--max-total BYTES`** bounds the aggregate on-disk size of the directory by deleting the oldest rotated copies first, walking mtime-ascending until the total drops below the cap. Active files are rotated (not deleted) by this rule.

A **`--keep N`** floor (default 1) protects the newest N rotated copies per base name regardless of age or aggregate size. **`--all-profiles`** sweeps the root `~/.hermes/logs/` plus every `~/.hermes/profiles/*/logs/` in one run. The tool is **explicit-only**: with no prune/rotate flag the file system is never touched — the report's `dry_run` is true regardless of `--dry-run`, and the `actions` list is empty. The default values shown below (10 MiB gziped, 30 days, 50 MiB total, keep 1) are documentation of sensible values; they are not implicit limits.

### Usage

```bash
# Preview what would happen on the active profile — no bytes written
talaria hermes log-rotate --dry-run \
    --max-size 10485760 --max-age 30 --max-total 52428800

# Apply: rotate files over 10 MiB gziped, delete rotated copies older
# than 30 days, keep the directory below 50 MiB total
talaria hermes log-rotate \
    --max-size 10485760 --max-age 30 --max-total 52428800

# Sweep every profile's logs/ in one go
talaria hermes log-rotate --all-profiles --dry-run --json
```

### Flags

| Flag | Default | Effect |
|------|---------|--------|
| `--profile NAME` | active | Profile whose `logs/` to operate on. Ignored when `--all-profiles` is set. |
| `--all-profiles` | off | Sweep the root `~/.hermes/logs/` plus every `~/.hermes/profiles/*/logs/` in one run. |
| `--max-size BYTES` | off | Per-file cap on the gzipped payload. Active files over the cap are rotated (copy → `<name>.<ext>.1.gz` → truncate to 0). |
| `--max-age DAYS` | off | Delete rotated copies and `logs/curator/<ts>/` directories older than the threshold. |
| `--max-total BYTES` | off | Cap the aggregate directory size; oldest rotated copies are deleted first until the total drops below. |
| `--keep N` | `1` | Minimum number of rotated copies to preserve per base name. Protects the newest N regardless of age or aggregate size. |
| `--dry-run` | off | Plan actions without copying, gzipping, truncating, or deleting. |
| `--json` | off | Emit JSON instead of human-readable output. |
| `--show-resolution` | off | Print the resolved log dir, scanned size, and planned actions, then exit. |

The report prints to stdout by default (no `--verbose` needed).

### Report shape

Each per-directory report carries `profile`, `log_dir`, `ok`, `scanned_files`, `scanned_bytes`, `total_size_after`, `rotated_count`, `truncated_count`, `deleted_count`, `deleted_bytes`, `dry_run`, and a flat `actions` list. Each action entry is `{path, action, reason, size_before, size_after, compressed_size}` with `action` in `{rotate, copy, truncate, delete, skip}`. The `rotate` / `copy` / `truncate` triple for one active file appears as three separate entries so JSON consumers can follow the per-step effect. The JSON envelope for `--all-profiles` is `{"reports": [<report>, ...]}`.

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
| `-v`, `--verbose` | off | Stream per-skill progress to stderr AND print the human-readable report (default: silent, exit code only). |

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
| `-v`, `--verbose` | off | Stream per-skill progress to stderr AND print the human-readable report (default: silent, exit code only). |

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
| `-v`, `--verbose` | off | Print the human-readable report (default: silent, exit code only). |

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
| `--mcp-serve-name NAME` | `hermes` | Name for the injected `mcp_servers` entry. |
| `--mcp-serve-port N` | `9119` | Port for the Hermes SSE endpoint. |
| `--mcp-serve-host HOST` | `localhost` | Host for the Hermes SSE endpoint. |
| `--dry-run` | off | Preview changes without writing. **Apply by default.** |
| `--no-backup` | off | Skip `.bak` backup before overwriting. |
| `--force-config` | off | Overwrite target `config.yaml` even when source is not newer. |
| `--list` | off | List dot-notation paths in source `config.yaml` and exit. |
| `--json` | off | Emit JSON report instead of human-readable output. |
| `-v`, `--verbose` | off | Print the human-readable report on stdout with diffs, per-skill detail, and source/target banners (default: silent, exit code only). |

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
| `-v`, `--verbose` | off | Print the human-readable report (default: silent, exit code only). |

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
| `-v`, `--verbose` | off | Print the human-readable report (default: silent, exit code only). |

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
| `XDG_CACHE_HOME` | Parent directory for the catalog cache and the benchmark smoke/vision cache (`$XDG_CACHE_HOME/talaria/benchmark-cache-<profile>.json`). |
| `KILOCODE_API_KEY` | Kilo Code gateway API key used by `refresh-catalog --gateway kilocode` (also read from `~/.hermes/.env`). |
| `GITHUB_TOKEN` / `GH_TOKEN` | Used by `skills install` for GitHub tree expansion and by similarity fetches for raw content access. |

**Test-only env vars** (never documented in a production env-var table; see `tests/AGENTS.md` §Local Contracts):

| Var | Effect |
|-----|--------|
| `_TESTING_TALARIA_RUN_MODEL_BENCH=1` | Opt-in: run the live model benchmark tests (`hermes chat` smoke + vision calls). Burns tokens. |
| `_TESTING_TALARIA_SKIP_MODEL_BENCH=1` | Opt-out: force-skip live tests even when the opt-in is set. Wins over opt-in. |
| `_TESTING_TALARIA_PROFILE_CONFIG=<path>` | Override the config.yaml path used by the live benchmark tests. |

## Adding a new feature

Talaria has two feature groups plus a configuration command group. Inspection features live under `talaria/hermes/` (read-only against `state.db` and `logs/`). Sync phases live under `talaria/sync/` (the write-bearing carve-out; copies profile artefacts between profiles). Single-profile configuration features also live under `talaria/hermos/` when they operate on one profile's own files.

1. Add `talaria/hermos/<feature>.py` exposing `run(paths, **opts) -> dict` and `render_human(report) -> tuple[int, str]`.
2. Wire its argparse subparser into `talaria.cli.build_parser`. The feature's **output tier** decides the dispatch shape:
   - **Silent-by-default** (maintenance/action commands): add `-v, --verbose` and gate the report print on `if args.verbose:`. Default run is exit code only.
   - **Print-by-default** (inspection commands whose report *is* the answer): add `-q, --quiet` and `-v, --verbose` (no-op alias for muscle memory), set `set_defaults(func=cmd_xxx, quiet=False)`, and gate the report print on `if not args.quiet:`. Default run prints.
   In both tiers, `--json` always prints and `--show-resolution` always prints + exits 0.
3. Add tests under `tests/test_<feature>.py` using the shared `fake_hermes_root` fixture. CLI tests that assert on human-readable stdout must respect the tier: silent-by-default commands pass `--verbose` explicitly; print-by-default commands pass `-q/--quiet` to assert on the silent path.

## Development

```bash
# install (uv recommended — repo ships a uv.lock)
uv sync && uv pip install -e ".[dev]"

# run the full test suite (no live Hermes or network required by default)
pytest

# run just the benchmark feature tests
pytest tests/test_benchmark.py
```

The default test suite uses an in-memory SQLite `sessions` table and tmpdir logs — no live Hermes install is required. Network-bound tests stub `urllib.request.urlopen`; no real Kilo Code or GitHub calls happen during `pytest`.

The **live model benchmark** (smoke + vision calls against real models via `hermes chat`) is gated behind `_TESTING_TALARIA_RUN_MODEL_BENCH=1` and skipped by default to avoid burning tokens. The vision fixtures (`assets/benchmark/vision/`) are checked into the repo; regenerate them with `uv run python assets/benchmark/vision/generate_vision_fixtures.py` (requires the `pillow` dev dependency).

## License

MIT — see `LICENSE`.

## References

- [Talaria — Wikipedia](https://en.wikipedia.org/wiki/Talaria) — mythology behind the name.
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — the agent this CLI maintains.
- [hermes-agent.nousresearch.com](https://hermes-agent.nousresearch.com) — the agent documentation and visual reference.
