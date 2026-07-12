# src/talaria/hermos

## Purpose

Hermes-specific Talaria features — inspections of the agent's `state.db`
and `logs/`, plus narrowly scoped maintenance for Hermes model metadata
caches.

## Ownership

- Each feature is a single module exposing `run(paths, **opts) -> dict`
  and `render_human(report) -> tuple[int, str]`.
- `doctor` is a multi-detector profile anomaly scan. It runs 12
  structured detectors against the resolved profile's `state.db`,
  `logs/`, and skill registry, plus a default-on free-flight
  pass that hands the model raw evidence (sessions' message text
  + log lines + aggregate stats + the profile's `config.yaml`)
  and asks it to find unknown-unknown anomalies and config
  improvements. Surfaced as `talaria hermes doctor`.
- `benchmark` is a per-model health/cost/latency/capability report.
  It discovers every unique `(model, provider)` pair from
  `config.yaml`, aggregates recent sessions from `state.db`, enriches
  with capability data from `models_dev_cache.json`, and makes one
  cached JSON smoke call per model when the cache is stale (default
  TTL 30 min). The report is read-only. Surfaced as
  `talaria hermes benchmark`.
- `doctor_llm` is the only place the doctor feature talks to a
  language model. **Nothing is hardcoded** — the module resolves
  the curator model + provider from the active profile's
  `config.yaml` at runtime via `resolve_curator_config(paths)`.
  Resolution order: `auxiliary.curator.model` +
  `auxiliary.curator.provider` → `model.aliases._curator` →
  `model.default` + top-level `provider`. The resolved
  `(model, provider)` pair is passed to `hermes_chat(prompt,
  model=..., provider=..., timeout=...)`. Any model failure /
  parse error / unavailability degrades to a no-op; the doctor
  command never breaks because the model failed.
- `doctor_free_flight` is the raw-evidence assembler + curator
  prompt for the open-ended pass. It reads and redacts the
  profile's `config.yaml` via `_redact_raw_yaml` (parent blocks
  like `auth`/`credentials`/`secrets` are fully redacted; leaf
  keys matching `api_key`/`token`/`password`/`secret` parts have
  their value replaced with `***REDACTED***`), then inlines the
  redacted YAML into the prompt. Log files are referenced via
  `@folder:` (framework-inlined). `state.db` is NOT inlined via
  `@file:` — instead `_dump_database_slices` queries the database
  read-only and writes four compact JSON slices (sessions metadata
  within the look-back window, compression locks, high-signal
  message failures, message truncations) to a temp directory. The
  slice file paths are listed in the prompt as plain paths — the
  model uses its file-read tools to inspect them on demand. The
  temp directory survives the full subprocess call + result parsing.
  Two finding kinds are returned: `anomaly` and `config_suggestion`.
- `refresh_catalog` is profile-agnostic by design — every Hermes profile
  reads the same provider cache. `--gateway` selects which provider's
  catalog is fetched and which provider manifest is written (currently
  only `kilocode`). Do not add `--profile` filtering, per-profile
  caches, or any logic that treats this feature as a state.db/logs consumer.
- `auxiliary` is profile-scoped by design — it derives
  `model.aliases._<usecase>` entries from the selected profile's own
  `auxiliary.<usecase>.model` block and writes them back into the same
  profile's `config.yaml`. No source/target split.
- `skill_install` is profile-scoped by design — it expands skill identifiers
  (recursive when they end in `/*`), invokes `hermes skills install` for each
  expanded child skill, and updates only that profile's `config.yaml` skill
  enable/disable policy. An optional `--category` forwards to
  `hermes skills install --category` so skills land in
  `skills/<category>/<name>/` instead of the flat root. The category value
  is the literal directory name (e.g. `software-development`), not a display
  name — Hermes' validation regex (<code>^&lbrack;a-z&rbrack;&lbrack;a-z0-9_/-&rbrack;*$</code>) rejects uppercase.
- `skill_uninstall` is profile-scoped by design — it mirrors `skill_install`:
  expand the identifier, invoke `hermes skills uninstall` for each child skill
  *name* (unlike install, uninstall takes a name, not an identifier), and
  remove the uninstalled names from the profile's `skills.disabled` list so the
  policy state does not reference skills that are no longer present.
  `hermes skills uninstall` has no `--yes` flag and prompts for confirmation
  on stdin; `default_uninstaller` feeds `input="y"` to make the call
  non-interactive. Hermes also exits 0 on several non-success conditions
  (prompt cancelled, skill not installed, skill is a builtin), so
  `default_uninstaller` detects failure markers (`Cancelled`, `Error:`,
  `not found`, `not a hub-installed`) in stdout and converts them to a
  non-zero return code. This detection MUST be preserved — without it
  Talaria falsely reports success for skills that were never removed.
- `skill_index` is profile-scoped and read-only — it exposes the single
  source of truth for the three places that record installed skills in
  a profile: the on-disk `<skills_root>/**/SKILL.md` walk (what
  `hermes skills list` shows), `<skills_root>/.hub/lock.json` (what
  `hermes skills search` shows), and `skills.disabled` in the profile's
  `config.yaml`. Both the `doctor` `skill_index_drift` detector and
  the `skill_prune` tool consume `read_index()` so their drift views
  agree. Writes are not part of this module — see `skill_prune`.
