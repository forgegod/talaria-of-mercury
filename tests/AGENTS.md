# tests

## Purpose

Pytest suite for the Talaria CLI and library.

## Ownership

- One test module per feature: `test_paths.py` for path resolution,
  `test_refresh_catalog.py`,
  `test_context_cache_fix.py`, `test_auxiliary.py`, `test_sync.py`,
  and `test_doctor.py` for feature coverage. `test_skill_install.py`
  covers recursive skill install orchestration.
  `test_skill_index.py` covers the read-side skill-index reader
  (filesystem walk + lock.json + `skills.disabled`); shared by the
  `doctor` `skill_index_drift` detector and the
  `talaria skills prune` tool. `test_skill_prune.py` covers the
  write-side reconcile (filesystem-only, lock-only, disabled-orphans
  prune classes, dry-run vs apply, renderer exit codes).
  `test_doctor_aux_models.py` is a live-model benchmark suite
  gated behind `_TESTING_TALARIA_RUN_MODEL_BENCH=1`.
- Shared fixtures live in `conftest.py`; shared test helpers live in
  `_helpers.py` (importable, not auto-discovered by pytest). Vision
  fixture images live in `assets/benchmark/vision/` (see
  `assets/AGENTS.md`).

## Local Contracts

- Test modules must NOT hit the real `~/.hermes/` — every test isolates its
  filesystem via `tmp_path` and its environment via the `clean_env` fixture.
- CLI tests must use `subprocess.run([sys.executable, "-m", "talaria.cli", ...])`
  — invoking the entry point proves the installed CLI surface works, not
  just the library functions.
- `tests/__init__.py` exists so Pyright/pytest can import from `tests._helpers`.
- **Internal test env vars use the `_TESTING_TALARIA_*` prefix.** Any
  environment variable that controls pytest behaviour (opt-in/opt-out
  gates, config overrides for CI, fixture toggles) MUST be named
  `_TESTING_TALARIA_<NAME>`. The leading underscore signals "internal,
  not for production use" and keeps test-only vars out of the README's
  production env-var table. Production env vars (`HERMES_PROFILE`,
  `XDG_CACHE_HOME`, `GITHUB_TOKEN`) never use this prefix — they are
  documented in `README.md` §Environment and consumed by the runtime,
  not by the test suite. Current `_TESTING_TALARIA_*` vars:
  `_TESTING_TALARIA_RUN_MODEL_BENCH`, `_TESTING_TALARIA_SKIP_MODEL_BENCH`,
  `_TESTING_TALARIA_PROFILE_CONFIG`.
- **Silent-by-default.** CLI tests that assert on human-readable stdout
  content must pass `-v/--verbose` explicitly; the default run is exit
  code only. Tests that assert on JSON output, help text, completion
  scripts, or `--show-resolution` output are unaffected because those
  channels always print.

  Carve-outs (default run prints, no `--verbose` needed in tests):

  * `talaria paths` — human-readable default; pass `--json` for the
    JSON channel.
  * `talaria hermes log-rotate` — human-readable default for both
    no-action and action runs.
  * `talaria hermes doctor` — human-readable default; pass
    `-q/--quiet` to assert on the silent (exit-code-only) path.
  * `talaria hermes benchmark` — human-readable default; pass
    `-q/--quiet` to assert on the silent (exit-code-only) path.
  * `talaria completion` and `talaria config sync --list` — the
    output is the answer the test asked for.

## Work Guidance

- New feature: drop a `tests/test_<feature>.py` alongside the existing
  Signal/Run/Renderer/Cli test classes (see `test_refresh_catalog.py`
  for a canonical example).
- Tests assert on real exit codes (`0` / `1`), not just stdout content.
- Use the `fake_hermes_root` fixture for layout, `make_sessions_db` for
  SQLite fixtures, and `_log_line(level, body, when)`-style helpers for
  log fixtures.

## Verification

- `pytest` from the repo root must exit 0 before any change ships.
- Coverage gaps: none tracked.

## Child DOX Index

- `test_paths.py` — `talaria.paths` resolution precedence and the
  `talaria paths` CLI dispatch (default-prints contract).
- `test_refresh_catalog.py` — reshape, credential discovery, cache
  freshness, urllib-stubbed fetch, run() orchestration, renderer, CLI.
- `test_context_cache_fix.py` — curated context-length cache repairs,
  backups, dry-run behaviour, and CLI profile/path resolution.
- `test_auxiliary.py` — single-profile auxiliary-alias derivation:
  injection, sentinel skipping, preservation, no-op, idempotency,
  dry-run, profile path resolution, CLI flags.
- `test_skill_install.py` — recursive skill identifier expansion, install
  policy updates, dry-run behaviour, and CLI flag coverage.
