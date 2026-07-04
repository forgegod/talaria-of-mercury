# src/talaria/hermos

## Purpose

Hermes-specific Talaria features — inspections of the agent's `state.db`
and `logs/`, plus narrowly scoped maintenance for Hermes model metadata
caches.

## Ownership

- Each feature is a single module exposing `run(paths, **opts) -> dict`
  and `render_human(report) -> tuple[int, str]`.
- `moa_truncation` verifies output-token trends and length-class log markers.
- `refresh_catalog` is profile-agnostic by design — every Hermes profile
  reads the same provider cache. `--gateway` selects which provider's
  catalog is fetched and which provider manifest is written (currently
  only `kilocode`). Do not add `--profile` filtering, per-profile
  caches, or any logic that treats this feature as a state.db/logs consumer.
- `context_cache_fix` is profile-scoped by design — it repairs only the
  selected profile's `context_length_cache.yaml` using a curated fix table
  and preserves unrelated cache entries.
- `auxiliary` is profile-scoped by design — it derives
  `model.aliases._<usecase>` entries from the selected profile's own
  `auxiliary.<usecase>.model` block and writes them back into the same
  profile's `config.yaml`. No source/target split.
- `skill_install` is profile-scoped by design — it expands recursive skill
  identifiers, invokes `hermes skills install` for each child skill, and
  updates only that profile's `config.yaml` skill enable/disable policy.
- `serve_stop` is profile-agnostic by design — it detects the Hermes
  dashboard/serve backend by its listening TCP port via `/proc/net/tcp`
  → socket inode → PID, then SIGTERM/poll/SIGKILL. It does not read
  `state.db`, `logs/`, or any profile artefact. `--profile` is recorded
  in the report only.

## Local Contracts

- `moa_truncation.MOA_OUTPUT_TOKEN_ALERT` and `DEFAULT_LOOKBACK_DAYS` are
  the canonical thresholds. Override per-call via flags, not by mutating
  module attributes.
- Log severity gating: Signal B only counts matches on
  `WARNING|ERROR|CRITICAL` lines, never INFO. The
  `STREAM_DROP_PATTERN` count is reported separately and never triggers
  the alert.
- Reports must include `fired: bool` so JSON consumers can branch on the
  exit signal without parsing human output.
- `refresh_catalog` reports use `ok: bool` instead of `fired` because
  there is no alert condition — there is only "refresh succeeded" vs.
  "tool error". Exit code 2 covers all failure modes
  (auth/network/parse/write); the `reason` field disambiguates them.
- `context_cache_fix` reports use `ok: bool` and `changed: bool`.
  Writes must go through the same atomic backup writer used by sync.
  `--dry-run` must not write a cache file or backup.
- `skill_install` reports use `ok: bool`; recursive installs are disabled by
  default via `skills.disabled` unless `--force-enable` or `--enable` says
  otherwise. `--dry-run` must not invoke Hermes or write `config.yaml`.
- `auxiliary` reports use `ok: bool` and `changed: bool`. Writes go
  through the same atomic backup writer used by sync and
  `context_cache_fix`. `--dry-run` must not write a `config.yaml` or
  backup. Usecases whose `model` is a "no override" sentinel
  (`auto`, `inherit`, `default`, ...) are skipped; existing
  operator-defined `model.aliases` keys are always preserved.
- `serve_stop` reports use `ok: bool` and `reason` of
  `stopped | none | detected | unsupported | partial`. It is Linux-only
  (the `/proc` filesystem is the discovery substrate); on other
  platforms it returns `ok: False, reason: "unsupported"` rather than
  attempting a fallback. `--dry-run` must detect and report PIDs without
  sending any signal. Detection MUST be port-based
  (`/proc/net/tcp` → inode → `/proc/<pid>/fd`), never cmdline-pattern
  based — the latter is exactly what `hermes serve --stop` does and it
  misses backends launched with a global flag between module and
  subcommand (e.g. `-p default dashboard`).

## Work Guidance

- Feature modules expose `run()` + `render_human()` and use Talaria CLI
  conventions (`--state-db`, `--log-dir`, `--profile`) for profile-scoped
  inputs.
- Feature-specific constants (regexes, thresholds, default windows) live at
  the top of the module.
- Network I/O is allowed only for catalog refresh. `refresh_catalog.fetch_catalog`
  does the fetch + reshape + write path inside the Python CLI.
- `context_cache_fix.KNOWN_CONTEXT_FIXES` must stay small and source-backed;
  do not add speculative model windows.
- Skill installation must delegate actual install semantics to `hermes skills
  install`; do not vendor or copy Hermes' hub installer into Talaria.

## Verification

- Signal functions are tested against a synthetic SQLite database created
  with `tests._helpers.make_sessions_db`.
- Log scans are tested with hand-crafted lines in a tmpdir; severity gating
  is the must-test edge case.
- `refresh_catalog` tests stub `urllib.request.urlopen` and run the full
  `run()` orchestrator against realistic upstream payloads. No real
  network is used.
- `context_cache_fix` tests cover bad existing entries, missing-key
  insertion, `--only-existing`, dry-run write suppression, and CLI profile
  resolution.
- `skill_install` tests cover GitHub tree expansion, default-disabled policy,
  selected enablement, force-enable, dry-run suppression, and CLI flags.
- `auxiliary` tests cover alias injection, sentinel skipping, alias
  preservation, no-op cases, idempotency, dry-run suppression, profile
  path resolution, and CLI flags.
- `serve_stop` tests cover `/proc/net/tcp` port/inode parsing, inode→PID
  lookup (including self-exclusion and dedup), run() branches (none,
  detected/dry-run, stopped, partial, unsupported), SIGTERM→SIGKILL
  escalation, ProcessLookup/Permission handling, renderer verdicts, and
  CLI --help/--show-resolution/--json. A synthetic `/proc` tree is built
  in tmp_path; `TALARIA_PROC_ROOT` env redirects the proc-fd root.

## Child DOX Index

- `moa_truncation.py` — Signal A (output_tokens trend) + Signal B (length-class
  log markers).
- `refresh_catalog.py` — fetch + reshape the selected gateway catalog into
  the matching Hermes provider manifest cache. Profile-agnostic.
- `context_cache_fix.py` — repair curated known-bad entries in a profile's
  `context_length_cache.yaml` with atomic writes and backups.
- `skill_install.py` — expand recursive skill identifiers and run per-skill
  Hermes installs, then update `skills.disabled` in profile config.
- `auxiliary.py` — derive `model.aliases._<usecase>` from a profile's own
  `auxiliary.<usecase>.model` block. Single-profile; surfaced as
  `talaria config apply-auxiliary`.
- `serve_stop.py` — detect and gracefully stop the Hermes dashboard/serve
  backend by its listening port. Profile-agnostic; Linux-only.