- `skill_prune` is profile-scoped by design and the write side of the
  skill-index reconcile. Three independent `--prune-*` flags select
  what to fix (filesystem-only orphans, lock-only orphans,
  `skills.disabled` orphans) and all default to OFF; the bare command
  is a no-op. `--apply` is required to execute — without it, the tool
  previews every planned action and exits 0. The default is `--no-backup`
  / dry-run behaviour; passes are atomic with a `.bak` snapshot for
  `lock.json` and via `talaria.sync.writer.write_with_backup` for
  `config.yaml`. Cross-profile prune (e.g. deleting the default
  `<hermes_root>/skills/` once every default-skill is shadowed by a
  named profile) is **deferred** — the user agreed to single-profile
  scope for this iteration. Surfaced as `talaria skills prune`.
- `serve_stop` is profile-agnostic by design — it detects the Hermes
  dashboard/serve backend by its listening TCP port via
  `psutil.net_connections` → PID, then SIGTERM/poll/SIGKILL. psutil
  abstracts the per-OS discovery substrate (`/proc/net` on Linux,
  libproc on macOS, NT APIs on Windows) behind one cross-platform call.
  It does not read `state.db`, `logs/`, or any profile artefact.
  `--profile` is recorded in the report only.
- `log_rotate` is profile-scoped by design — it rotates and prunes the
  active profile's `logs/` (or every profile's `logs/` with
  `--all-profiles`) using explicit flags. The active file rotation
  pattern is **copy → gzip → truncate** (never an in-place shift):
  when `--max-size` is set, an active file whose gzipped payload
  would exceed the cap is copied to `<name>.<ext>.1.gz` (gzip level
  6) and the source is truncated to zero bytes. `--max-age` deletes
  rotated copies and `logs/curator/<ts>/` snapshot directories
  whose mtime is older than the threshold. `--max-total` bounds the
  aggregate on-disk size of the directory by deleting the oldest
  rotated copies first. `--keep N` is a per-base-name floor that
  protects the newest N rotated copies regardless of age or size.
  Curator snapshot directories are deleted as a single unit
  (never partially) and the `--max-size` rotation is never
  substituted by a `.N` shift because Hermes writers append
  concurrently and a shift would race them. The tool is
  **explicit-only**: with no prune/rotate flags (`--max-size`,
  `--max-age`, `--max-total`) the file system is never touched and
  the report's `dry_run` field is true regardless of `--apply`; with
  at least one flag set, `--apply` is the default and `--dry-run`
  previews. The displayed default values (10 MiB gziped per file,
  30 days, 50 MiB total, keep 1) are not implicit limits — they are
  documentation of sensible values the operator can pass.