- `test_skill_index.py` — reader for filesystem walk, lock.json, and
  `skills.disabled`: profile resolution, missing/invalid file
  tolerance, and the three drift classes (filesystem-only,
  lock-only, disabled-orphans) plus the combined case and the
  empty-profile path.
- `test_skill_prune.py` — write side of the reconcile: no-op path
  with no prune flags, dry-run vs apply, the three prune classes
  (filesystem-only, lock-only, disabled-orphans), backup-on-write
  behaviour for lock.json + config.yaml, and renderer exit codes
  (0 for no-op / dry-run, 1 for apply-with-action).
- `test_sync.py` — sync phases (config, soul, skills, env,
  context_cache), dot-path helpers, profile resolution, run_sync
  orchestration, CLI surface.
- `test_completion.py` — bash/zsh completion script generation: parser-tree
  introspection, script structure, syntax validation (`bash -n` / `zsh -n`),
  functional bash completion via sourced script, and CLI subprocess.
- `test_log_rotate.py` — rotation parser (`_parse_rotated` for active,
  plain rotated, gz rotated, multi-digit index, README exclusion,
  empty string), classifier (active/rotated/other), active
  rotation (under cap skipped, over cap copies+gzip+truncates,
  second rotation overwrites the first), age-based delete (old
  rotated copies, curator snapshot directories, max-age=0 with
  keep floor), aggregate size prune (oldest-first with keep floor,
  under-cap no-op), keep floor (keep=2 protects two newest, keep=0
  protects nothing), dry-run suppression, multi-profile target
  enumeration, run/render shape, `show_resolution` option echo,
  and CLI `--help`.
- `test_doctor.py` — 12-detector doctor feature: per-detector
  tests (truncation_output, compression_stale_locks, zombie,
  ghost, rewind, cost_anomalies, skill_index_drift, etc.),
  orchestrator selection (`--only`/`--skip`), error isolation,
  renderer, apply-config-suggestions (dry-run, backup,
  parent-block creation, type coercion, missing-file creation),
  config redaction, free-flight pass (zero-log-lines
  short-circuit, finding parsing, stub runner, unavailable
  degradation), and CLI subprocess coverage. CLI subprocess tests
  skip `skill_index_drift` via `--skip` because the detector reads
  `paths.hermes_root` (the operator's live `~/.hermes/`), which is
  not mocked by the `tmp_path` / `--state-db` / `--log-dir` fixture
  shape — coverage for the detector lives in `TestSkillIndexDrift`
  with a hermes-root built under `tmp_path`.
- `test_benchmark.py` — `talaria hermes benchmark` feature: model
  discovery (dedup, sources, alias provider resolution), state.db
  aggregation (group-by-model, window filtering, reasoning config
  extraction, first-response latency CTE), models.dev slug matching
  (nested prefixes, vision detection), cache TTL freshness, smoke
  stub runner (ok/fail/exception), orchestrator run paths
  (no-smoke, cached reuse, stale-trigger), renderer (clean/fail/
  no-models), and CLI subprocess (--help, --show-resolution,
  --json, --quiet, default-prints). Also covers the integrated
  vision benchmark: `_match_vision_response` (basic +
  case-insensitive + `|` alternatives), `_vision_call` stub runner,
  `run()` vision loop (vision model tested / non-vision skipped,
  `vision=False` opt-out, failure recording, TTL caching, missing
  fixture dir degradation), renderer vision lines, and CLI
  `--no-vision` / `--vision-fixtures-dir` flags.
- `test_doctor_aux_models.py` — live-model benchmark suite with
  **deduplicated discovery**. Walks `model.default`, every
  `model.aliases` entry, and every `auxiliary.<usecase>.model` block
  to build a set of unique `(model, provider)` pairs (50 config
  references → 19 unique pairs in the live vc-client profile). Each
  pair is benchmarked once via `hermes chat -q` with a JSON smoke
  prompt. Gated behind `_TESTING_TALARIA_RUN_MODEL_BENCH=1` (opt-in)
  with `_TESTING_TALARIA_SKIP_MODEL_BENCH=1` (opt-out, wins over
  opt-in). Also includes curator parity invariant
  (`xfail(strict=False)`), a live vision smoke test
  (`test_benchmark_vision_live`) that runs the integrated
  `talaria.hermos.benchmark.run` with `vision=True` against the
  live profile, and deterministic config-invariant checks (YAML
  parse, required aliases, non-empty model ids, model.default set,
  discovery finds ≥1 target) that always run. Vision capability is
  no longer tested as a standalone parametrized test — it is part
  of the `talaria hermes benchmark` feature (see `test_benchmark.py`
  for unit coverage).
- `_helpers.py` — `make_sessions_db(path, rows)`,
  `make_full_state_db(path, *, sessions, messages, compression_locks)`.
- `conftest.py` — `fake_hermes_root`, `clean_env` fixtures.