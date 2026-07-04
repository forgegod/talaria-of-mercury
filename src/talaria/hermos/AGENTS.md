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
- `skill_install` is profile-scoped by design — it expands skill identifiers
  (recursive when they end in `/*`), invokes `hermes skills install` for each
  expanded child skill, and updates only that profile's `config.yaml` skill
  enable/disable policy. An optional `--category` forwards to
  `hermes skills install --category` so skills land in
  `skills/<category>/<name>/` instead of the flat root. The category value
  is the literal directory name (e.g. `software-development`), not a display
  name — Hermes' validation regex (`^[a-z][a-z0-9_/-]*$`) rejects uppercase.
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
  validated against Hermes' regex `^[a-z][a-z0-9_/-]*$`. Creating a
  category that already exists is a no-op on the directory; re-writing
  its `DESCRIPTION.md` goes through the atomic backup writer with an
  optional `.bak`. `--dry-run` must not create any directory or file.

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
  Reports include `category` (the forwarded directory-name or empty string).
- `skill_uninstall` reports use `ok: bool`; successfully uninstalled skill
  names are removed from `skills.disabled`. `--dry-run` must not invoke
  Hermes or write `config.yaml`. Partial failures still clean up the skills
  that uninstalled successfully; `ok` is False when any uninstall fails.
- `auxiliary` reports use `ok: bool` and `changed: bool`. Writes go
  through the same atomic backup writer used by sync and
  `context_cache_fix`. `--dry-run` must not write a `config.yaml` or
  backup. Usecases whose `model` is a "no override" sentinel
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
  `^[a-z][a-z0-9_/-]*$`; invalid names raise `SkillCategoryError`.
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
- `context_cache_fix.KNOWN_CONTEXT_FIXES` must stay small and source-backed;
  do not add speculative model windows.
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
  is the must-test edge case.
- `refresh_catalog` tests stub `urllib.request.urlopen` and run the full
  `run()` orchestrator against realistic upstream payloads. No real
  network is used.
- `context_cache_fix` tests cover bad existing entries, missing-key
  insertion, `--only-existing`, dry-run write suppression, and CLI profile
  resolution.
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

- `moa_truncation.py` — Signal A (output_tokens trend) + Signal B (length-class
  log markers).
- `refresh_catalog.py` — fetch + reshape the selected gateway catalog into
  the matching Hermes provider manifest cache. Profile-agnostic.
- `context_cache_fix.py` — repair curated known-bad entries in a profile's
  `context_length_cache.yaml` with atomic writes and backups.
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