- `sync_env` is profile-scoped by design — it refreshes the selected
  profile's `.env` values from the live process environment
  (`os.environ`). For every `KEY=...` line already present in the
  target file, the value is overwritten with the matching environment
  value. Keys absent from the file are **never added by default** (the
  file's variable set is the operator-defined scope). Four opt-in,
  orthogonal, repeatable key operations change this:

  * `add_keys` / `--add-key` — append a named key that is absent from
    the file and present in the environment with a non-empty value.
    Keys already present (active or disabled) are never re-added.
  * `skip_keys` / `--skip-key` — keep a key out of the env-value
    refresh; its file value is preserved as-is.
  * `disable_keys` / `--disable-key` — comment out an active
    assignment (`KEY=value` → `#KEY=value`). The `export` prefix is
    dropped from the commented form. Disabled keys are hidden from the
    refresh scan and keep their value while inactive.
  * `enable_keys` / `--enable-key` — uncomment a previously disabled
    assignment (`#KEY=value` → `KEY=value`). The stored value is
    restored verbatim; the key is not refreshed from the environment
    on the same run.

  All four are processed in a single line scan; each original line is
  touched at most once. With none of them the behaviour is identical
  to the value-only refresh. Empty environment values leave the file
  value untouched (refresh) or skip the key (add). Writes go through
  the atomic backup writer. This supersedes the retired
  `~/.config/shell/sync-secrets.sh` shell helper.
- `skill_category` is profile-scoped by design — it creates a category
  directory under the profile's `skills/` tree and optionally writes a
  `DESCRIPTION.md` whose frontmatter `description:` is rendered in the
  Hermes system prompt after the category name. Category names are the
  literal directory name (e.g. `software-development`, `mlops/training`),
  validated against Hermes' regex <code>^&lbrack;a-z&rbrack;&lbrack;a-z0-9_/-&rbrack;*$</code>. Creating a
  category that already exists is a no-op on the directory; re-writing
  its `DESCRIPTION.md` goes through the atomic backup writer with an
  optional `.bak`. `--dry-run` must not create any directory or file.

## Local Contracts

- `doctor.OUTPUT_TOKEN_ALERT` and `DEFAULT_LOOKBACK_DAYS` are
  the canonical thresholds. Override per-call via flags, not by mutating
  module attributes.
- **Log-file discovery is `*.log` + `*.log.*` at the top level of
  the profile's `logs/` directory**, returned by
  `doctor.discover_log_files(log_dir)`. The discovery contract
  is: every active file (`agent.log`, `errors.log`, `tui_gateway_crash.log`,
  `gateway.log`, etc.) AND every rotated copy (`agent.log.1`,
  `agent.log.1.gz`). Non-log files (e.g. `README.md`) are excluded.
  Curator snapshot directories (`logs/curator/<ts>/`) are excluded by
  default and walked only when `include_curator=True` (CLI:
  `--include-curator`). The `--days` / `--since` window is then applied
  at the *line* level across every discovered file, so the verdict
  reflects "the part of each logfile that is X days old" — not the
  whole file.
- Log severity gating: the `truncation_log_markers` detector only counts
  matches on `WARNING|ERROR|CRITICAL` lines, never INFO. The
  `STREAM_DROP_PATTERN` count is reported separately and never triggers
  the alert.
- Reports must include `fired: bool` so JSON consumers can branch on the
  exit signal without parsing human output. The `selected_detectors`
  list names every detector that ran; `skipped_detectors` names every
  canonical detector excluded by `--only` / `--skip` (empty when all
  ran). The renderer surfaces a `skipped:` header whenever the list is
  non-empty. The `discovered_log_files`
  list is reported alongside the scan for reproducibility; `per_file`
  inside `truncation_log_markers` carries per-file hit counts.
- `doctor.run()` accepts a `free_flight: bool` parameter (default
  True). When True, the orchestrator appends the open-ended curator
  pass to `per_detector`. Two kinds of free-flight findings are
  emitted: `free_flight:anomaly:<slug>` (fired iff severity is
  warn/alert) and `free_flight:config:<slug>` (never fired; the
  operator decides whether to apply). A `free_flight` field in the
  report summarises findings_count / fired_count / token_budget.
  The curator model is resolved from the profile config via
  `resolve_curator_config(paths)` and invoked as `hermes_chat(prompt,
  model=..., provider=..., timeout=...)`. A model failure /
  unavailability / parse error degrades to a no-op — the doctor
  command stays useful offline.
- `doctor.run()` attaches a `remediation` field to every
  `per_detector` entry via the `_with_remediation_hint` helper.
  The field is the exact argv shape the operator can paste (e.g.
  `"--prune-stale-locks [--apply]"` or
  `"talaria skills prune --prune-filesystem-only
  --prune-lock-only --prune-disabled-orphans --apply"`). Two
  flavours of hint exist: doctor tactical flags (start with `--`,
  append to the existing `talaria hermes doctor …` command) and
  sibling-command remediations (start with the sibling command
  name, e.g. `talaria skills prune`, because the remediation lives
  in a different process with its own safety model). Detectors
  without a remediation get `None`. The hint is attached
  regardless of `fired` status so JSON consumers can introspect
  every detector. The renderer prints a `fix: <hint>` line under
  fired findings only — telling the operator how to fix a clean
  detector would be noise. `_DETECTOR_REMEDIATION_HINTS` is the
  single source of truth mapping detector id → hint text; free-
  flight findings (anomaly + config_suggestion) deliberately do
  NOT get enriched, because the tactical layer does not apply to
  them.
- `doctor_free_flight` redacts secrets before passing the evidence
  to the model. `_redact_raw_yaml` is a line-oriented scanner:
  parent blocks in `_REDACT_PARENT_KEYS` (`auth`, `credentials`,
  `secrets`, `providers`, `api_keys`, `tokens`) recursively
  redact every child line until the parent's indentation returns.
  Leaf keys whose split-parts match `_REDACT_VALUE_PARTS`
  (`api_key`, `token`, `password`, `secret`, `credential`, etc.)
  have their value replaced with `***REDACTED***`. Keys are split
  on `_`/`-`/non-alphanumeric delimiters before part matching so
  `max_tokens` (parts `["max","tokens"]`) is NOT redacted while
  `api_key` (parts `["api","key","api_key"]`) IS. False negatives
  are a security bug; false positives only erase legitimate
  config (safe). The redacted config is inlined into the prompt;
  the raw config is never handed to the model via `@file:`.
- `doctor_free_flight` enforces a token budget (default 100k) by
  trimming sessions tail-first; whole sessions are dropped, never
  partially. A budget of 0 disables the pass entirely (skipped
  detector result).
- `doctor.apply_config_suggestions()` reuses the
  :func:`talaria.sync.writer.write_with_backup` atomic backup
  writer plus :mod:`talaria.sync.yaml_io` for safe YAML round-trip. Each
  `config_suggestion` finding is a `yaml_path = suggested_value`
  set; bad paths are captured as `skipped: [{yaml_path, reason}]`
  and never raise. `dry_run=True` reports a unified diff in
  `apply.dry_run_diff` without writing bytes. The CLI surface
  is `--apply-curator-suggestions` to write and `--dry-run` to
  preview; neither is on by default. The apply path is
  curator-only: it filters the findings list to
  `id.startswith("free_flight:config:")` and ignores anomaly
  findings (deterministic detectors + free-flight `kind=anomaly`),
  because anomaly findings are diagnostic and have no tactical
  action. The flag name (`--apply-curator-suggestions`) reflects
  this — only curator suggestions are applied.
- `doctor.apply_tactical_actions()` is the second remediation path.
  Most deterministic findings stay diagnostic; three have an
  unambiguous local fix and are exposed as opt-in tactical flags:

  * `TACTICAL_PRUNE_STALE_LOCKS` (`--prune-stale-locks`) — drops
    every row in `compression_locks` whose `expires_at` is in the
    past. Stale locks block the next compressor run and indicate a
    crashed compressor process; deletion is the right action.
  * `TACTICAL_CLOSE_ZOMBIES` (`--close-zombies`) — sets
    `ended_at = now` on sessions whose `started_at` is older
    than `ZOMBIE_THRESHOLD_SECONDS` and whose `ended_at IS NULL`.
    The session row is preserved (only `ended_at` is written) so
    the audit trail of the crash survives.
  * `TACTICAL_PRUNE_GHOST_SESSIONS` (`--prune-ghost-sessions`)
    — deletes sessions within the look-back window whose `messages`
    join is empty. These are aborted creates — the session row
    was inserted but the writer crashed before any message
    arrived; there is nothing to preserve.

  Each flag defaults to **dry-run preview**: the report's
  `would_modify` lists every row that would be touched and no
  bytes are written. `--apply` flips every selected tactical
  flag into its write path; the per-action `dry_run` field
  records which side actually ran. The explicit-consent default
  (preview-by-default, write-on-`--apply`) matches the rest of
  Talaria's destructive-write tools: `talaria skills prune`,
  `talaria config sync`, `talaria hermes log-rotate`. Flags are
  independent — a doctor invocation can run any subset, and
  unselected actions return `{"selected": False}` so consumers
  can iterate `TACTICAL_ACTION_IDS` without conditional key
  access.

  Tactical writes go directly to `state.db` via SQLite WAL. They
  do NOT create a `state.db.bak` because partial-file backup of
  a live SQLite DB can capture a mid-transaction state and the
  operator's existing `state.db` backup regime is the contract
  for recovery. The other nine findings (truncation, stream
  drops, compression failures, rewinds, handoff errors, cost
  anomalies, skill_index_drift, and the unused-not-yet-flagged
  free-flight anomaly class) stay diagnostic because they need
  human context to fix correctly.
- The canonical operator-facing detector catalog (id, what it
  checks, threshold, severity, where it queries) is the
  `Detector catalog` table below. The same data is also surfaced
  via `talaria hermes doctor --show-resolution` for machines.
  Keep both in sync when detectors are added or thresholds change.

### Detector catalog

| id                          | what it checks                                                                  | threshold / window                              | severity   | source                                  |
|-----------------------------|--------------------------------------------------------------------------------|--------------------------------------------------|------------|------------------------------------------|
| `truncation_output`         | sessions with `output_tokens` above the alert threshold                         | 64 000 (no-rotation; = `OUTPUT_TOKEN_ALERT`)    | alert      | `sessions` table (SQL)                   |
| `truncation_finish_reason`  | messages with `finish_reason='length'` in the window                             | ≥ 1 hit                                         | alert      | `messages` table (SQL)                   |
| `truncation_log_markers`     | `WARNING|ERROR|CRITICAL` lines matching a length-class pattern in any `*.log` file | ≥ 1 hit                                         | alert      | log files (uses `doctor.discover_log_files`) |
| `stream_drops`              | mid-tool-call stream-drop warnings above the alert / borderline rate             | alert: 10 / borderline: 3 per window              | warn / alert | log files                              |
| `compression_stale_locks`   | `compression_locks` rows whose `expires_at` is in the past                       | ≥ 1 expired lock                                 | alert      | `compression_locks` table (SQL)          |
| `compression_failures`      | sessions with `compression_failure_error IS NOT NULL` in the window               | ≥ 1 session                                     | alert      | `sessions` table (SQL)                   |
| `rewinds`                   | sessions with `rewind_count` above the alert threshold                            | alert: 3 (counts ≥ 2 are reported)                | warn       | `sessions` table (SQL)                   |
| `handoff_errors`             | sessions with `handoff_error IS NOT NULL` in the window                          | ≥ 1 session                                     | alert      | `sessions` table (SQL)                   |
| `cost_anomalies`            | sessions with `cost_status` outside the allowed set, or est/actual divergence    | alert: divergence ≥ 25 % or bad status           | warn / alert | `sessions` table (SQL)                 |
| `zombie_sessions`            | sessions with `ended_at IS NULL` and `started_at` older than the threshold        | 24 h (`ZOMBIE_THRESHOLD_SECONDS`)                | alert      | `sessions` table (SQL)                   |
| `ghost_sessions`             | sessions with no `messages` rows in the window                                    | ≥ 1 session                                     | warn       | `sessions` + `messages` join (SQL)       |
| `skill_index_drift`          | names in filesystem walk but missing from `lock.json` (or vice versa), or in `skills.disabled` referencing nothing | ≥ 1 drift class | alert      | `skill_index.read_index()` (filesystem + lock.json + `config.yaml`) |

All detectors are *confident*: they decide in pure Python with no
model call. The free-flight curator pass is the only LLM use, and
its findings (anomaly + config_suggestion) are emitted under the
`free_flight:` id prefix so the renderer can group them.

- `benchmark` reports use `ok: bool`. The `per_model` list carries
  one entry per discovered `(model, provider)` pair with: `sources`
  (every config path that pointed to it), `reasoning_level` (from
  `sessions.model_config.reasoning_config.effort`), `capabilities`
  (enriched from `models_dev_cache.json` by slug match — handles
  provider-prefix differences like `zai-coding/` vs `z-ai/`),
  `state_db` (call count, avg tokens, cost aggregates),
  `avg_first_response_latency_s` (first user→assistant gap per
  session, averaged), `smoke` (cached or fresh JSON smoke-call
  result: `ok`, `latency_s`), and `vision` (a list of per-fixture
  results for vision-capable models only; `None` for non-vision
  models). Exit code 1 if any model fails the smoke test OR any
  vision fixture fails; 0 otherwise. The cache lives at
  `$XDG_CACHE_HOME/talaria/benchmark-cache-<profile>.json` and is
  keyed by `model--provider` for smoke and
  `<model_id>::vision::<fixture_label>` for vision. Smoke calls are
  suppressed with `--no-smoke` (state.db-only report); vision calls
  are suppressed with `--no-vision` (default: vision enabled for
  every model whose capabilities include vision per models.dev).
  `--ttl` controls the cache freshness window for both (default
  1800s = 30 min). The vision fixture images live in
  `assets/benchmark/vision/` (resolved by
  `_default_vision_dir()` relative to the repository root);
  `--vision-fixtures-dir` overrides the path. Vision ground-truth
  entries support `|`-separated alternatives for visually-ambiguous
  fixtures (e.g. the winged-sandal glyph reads as "wings",
  "winged", "sandal", or "butterfly" depending on the model — all
  are valid). The report summary carries `vision_enabled`,
  `vision_models` (count of vision-capable discovered models),
  `vision_calls_made`, `vision_calls_cached`, `vision_dir`, and
  `vision_dir_found`. Smoke and vision calls run in parallel via
  `ThreadPoolExecutor` (default `DEFAULT_JOBS = 8`; `--jobs N` to tune,
  `--jobs 1` for sequential). The report summary carries `jobs`.
- `refresh_catalog` reports use `ok: bool` instead of `fired` because
  there is no alert condition — there is only "refresh succeeded" vs.
  "tool error". Exit code 2 covers all failure modes
  (auth/network/parse/write); the `reason` field disambiguates them.
- `skill_install` reports use `ok: bool`; recursive installs are disabled by
  default via `skills.disabled` unless `--force-enable` or `--enable` says
  otherwise. `--dry-run` must not invoke Hermes or write `config.yaml`.
  Reports include `category` (the forwarded directory-name or empty string).
- `skill_uninstall` reports use `ok: bool`; successfully uninstalled skill
  names are removed from `skills.disabled`. `--dry-run` must not invoke
  Hermes or write `config.yaml`. Partial failures still clean up the skills
  that uninstalled successfully; `ok` is False when any uninstall fails.
- `auxiliary` reports use `ok: bool` and `changed: bool`. Writes go
  through the same atomic backup writer used by sync. `--dry-run` must not
  write a `config.yaml` or backup. Usecases whose `model` is a "no override" sentinel
  (`auto`, `inherit`, `default`, ...) are skipped; existing
  operator-defined `model.aliases` keys are always preserved.
- `serve_stop` reports use `ok: bool` and `reason` of
  `stopped | none | detected | partial`. Detection is cross-platform via
  `psutil.net_connections("inet")` filtered to LISTEN sockets on the
  target port. `--dry-run` must detect and report PIDs without sending
  any signal. Detection MUST be port-based, never cmdline-pattern
  based — the latter is exactly what `hermes serve --stop` does and it
  misses backends launched with a global flag between module and
  subcommand (e.g. `-p default dashboard`).
- `sync_env` reports use `ok: bool` and `changed: bool`. Fields:
  `updated` (list of `{key, old, new}`), `unchanged` (list of keys),
  `absent` (keys in the file but missing from the environment), `added`
  (list of `{key, value}` newly appended via `add_keys`), `add_skipped`
  (`{key, reason}` — `already-present | already-disabled | not-in-env |
  empty-value | invalid-name`), `skipped` (list of keys excluded from
  refresh via `skip_keys`), `skip_skipped` (`{key, reason}` — `not-found
  | invalid-name`), `disabled` (list of keys commented out),
  `disable_skipped` (`{key, reason}` — `already-disabled | not-found |
  invalid-name`), `enabled` (list of keys uncommented), `enable_skipped`
  (`{key, reason}` — `not-disabled | invalid-name`). `changed` is true
  only when bytes change (updated, added, disabled, or enabled); skipped
  keys never count as a change. Writes go through the same atomic backup
  writer used by sync and `auxiliary`. `--dry-run` must not write a
  `.env` or backup. The `export` prefix on matching lines is preserved
  on refresh and dropped on disable; comments and blank lines are
  preserved verbatim. Values are **never** echoed in `show_resolution`
  output (only key names) to avoid leaking secrets. When `add_keys`
  adds keys and the target file does not exist, the file is created
  with the added lines; when no key is addable and the file is missing,
  it stays a no-op.
- `log_rotate` reports use `ok: bool` and carry the **explicit-only**
  contract: `dry_run: true` whenever no prune/rotate flag was supplied
  OR the operator passed `--dry-run`. Fields: `scanned_files`,
  `scanned_bytes`, `total_size_after`, `rotated_count`,
  `truncated_count`, `deleted_count`, `deleted_bytes`, and a flat
  `actions` list (one entry per `rotate` / `copy` / `truncate` /
  `delete` / `skip` step, each with `path`, `action`, `reason`,
  `size_before`, `size_after`, `compressed_size`). The
  `curator/<ts>/` snapshot directories are deleted as a single unit;
  the active file rotation is `copy → gzip → truncate` (single
  `.1.gz` slot, never an in-place shift). The active file is never
  deleted — `--max-size` rotates, not removes, regardless of how
  large the gzipped payload is. The `--keep` floor protects the
  newest N rotated copies per base name; the `--max-total` rule
  walks the rotated set oldest-first and stops as soon as the
  total is below the cap. `--dry-run` must not copy, gzip, truncate,
  or delete any bytes (verified by tests).
- `skill_category` reports use `ok: bool`, `created: bool`, and
  `description_written: bool`. Writes go through the atomic backup
  writer. `--dry-run` must not create a directory or write
  `DESCRIPTION.md`. The category name must match Hermes' category regex
  <code>^&lbrack;a-z&rbrack;&lbrack;a-z0-9_/-&rbrack;*$</code>; invalid names raise `SkillCategoryError`.
- `skill_install` reports include `name_collisions` — a dict mapping
  colliding skill names to their competing identifiers. Hermes' lock.json
  keys by skill name, so two identifiers with the same trailing component
  (e.g. `cat-a/foo` and `cat-b/foo`) collide: the second install
  overwrites the first's lock entry and orphans its directory. Talaria
  detects this from the expanded identifier list and warns the operator
  (verbose output + render_human + JSON report). It does not block the
  install — Hermes handles the overwrite with `--force` or warns without it.
- `skill_similarity` provides fuzzy comparison for collision assessment:
  reads the installed skill's SKILL.md frontmatter from disk, fetches the
  incoming skill's frontmatter from GitHub raw content, and compares
  `name + description` via `difflib.SequenceMatcher` (stdlib, no deps).
  Default threshold 0.65. `--replace-similar-skill` uninstalls similar
  skills before installing the new one. `run()` reports
  `similarity_assessments` (list of per-skill ratio/similar/error dicts)
  and `replaced_skills` (list of uninstalled names). Similarity fetches
  only happen when the skill name already exists in lock.json — no extra
  network calls for non-colliding installs.

## Work Guidance

- Feature modules expose `run()` + `render_human()` and use Talaria CLI
  conventions (`--state-db`, `--log-dir`, `--profile`) for profile-scoped
  inputs.
- **Silent-by-default.** Feature modules should NOT print to stdout or
  stderr from `run()` unless the caller passed an explicit verbosity
  flag. The internal `_say(msg)` helper pattern (used by `skill_install`
  and `skill_uninstall`) gates every per-step progress line on a
  `verbose: bool` parameter; new feature modules should follow the
  same pattern. The CLI dispatcher (`talaria/cli/__init__.py`) gates
  the final `print(render_human(...))` on `args.verbose` — so the
  `render_human()` function may keep producing the full report; the
  dispatcher decides whether the operator sees it. Errors are always
  surfaced via the report's `ok: False` + `error` field (and via
  `print_error(...)` from the sync renderer when the failure happens
  before a report can be assembled), never via ad-hoc prints.

  Carve-out: `log_rotate` is explicit-only and prints by default from
  the CLI (no `--verbose` flag). With no action flags the renderer
  reports a "no actions planned" verdict that the operator must see to
  confirm the filesystem was untouched; gating that behind `--verbose`
  would hide the safety signal. The `run()` function itself stays
  silent — the carve-out is at the CLI dispatcher, not in this
  module.
- Feature-specific constants (regexes, thresholds, default windows) live at
  the top of the module.
- Network I/O is allowed only for catalog refresh. `refresh_catalog.fetch_catalog`
  does the fetch + reshape + write path inside the Python CLI.
- Skill install/uninstall must delegate actual semantics to `hermes skills
  install` / `hermes skills uninstall`; do not vendor or copy Hermes' hub
  installer into Talaria. Note: `hermes skills uninstall` takes a skill
  *name*, not an identifier — reduce each expanded identifier to its
  trailing component before delegation. `hermes skills uninstall` has no
  `hermes skills uninstall` has no `--yes` flag (unlike install) and prompts on stdin; the uninstaller must
  feed confirmation non-interactively and detect Hermes' false-zero-rc
  failures via stdout markers (see `default_uninstaller`).
  Both install and uninstall delegate to `hermes skills ...` subprocesses
  and must set `HERMES_HOME` (not `HERMES_PROFILE`) so the child process
  operates on the correct profile. Hermes resolves profiles exclusively
  through `HERMES_HOME` pointing at the profile directory
  (`~/.hermes/profiles/<name>`), never via a `HERMES_PROFILE` env var.
  Use `skill_install.profile_hermes_home(paths)` to compute the value.

## Verification

- Signal functions are tested against a synthetic SQLite database created
  with `tests._helpers.make_sessions_db`.
- Log scans are tested with hand-crafted lines in a tmpdir; severity gating
  is the must-test edge case. The `truncation_log_markers` detector tests
  cover: per-line window filtering inside a long file, multi-file
  aggregation, missing-file resilience, the `STREAM_DROP_PATTERN`
  separation, and per-file hit breakdown.
- `discover_log_files` is tested for: empty / missing dir, picking up
  active and rotated files, excluding non-log files, excluding
  `logs/curator/<ts>/` by default, walking the curator tree only when
  `include_curator=True`, and de-duplicating symlinks whose resolved
  target is the same file.
- `doctor` tests cover: per-detector unit tests with
  `make_full_state_db` fixtures (zombies, ghosts, rewinds, cost
  divergences, stale locks, compression failures, handoff errors,
  high-output sessions, length finish_reason), orchestrator with
  stub `free_flight_runner`, `--only` /
  `--skip` filtering (incl. unknown-id exit 2), missing state.db
  resilience, renderer for both structured and free-flight groups,
  CLI subprocess coverage for the full flag set, the
  tactical-action layer (`TestTacticalActions`) covering dry-run
  previews, actual writes, idempotency, out-of-window preservation,
  missing-DB error reporting, combined-flag independence, and
  stable report keys via `TACTICAL_ACTION_IDS`, and the
  remediation-hint layer (`TestRemediationHints`) covering
  `_with_remediation_hint` shape (known + unknown + free-flight
  detectors), tactical-detector/map consistency, JSON report
  integration, and the renderer's `fix:` line gating on
  `fired=True`.
- `doctor_free_flight` tests cover: config redaction (parent
  blocks recursively redacted, leaf keys by split-part matching,
  `max_tokens` preserved, comments/blanks preserved), zero-log-lines
  short-circuit, finding parsing (anomaly + config_suggestion
  kinds, default kind, invalid kind fallback), fenced-JSON / bare-
  JSON / garbage-input parsing, stub-runner finding return, and
  unavailable-runner degradation.
- `refresh_catalog` tests stub `urllib.request.urlopen` and run the full
  `run()` orchestrator against realistic upstream payloads. No real
  network is used.
- `skill_install` tests cover GitHub tree expansion, default-disabled policy,
  selected enablement, force-enable, dry-run suppression, CLI flags, and
  `--category` forwarding (command construction, omission when empty,
  run() propagation, report field, CLI `--help` presence).
- `skill_uninstall` tests cover disabled-list cleanup, name-based (not
  identifier) delegation to `hermes skills uninstall`, dry-run suppression,
  partial-failure cleanup, profile-scoped config writes, and CLI flags.
- `skill_category` tests cover name validation (accept/reject edge cases),
  skills-dir resolution per profile, directory creation (with and without
  description, nested categories, existing-dir no-op), DESCRIPTION.md
  overwrite with backup, `--no-backup`, `--dry-run` suppression, named
  profile path, invalid-name error, render_human output, show_resolution
  shape, and CLI `--help`.
- `auxiliary` tests cover alias injection, sentinel skipping, alias
  preservation, no-op cases, idempotency, dry-run suppression, profile
  path resolution, and CLI flags.
- `serve_stop` tests cover `psutil.net_connections` parsing (port
  filter, status filter, self-exclusion, dedup, no-laddr, psutil/OSError
  resilience), run() branches (none, detected/dry-run, stopped, partial),
  SIGTERM→SIGKILL escalation, ProcessLookup/Permission handling,
  `_pid_alive` psutil fallback, renderer verdicts, and CLI
  --help/--show-resolution/--json. psutil is mocked via monkeypatch.
- `sync_env` tests cover value refresh from env, no-add-by-default
  semantics (absent keys never written unless `add_keys` is passed),
  `export` prefix preservation, comment/blank preservation,
  empty-env-value skip (refresh and add), absent-from-env listing,
  dry-run suppression, default backup creation, `--no-backup`,
  missing-file no-op, idempotency, profile path resolution,
  explicit-path override, `show_resolution` shape, and CLI
  --help/--json/--dry-run/--show-resolution. The `add_keys` / `--add-key`
  tests cover append-from-env, add+refresh in one run, already-present
  skip, not-in-env skip, empty-value skip, invalid-name skip,
  duplicate collapse, missing-file creation, missing-file no-op when
  nothing is addable, dry-run suppression, default backup, default
  no-add backward compatibility, trailing-newline separation of the
  appended block, `run()` forwarding, and `show_resolution`
  `would_add`/`add_skipped` shape, plus CLI `--add-key` append,
  repeatable, and not-in-env cases. The `skip_keys` / `--skip-key`,
  `disable_keys` / `--disable-key`, and `enable_keys` / `--enable-key`
  tests cover value preservation, export-prefix drop on disable,
  verbatim restore on enable (not env-refreshed), not-found /
  already-disabled / not-disabled skip reasons, invalid-name skip,
  plain-comment non-match, dry-run suppression, default backup,
  `run()` forwarding, disable→enable roundtrip, combined operations
  in one run, `show_resolution` `would_skip`/`would_disable`/
  `would_enable` shape, and CLI `--skip-key`/`--disable-key`/
  `--enable-key`.
- `log_rotate` tests cover the rotation parser (`_parse_rotated` for
  active, plain rotated, gz rotated, multi-digit index, README
  exclusion, empty string), the classifier (active/rotated/other),
  active rotation (under cap skipped, over cap copies+gzip+truncates,
  second rotation overwrites the first), age-based delete (old
  rotated copies, curator snapshot directories, max-age=0 with
  keep floor), aggregate size prune (oldest-first with keep floor,
  under-cap no-op), the keep floor (keep=2 protects two newest,
  keep=0 protects nothing), dry-run suppression (no copy, no
  delete, no rotate), multi-profile target enumeration, run/render
  shape, `show_resolution` option echo, and CLI `--help`.

## Child DOX Index

- `doctor.py` — multi-detector profile anomaly scan. Runs 11
  structured detectors (truncation_output, truncation_finish_reason,
  truncation_log_markers, stream_drops, compression_stale_locks,
  compression_failures, rewinds, handoff_errors, cost_anomalies,
  zombie_sessions, ghost_sessions) against `state.db` and `logs/`,
  plus a default-on free-flight curator pass. Surfaced as
  `talaria hermes doctor`.
- `benchmark.py` — per-model health/cost/latency/capability report.
  Discovers every unique `(model, provider)` pair from `config.yaml`,
  aggregates recent sessions from `state.db` (call counts, token
  averages, cost, reasoning level from `model_config`, first-response
  latency from the messages table), enriches with capability data
  from `models_dev_cache.json` (reasoning, tool-call, vision,
  context/output limits, per-token cost matched by model slug so
  provider-prefix differences like `zai-coding/` vs `z-ai/` resolve).
  Makes one fresh JSON smoke call per model when the cache is stale
  (default TTL 30 min, cached to
  `$XDG_CACHE_HOME/talaria/benchmark-cache-<profile>.json`).
  For every model whose capabilities include vision, sends each
  fixture image from `assets/benchmark/vision/` via
  `hermes chat --image` and asserts the model reads it correctly
  (counting, OCR, spatial reasoning, brand-logo recognition).
  `--no-vision` disables vision calls; `--vision-fixtures-dir`
  overrides the fixture path. Smoke and vision calls run in parallel
  via `ThreadPoolExecutor` (default `DEFAULT_JOBS = 8`; `--jobs N` to
  tune, `--jobs 1` for sequential). Surfaced as
  `talaria hermes benchmark`. Read-only.
- `doctor_llm.py` — curator-model subprocess runner. Resolves
  the curator model + provider from the profile config at runtime
  via `resolve_curator_config(paths)`, then calls
  `hermes chat -q` via `hermes_chat(prompt, model=..., provider=...,
  timeout=...)`. Degrades to `AdjudicationUnavailable` on any
  failure; the orchestrator catches this and emits a no-op result.
- `doctor_free_flight.py` — open-ended curator pass for the
  `doctor` command. Reads + redacts the profile's `config.yaml`
  via `_redact_raw_yaml`, inlines it into the prompt, references
  log files + `state.db` via `@folder:` / `@file:` syntax. Resolves
  the curator model + provider from config at runtime (no
  hardcoding). Parses the curator response into findings of two
  kinds (`anomaly` + `config_suggestion`) and returns them as
  `DetectorResult` objects. Config suggestions never fire
  (`fired=False`); the operator decides whether to act.
- `refresh_catalog.py` — fetch + reshape the selected gateway catalog into
  the matching Hermes provider manifest cache. Profile-agnostic.
- `skill_install.py` — expand skill identifiers (recursive when ending in
  `/*`) and run per-skill Hermes installs, then update `skills.disabled`
  in profile config.
- `skill_uninstall.py` — mirror of `skill_install`: expand identifiers,
  run per-skill Hermes uninstalls (by name), then remove uninstalled names
  from `skills.disabled`.
- `skill_category.py` — create a skill category directory under the
  profile's `skills/` tree with an optional `DESCRIPTION.md`. Surfaced as
  `talaria skills create-category`.
- `skill_similarity.py` — fuzzy comparison (difflib.SequenceMatcher) of
  incoming vs installed skill frontmatter. Used by `skill_install` to
  detect similar-but-not-identical collisions and power
  `--replace-similar-skill`. Reads Hermes' lock.json + on-disk SKILL.md;
  fetches incoming SKILL.md from GitHub raw.
- `auxiliary.py` — derive `model.aliases._<usecase>` from a profile's own
  `auxiliary.<usecase>.model` block. Single-profile; surfaced as
  `talaria config apply-auxiliary`.
- `serve_stop.py` — detect and gracefully stop the Hermes dashboard/serve
  backend by its listening port. Profile-agnostic; cross-platform via psutil.
- `log_rotate.py` — rotate and prune the active profile's `logs/`
  directory (or every profile's `logs/` with `--all-profiles`).
  `--max-size` rotates active files whose gzipped payload exceeds the
  cap via `copy → gzip → truncate`; `--max-age` deletes old rotated
  copies and `logs/curator/<ts>/` snapshot directories; `--max-total`
  bounds the aggregate size by deleting the oldest rotated copies
  first; `--keep` is a per-base-name floor that protects the newest
  N copies. Explicit-only: with no flags the file system is never
  touched. Surfaced as `talaria hermes log-rotate`.
- `sync_env.py` — refresh a single profile's `.env` values from the live
  process environment (`os.environ`). Never adds new keys by default;
  opt-in `add_keys` / `--add-key` appends named keys from the environment;
  `--skip-key` excludes keys from refresh; `--disable-key` comments out
  assignments; `--enable-key` uncomments them. Surfaced as
  `talaria config sync-env`.